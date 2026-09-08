# -*- coding: utf-8 -*-
"""Aggregate eval_results/results_{vllm,api}.jsonl into summary.json + report.md."""
from __future__ import annotations

import json
from pathlib import Path

OUT = Path(__file__).resolve().parents[1]
SUB_KEYS = ["process_step", "tool_schema", "answer_tag", "stage_aware", "tool_efficiency", "llm_judge"]
FILES = {"vllm": "results_vllm_rl150.jsonl", "api": "results_api.jsonl"}


def load(mode: str) -> list[dict]:
    return [json.loads(l) for l in open(OUT / "data" / FILES[mode], encoding="utf-8") if l.strip()]


def mean(values):
    vals = [v for v in values if v is not None]
    return round(sum(vals) / len(vals), 4) if vals else None


def aggregate(records: list[dict]) -> dict:
    n = len(records)
    ok = [r for r in records if not r.get("runner_error")]
    scored = [r for r in ok if r.get("rl")]
    return {
        "cases": n,
        "runner_errors": n - len(ok),
        "completion_rate": round(sum(1 for r in ok if r["harness"]["status"] == "completed") / max(1, len(ok)), 4),
        "mean_required_tool_coverage": mean([r["required_tool_coverage"] for r in ok]),
        "tool_error_rate": round(
            sum(r["harness"]["tool_errors"] for r in ok) / max(1, sum(r["harness"]["tool_calls"] for r in ok)), 4),
        "mean_steps": mean([r["harness"]["steps"] for r in ok]),
        "mean_tool_calls": mean([r["harness"]["tool_calls"] for r in ok]),
        "mean_tokens": mean([r["harness"]["total_tokens"] for r in ok]),
        "mean_elapsed_seconds": mean([r["harness"]["elapsed_seconds"] for r in ok]),
        "total_repeat_blocks": sum(r["harness"]["repeat_blocks"] for r in ok),
        "total_validation_errors": sum(r["harness"]["validation_errors"] for r in ok),
        "rl_sub_means": {k: mean([r["rl"].get("sub_rewards", {}).get(k) for r in scored]) for k in SUB_KEYS},
        "rl_mixed_phase3_mean": mean([r["rl"]["mixed_reward_phase3"] for r in scored]),
        "mean_answer_chars": mean([r["answer_chars"] for r in ok]),
    }


def main() -> None:
    summary = {}
    for mode in ("vllm", "api"):
        records = load(mode)
        agg = aggregate(records)
        # Do not publish the reward decomposition: keep the mixed score and
        # the judge mean only.
        agg["llm_judge_mean"] = agg.pop("rl_sub_means")["llm_judge"]
        agg["rl_mixed_mean"] = agg.pop("rl_mixed_phase3_mean")
        summary[mode] = agg
    (OUT / "data" / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    lines = ["# 评测报告：Voyager-4B vs DeepSeek（同一 Harness、同一真实工具链）", ""]
    lines.append("- 测试集：`data/test_final.jsonl` 10 条用例（精选自训练侧 80 条测试集）")
    lines.append("- 工具链：高德 Web 服务（真实） + Firecrawl（真实检索）")
    lines.append("- 分数：训练侧过程奖励混合分（训练同款评测代码复算）+ LLM judge（deepseek-v4-flash，对照测试集 gold answer）")
    lines.append("")
    lines.append("## 总览")
    lines.append("")
    lines.append("| 指标 | Voyager-4B (vLLM) | DeepSeek (api) |")
    lines.append("|---|---:|---:|")
    labels = {
        "completion_rate": "完成率",
        "mean_required_tool_coverage": "必需工具覆盖率",
        "tool_error_rate": "工具错误率",
        "mean_steps": "平均模型轮次",
        "mean_tool_calls": "平均工具调用数",
        "mean_tokens": "平均 Token（累计）",
        "mean_elapsed_seconds": "平均耗时（秒）",
        "total_repeat_blocks": "重复调用阻断次数",
        "total_validation_errors": "Schema 校验错误数",
        "rl_mixed_mean": "过程奖励混合分",
        "llm_judge_mean": "LLM judge",
        "mean_answer_chars": "平均答案长度（字）",
    }
    for key, label in labels.items():
        fmt = lambda v: v if v is not None else "-"
        lines.append(f"| {label} | {fmt(summary['vllm'][key])} | {fmt(summary['api'][key])} |")
    lines.append("")
    lines.append("## 逐条明细")
    lines.append("")
    lines.append("| case | query | Voyager-4B 状态/混合分 | DeepSeek 状态/混合分 |")
    lines.append("|---|---|---|---|")
    vllm_map = {r["case_id"]: r for r in load("vllm")}
    api_map = {r["case_id"]: r for r in load("api")}
    for case_id, rv in vllm_map.items():
        ra = api_map.get(case_id, {})
        q = rv["query"][:24].replace("|", "/")
        rv_mixed = rv["rl"]["mixed_reward_phase3"] if rv.get("rl") else "-"
        rv_s = f"{rv['harness']['status']} / {rv_mixed}"
        ra_mixed = ra["rl"]["mixed_reward_phase3"] if ra.get("rl") else "-"
        ra_s = f"{ra.get('harness', {}).get('status', '-')} / {ra_mixed}"
        lines.append(f"| {case_id[:8]} | {q} | {rv_s} | {ra_s} |")
    lines.append("")
    lines.append("> 注意：n=10 抽样，RL 分数与 judge 均有方差；单次对比不构成总体优劣结论。")
    (OUT / "report.md").write_text("\n".join(lines), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
