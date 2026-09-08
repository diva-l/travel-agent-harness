# -*- coding: utf-8 -*-
"""Harness load test (vLLM mode): concurrency sweep + bare-vLLM baseline.

Tiers:
  seq-c1  concurrency 1, parallel_tool_calls=False  (fully sequential baseline)
  par-c1  concurrency 1, parallel_tool_calls=True   (intra-turn parallel gain)
  par-c2 / par-c4 / par-c8                           (cross-task scaling)
  bare-c8 direct vLLM first-round requests, no harness (model-only ceiling)

Per tier: wall time, tasks/hour, latency p50/p95/max, completion, harness
interventions (validation/repeat/budget/retry), and the model/tool/harness
time breakdown from trace duration_ms. GPU is sampled every 2s throughout.
"""
from __future__ import annotations

import json
import sqlite3
import statistics
import subprocess
import threading
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import sys

HARNESS_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(HARNESS_ROOT / "src"))

from travel_agent_harness.config import HarnessConfig, load_env_file  # noqa: E402
from travel_agent_harness.harness import build_default_harness  # noqa: E402
from travel_agent_harness.prompts import build_planner_system_prompt  # noqa: E402

OUT = Path(__file__).resolve().parents[1] / "data"
OUT.mkdir(parents=True, exist_ok=True)
VLLM = "http://127.0.0.1:8000"

TIERS = [
    {"name": "seq-c1", "concurrency": 1, "parallel": False},
    {"name": "par-c1", "concurrency": 1, "parallel": True},
    {"name": "par-c2", "concurrency": 2, "parallel": True},
    {"name": "par-c4", "concurrency": 4, "parallel": True},
    {"name": "par-c8", "concurrency": 8, "parallel": True},
]

_gpu_samples: list[dict] = []
_gpu_stop = threading.Event()
_current_tier = {"name": "idle"}


def gpu_sampler() -> None:
    while not _gpu_stop.is_set():
        try:
            out = subprocess.run(
                ["nvidia-smi", "--query-gpu=utilization.gpu,memory.used,power.draw",
                 "--format=csv,noheader,nounits"],
                capture_output=True, text=True, timeout=5,
            ).stdout.strip()
            util, mem, power = [float(x.strip()) for x in out.split(",")]
            _gpu_samples.append({
                "ts": time.time(), "tier": _current_tier["name"],
                "gpu_util": util, "mem_mib": mem, "power_w": power,
            })
        except Exception:
            pass
        _gpu_stop.wait(2.0)


def vllm_metrics() -> dict[str, float]:
    try:
        with urllib.request.urlopen(f"{VLLM}/metrics", timeout=10) as resp:
            text = resp.read().decode()
    except Exception:
        return {}
    wanted = ("vllm:prompt_tokens_total", "vllm:generation_tokens_total",
              "vllm:request_success_total")
    result: dict[str, float] = {}
    for line in text.splitlines():
        if line.startswith("#"):
            continue
        for name in wanted:
            if line.startswith(name):
                key = name.replace("vllm:", "").replace("_total", "")
                result[key] = result.get(key, 0.0) + float(line.rsplit(" ", 1)[1])
        if line.startswith("vllm:gpu_prefix_cache_queries") or line.startswith("vllm:gpu_prefix_cache_hits"):
            key = "prefix_" + line.split("{")[0].replace("vllm:gpu_", "").replace("_total", "")
            result[key] = result.get(key, 0.0) + float(line.rsplit(" ", 1)[1])
    return result


def load_cases() -> list[dict]:
    rows = [json.loads(l) for l in open(HARNESS_ROOT / "eval_results" / "data" / "test_final.jsonl", encoding="utf-8") if l.strip()]
    rows.sort(key=lambda r: r["id"])
    return [rows[i] for i in range(0, len(rows), 8)][:10]


def case_query(case: dict) -> str:
    return next(m["content"] for m in case["conversations"] if m["role"] == "user").strip()


def time_breakdown(db_path: Path) -> dict[str, float]:
    """Sum model/tool durations from traces; harness overhead = wall - model - tool."""
    db = sqlite3.connect(db_path)
    model_ms = tool_ms = 0.0
    for (kind, payload) in db.execute("select kind, payload_json from traces"):
        d = json.loads(payload)
        if kind == "model_response":
            model_ms += d.get("duration_ms") or 0.0
        elif kind in ("tool_succeeded", "tool_failed"):
            tool_ms += d.get("duration_ms") or 0.0
    db.close()
    return {"model_ms": model_ms, "tool_ms": tool_ms}


def run_tier(tier: dict, cases: list[dict]) -> dict:
    db_path = OUT / f"eval-perf-{tier['name']}.db"
    db_path.unlink(missing_ok=True)
    config = HarnessConfig.from_env(
        planner_mode="vllm",
        db_path=db_path,
        report_enabled=False,
        parallel_tool_calls=tier["parallel"],
        request_timeout_seconds=300.0,
    )
    harness = build_default_harness(config)

    per_task: list[dict] = []
    lock = threading.Lock()

    def work(case: dict) -> None:
        started = time.monotonic()
        try:
            state = harness.run(case_query(case))
            rec = {
                "case_id": case["id"], "status": state.status.value,
                "steps": state.step, "tool_calls": state.tool_calls,
                "tool_errors": state.tool_errors, "validation_errors": state.validation_errors,
                "total_tokens": state.total_tokens,
                "wall_seconds": round(time.monotonic() - started, 2),
            }
        except Exception as exc:  # keep the tier going
            rec = {"case_id": case["id"], "status": "runner_error", "error": str(exc),
                   "wall_seconds": round(time.monotonic() - started, 2)}
        with lock:
            per_task.append(rec)

    metrics_before = vllm_metrics()
    tier_started = time.monotonic()
    with ThreadPoolExecutor(max_workers=tier["concurrency"]) as pool:
        list(pool.map(work, cases))
    wall = time.monotonic() - tier_started
    metrics_after = vllm_metrics()

    breakdown = time_breakdown(db_path)
    walls = sorted(r["wall_seconds"] for r in per_task)
    counts: dict[str, int] = {}
    for r in per_task:
        counts[r["status"]] = counts.get(r["status"], 0) + 1
    prompt_delta = metrics_after.get("prompt_tokens", 0) - metrics_before.get("prompt_tokens", 0)
    gen_delta = metrics_after.get("generation_tokens", 0) - metrics_before.get("generation_tokens", 0)
    return {
        "tier": tier["name"], "concurrency": tier["concurrency"],
        "parallel_tool_calls": tier["parallel"],
        "wall_seconds": round(wall, 2),
        "tasks_per_hour": round(len(per_task) / wall * 3600, 1),
        "latency_p50": statistics.median(walls),
        "latency_p95": walls[min(len(walls) - 1, int(len(walls) * 0.95))],
        "latency_max": max(walls),
        "status_counts": counts,
        "total_tool_calls": sum(r.get("tool_calls", 0) for r in per_task),
        "total_tool_errors": sum(r.get("tool_errors", 0) for r in per_task),
        "total_validation_errors": sum(r.get("validation_errors", 0) for r in per_task),
        "model_time_s": round(breakdown["model_ms"] / 1000, 1),
        "tool_time_s": round(breakdown["tool_ms"] / 1000, 1),
        "vllm_prompt_tokens": prompt_delta,
        "vllm_generation_tokens": gen_delta,
        "vllm_agg_tokens_per_s": round((prompt_delta + gen_delta) / wall, 1) if wall else None,
        "per_task": per_task,
    }


def run_bare(cases: list[dict], concurrency: int = 8) -> dict:
    """First-round requests straight to vLLM — model-only ceiling, no harness."""
    tools_schema = build_default_harness(
        HarnessConfig.from_env(planner_mode="vllm", db_path=OUT / "eval-perf-bare.db",
                               report_enabled=False)
    ).runtime.tools.api_schemas()
    system_prompt = build_planner_system_prompt(protocol="tagged", max_tool_rounds=13, tools=tools_schema)
    (OUT / "eval-perf-bare.db").unlink(missing_ok=True)

    def work(case: dict) -> dict:
        payload = {
            "model": "travel-planner",
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": case_query(case)},
            ],
            "temperature": 0.2, "top_p": 0.95, "top_k": 50, "max_tokens": 5000,
        }
        req = urllib.request.Request(
            f"{VLLM}/v1/chat/completions",
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={"Content-Type": "application/json"}, method="POST",
        )
        started = time.monotonic()
        try:
            with urllib.request.urlopen(req, timeout=300) as resp:
                body = json.loads(resp.read().decode())
            usage = body.get("usage") or {}
            return {"case_id": case["id"], "wall_seconds": round(time.monotonic() - started, 2),
                    "prompt_tokens": usage.get("prompt_tokens"), "completion_tokens": usage.get("completion_tokens")}
        except Exception as exc:
            return {"case_id": case["id"], "error": str(exc),
                    "wall_seconds": round(time.monotonic() - started, 2)}

    metrics_before = vllm_metrics()
    started = time.monotonic()
    with ThreadPoolExecutor(max_workers=concurrency) as pool:
        per_task = list(pool.map(work, cases))
    wall = time.monotonic() - started
    metrics_after = vllm_metrics()
    walls = sorted(r["wall_seconds"] for r in per_task)
    prompt_delta = metrics_after.get("prompt_tokens", 0) - metrics_before.get("prompt_tokens", 0)
    gen_delta = metrics_after.get("generation_tokens", 0) - metrics_before.get("generation_tokens", 0)
    return {
        "tier": "bare-c8", "concurrency": concurrency, "wall_seconds": round(wall, 2),
        "requests_per_hour": round(len(per_task) / wall * 3600, 1),
        "latency_p50": statistics.median(walls), "latency_max": max(walls),
        "errors": sum(1 for r in per_task if "error" in r),
        "vllm_prompt_tokens": prompt_delta, "vllm_generation_tokens": gen_delta,
        "vllm_agg_tokens_per_s": round((prompt_delta + gen_delta) / wall, 1) if wall else None,
        "per_task": per_task,
    }


def main() -> None:
    load_env_file(HARNESS_ROOT / ".env")
    cases = load_cases()
    print(f"load test: {len(cases)} cases x {len(TIERS)} tiers + bare-c8", flush=True)

    sampler = threading.Thread(target=gpu_sampler, daemon=True)
    sampler.start()

    results: list[dict] = []
    for tier in TIERS:
        _current_tier["name"] = tier["name"]
        print(f"[{tier['name']}] running...", flush=True)
        result = run_tier(tier, cases)
        results.append(result)
        print(f"  -> wall={result['wall_seconds']}s tasks/h={result['tasks_per_hour']} "
              f"p50={result['latency_p50']}s status={result['status_counts']}", flush=True)

    _current_tier["name"] = "bare-c8"
    print("[bare-c8] running...", flush=True)
    bare = run_bare(cases)
    results.append(bare)
    print(f"  -> wall={bare['wall_seconds']}s req/h={bare['requests_per_hour']} "
          f"p50={bare['latency_p50']}s errors={bare['errors']}", flush=True)

    _current_tier["name"] = "idle"
    _gpu_stop.set()
    sampler.join(timeout=5)

    (OUT / "load_vllm.json").write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    with open(OUT / "gpu_samples.jsonl", "w", encoding="utf-8") as f:
        for sample in _gpu_samples:
            f.write(json.dumps(sample) + "\n")
    print(f"saved: {OUT}/load_vllm.json, gpu_samples.jsonl ({len(_gpu_samples)} samples)")


if __name__ == "__main__":
    main()
