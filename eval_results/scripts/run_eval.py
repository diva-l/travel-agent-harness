# -*- coding: utf-8 -*-
"""Offline evaluation: harness-level scoring for the vLLM planner vs the
DeepSeek baseline, with a pluggable reward-scorer interface on top.

Scoring is layered:

- Harness metrics (status, tool coverage, steps, tokens, elapsed) are always
  computed — they only need this repository.
- The trajectory score comes from a *scorer*: any Python file exposing
  `score(messages: list[dict]) -> float` can be plugged in via `--scorer`.
  See example_scorer.py for the interface.
- The numbers in report.md were produced with the training-side reward
  plugin (not shipped in this repo); when it is unavailable, the run still
  produces all harness metrics and simply skips the trajectory score.

Usage:
    python run_eval.py --mode vllm|api [--limit 10] [--scorer my_scorer.py]
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import re
import sys
import time
import types
from pathlib import Path

HARNESS_ROOT = Path("/root/autodl-tmp/TravelAgentHarness")
REWARD_PLUGIN = Path(
    "/root/autodl-tmp/travel_agentic_rl/ms-swift/examples/train/grpo/plugin/tooluse_reward_parser_aligned.py"
)
TEST_SET = HARNESS_ROOT / "eval_results" / "data" / "test_final.jsonl"
OUT_DIR = HARNESS_ROOT / "eval_results" / "data"

sys.path.insert(0, str(HARNESS_ROOT / "src"))

from travel_agent_harness.config import HarnessConfig, load_env_file  # noqa: E402
from travel_agent_harness.harness import build_default_harness  # noqa: E402
from travel_agent_harness.prompts import build_planner_system_prompt  # noqa: E402


# ---------------------------------------------------------------------------
# Case selection (deterministic: sort by id, every 8th of 80)
# ---------------------------------------------------------------------------

def load_cases(limit: int = 10) -> list[dict]:
    rows = [json.loads(l) for l in open(TEST_SET, encoding="utf-8") if l.strip()]
    rows.sort(key=lambda r: r["id"])
    step = max(1, len(rows) // limit)
    return [rows[i] for i in range(0, len(rows), step)][:limit]


def fingerprint(cases: list[dict]) -> str:
    canon = lambda v: json.dumps(v, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    h = hashlib.sha256()
    for r in sorted(cases, key=lambda x: x["id"]):
        h.update(canon(r).encode())
        h.update(b"\n")
    return h.hexdigest()


def case_query(case: dict) -> str:
    return next(m["content"] for m in case["conversations"] if m["role"] == "user").strip()


def case_gold_answer(case: dict) -> str:
    for m in reversed(case["conversations"]):
        if m["role"] == "assistant":
            match = re.findall(r"<answer>\s*(.*?)\s*</answer>", m["content"], re.DOTALL)
            if match:
                return match[-1].strip()
    return ""


def case_gold_tools(case: dict) -> list[str]:
    text = " ".join(m["content"] for m in case["conversations"] if m["role"] == "assistant")
    return sorted(set(re.findall(r"[\"']name[\"']:\s*[\"'](\w+)[\"']", text)))


# ---------------------------------------------------------------------------
# RL reward plugin (training code, loaded with a stubbed swift.rewards)
# ---------------------------------------------------------------------------

def load_reward_plugin(gold_path: Path):
    swift = types.ModuleType("swift")
    rewards_mod = types.ModuleType("swift.rewards")

    class ORM:
        def __init__(self, args=None, **kwargs):
            pass

    rewards_mod.ORM = ORM
    rewards_mod.orms = {}
    swift.rewards = rewards_mod
    sys.modules.setdefault("swift", swift)
    sys.modules["swift.rewards"] = rewards_mod

    os.environ["ANSWER_JUDGE_GOLD_DATASET_PATH"] = str(gold_path)
    spec = importlib.util.spec_from_file_location("tooluse_reward_parser_aligned", REWARD_PLUGIN)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def load_user_scorer(path: Path):
    """Load a user-provided scorer module exposing score(messages) -> float."""
    spec = importlib.util.spec_from_file_location("user_scorer", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    if not callable(getattr(module, "score", None)):
        raise TypeError(f"{path} must define a callable score(messages)")
    return module.score


# ---------------------------------------------------------------------------
# Trajectory -> reward-context messages
# ---------------------------------------------------------------------------

def turns_from_state(state, tools_schema: list[dict]) -> list[dict]:
    """Convert harness messages into synthetic training-format messages.

    Only system/user/assistant roles matter to the reward contexts. Native
    (DeepSeek) assistant turns are rendered as tagged text so the same parser
    metrics apply to both protocols.
    """
    system_prompt = build_planner_system_prompt(
        protocol="tagged", max_tool_rounds=13, tools=tools_schema
    )
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": state.objective},
    ]
    for m in state.messages[2:]:
        if m["role"] == "assistant":
            if m.get("tool_calls"):
                blocks = []
                for c in m["tool_calls"]:
                    arguments = c["function"].get("arguments") or "{}"
                    if isinstance(arguments, str):
                        try:
                            arguments = json.loads(arguments)
                        except json.JSONDecodeError:
                            pass
                    blocks.append(
                        '<tool_call>{"name": %s, "arguments": %s}</tool_call>'
                        % (json.dumps(c["function"]["name"], ensure_ascii=False),
                           json.dumps(arguments, ensure_ascii=False))
                    )
                messages.append({"role": "assistant", "content": "".join(blocks)})
            else:
                content = m.get("content") or ""
                if "<answer>" not in content and "<tool_call>" not in content:
                    content = f"<answer>{content}</answer>"
                messages.append({"role": "assistant", "content": content})
    return messages


def score_rl(plugin, judge, messages: list[dict]) -> dict:
    kwargs = {"completions": [""], "prompts": [""], "messages": [messages]}
    ctx = plugin._get_reward_contexts(**kwargs)[0]
    sub = {
        "process_step": plugin.ParserAlignedProcessReward()(**kwargs)[0],
        "tool_schema": plugin.ParserAlignedToolSchemaReward()(**kwargs)[0],
        "answer_tag": plugin.ParserAlignedAnswerTagReward()(**kwargs)[0],
        "stage_aware": plugin.ParserAlignedStageReward()(**kwargs)[0],
        "tool_efficiency": plugin.ParserAlignedToolEfficiencyReward()(**kwargs)[0],
        "llm_judge": judge(**kwargs)[0] if judge is not None else None,
    }
    weights = {"process_step": 0.05, "tool_schema": 0.07, "answer_tag": 0.03,
               "stage_aware": 0.05, "tool_efficiency": 0.10, "llm_judge": 0.70}
    mixed = sum(w * (sub[k] or 0.0) for k, w in weights.items())
    return {
        "sub_rewards": sub,
        "mixed_reward_phase3": round(max(-1.0, min(1.0, mixed)), 4),
        "n_assistant_turns": len(ctx["assistant_turns"]),
        "intermediate_summary": {k: v for k, v in ctx["summary"].items() if k != "signatures"},
    }


# ---------------------------------------------------------------------------
# Harness-level metrics from trace
# ---------------------------------------------------------------------------

def harness_metrics(harness, state) -> dict:
    traces = harness.runtime.store.trace(state.task_id)
    counts = {}
    for event in traces:
        counts[event["kind"]] = counts.get(event["kind"], 0) + 1
    return {
        "status": state.status.value,
        "error": state.error,
        "steps": state.step,
        "tool_calls": state.tool_calls,
        "successful_tool_calls": state.successful_tool_calls,
        "tool_errors": state.tool_errors,
        "validation_errors": state.validation_errors,
        "repeat_blocks": counts.get("tool_blocked", 0),
        "final_rejected": counts.get("final_rejected", 0),
        "model_retries_hit": counts.get("model_error", 0),
        "total_tokens": state.total_tokens,
        "elapsed_seconds": round(state.elapsed_seconds, 2),
        "checkpoints": len(harness.runtime.store.list_checkpoints(state.task_id)),
        "budget_utilization": {
            "steps": round(state.step / harness.runtime.config.max_steps, 3),
            "tokens": round(state.total_tokens / harness.runtime.config.max_total_tokens, 3),
            "seconds": round(state.elapsed_seconds / harness.runtime.config.max_seconds, 3),
        },
    }


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=["vllm", "api"], required=True)
    parser.add_argument("--limit", type=int, default=10)
    parser.add_argument("--scorer", help="Python file exposing score(messages) -> float")
    parser.add_argument("--skip-judge", action="store_true")
    args = parser.parse_args()

    OUT_DIR.mkdir(exist_ok=True)
    load_env_file(HARNESS_ROOT / ".env")

    planner_mode = args.mode
    overrides = {
        "planner_mode": planner_mode,
        "db_path": Path(f"/root/autodl-tmp/eval-{planner_mode}.db"),
        "report_enabled": False,  # eval scores the planner; report stage is separate
    }
    if planner_mode == "api":
        # DeepSeek baseline: same tool chain, budgets aligned with vllm preset
        overrides["max_total_tokens"] = 80000
        overrides["max_seconds"] = 600.0
    config = HarnessConfig.from_env(**overrides)
    harness = build_default_harness(config)

    cases = load_cases(args.limit)
    fp = fingerprint(cases)
    gold_path = OUT_DIR / "gold_selected.jsonl"
    with open(gold_path, "w", encoding="utf-8") as f:
        for case in cases:
            f.write(json.dumps(case, ensure_ascii=False) + "\n")

    # Scorer selection: --scorer (user implementation) > training-side plugin
    # (internal, not shipped) > None (harness metrics only).
    scorer = None
    scorer_name = "none"
    if args.scorer:
        user_score = load_user_scorer(Path(args.scorer))
        scorer_name = f"user:{Path(args.scorer).name}"

        def scorer(messages, _score=user_score):
            return {"mixed_reward_phase3": round(float(_score(messages)), 4)}

    elif REWARD_PLUGIN.is_file():
        plugin = load_reward_plugin(gold_path)
        judge = None
        if not args.skip_judge:
            os.environ["JUDGE_API_KEY"] = config.report_api_key or config.api_key
            os.environ["JUDGE_BASE_URL"] = "https://api.deepseek.com"
            os.environ["JUDGE_MODEL"] = "deepseek-v4-flash"
            judge = plugin.ParserAlignedAnswerLLMJudgeReward()
        scorer_name = "training-side plugin"

        def scorer(messages, _plugin=plugin, _judge=judge):
            return score_rl(_plugin, _judge, messages)

    else:
        print("[eval] no reward scorer available (training-side scorer is not "
              "shipped; pass --scorer your_scorer.py to plug in your own) — "
              "harness metrics only")

    tools_schema = harness.runtime.tools.api_schemas()
    results_path = OUT_DIR / f"results_{planner_mode}.jsonl"
    with open(results_path, "w", encoding="utf-8") as out:
        for index, case in enumerate(cases, 1):
            query = case_query(case)
            gold_tools = case_gold_tools(case)
            print(f"[{planner_mode} {index}/{len(cases)}] {query[:40]}...", flush=True)
            started = time.monotonic()
            try:
                state = harness.run(query)
            except Exception as exc:  # keep the batch going
                record = {"case_id": case["id"], "query": query, "runner_error": str(exc)}
                out.write(json.dumps(record, ensure_ascii=False) + "\n")
                out.flush()
                continue
            hm = harness_metrics(harness, state)
            messages = turns_from_state(state, tools_schema)
            rl = scorer(messages) if scorer else None
            model_tools = sorted({
                c["function"]["name"] if "function" in c else c.get("name")
                for m in state.messages if m["role"] == "assistant"
                for c in (m.get("tool_calls") or [])
            } | {
                name for name in re.findall(
                    r"<tool_call>\s*\{[^}]*?[\"']name[\"']\s*:\s*[\"'](\w+)[\"']",
                    " ".join(m.get("content") or "" for m in state.messages if m["role"] == "assistant"),
                )
            })
            coverage = (
                len(set(gold_tools) & set(model_tools)) / len(gold_tools) if gold_tools else None
            )
            record = {
                "case_id": case["id"],
                "query": query,
                "gold_tools": gold_tools,
                "model_tools": model_tools,
                "required_tool_coverage": coverage,
                "answer_chars": len(state.answer or ""),
                "answer": state.answer,
                "harness": hm,
                "rl": rl,
                "wall_seconds": round(time.monotonic() - started, 2),
            }
            out.write(json.dumps(record, ensure_ascii=False) + "\n")
            out.flush()
            mixed = rl["mixed_reward_phase3"] if rl else "-"
            print(f"  -> {hm['status']} steps={hm['steps']} tools={hm['tool_calls']} "
                  f"tokens={hm['total_tokens']} mixed={mixed}", flush=True)

    print(json.dumps({"mode": planner_mode, "dataset_fingerprint": fp,
                      "scorer": scorer_name,
                      "results": str(results_path)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
