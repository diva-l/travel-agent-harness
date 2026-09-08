# -*- coding: utf-8 -*-
"""Aggregate eval_results/results_{vllm,api}.jsonl into summary.json + report.md."""
from __future__ import annotations

import json
from pathlib import Path

OUT = Path("/root/autodl-tmp/TravelAgentHarness/eval_results")
SUB_KEYS = ["process_step", "tool_schema", "answer_tag", "stage_aware", "tool_efficiency", "llm_judge"]


def load(mode: str) -> list[dict]:
    return [json.loads(l) for l in open(OUT / "data" / f"results_{mode}.jsonl", encoding="utf-8") if l.strip()]


def mean(values):
    vals = [v for v in values if v is not None]
    return round(sum(vals) / len(vals), 4) if vals else None


def aggregate(records: list[dict]) -> dict:
    n = len(records)
    ok = [r for r in records if not r.get("runner_error")]
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
        "rl_sub_means": {k: mean([r["rl"]["sub_rewards"][k] for r in ok]) for k in SUB_KEYS},
        "rl_mixed_phase3_mean": mean([r["rl"]["mixed_reward_phase3"] for r in ok]),
        "mean_answer_chars": mean([r["answer_chars"] for r in ok]),
    }


def main() -> None:
    summary = {}
    for mode in ("vllm", "api"):
        records = load(mode)
        summary[mode] = aggregate(records)
    (OUT / "data" / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    lines = ["# 评测报告：Voyager-4B (vLLM) vs DeepSeek 基线", ""]
    lines.append("- 测试集：`test_final.jsonl` 确定性抽样 10/80（按 id 排序每隔 8 条）")
    lines.append("- 数据集指纹（sha256）：`b7d3c735c13f92328aa0bf246d0649e4a1f8cfac6fcc187281775622e0ef4303`")
    lines.append("- 工具链：高德 Web 服务（真实） + Firecrawl（真实检索）")
    lines.append("- RL 分数：训练侧 parser-aligned 六项子 reward 原代码复算，课程第 3 阶段权重 "
                 "[process 0.05, schema 0.07, answer_tag 0.03, stage 0.05, efficiency 0.10, llm_judge 0.70]")
    lines.append("- LLM judge：deepseek-v4-flash，对照测试集 gold answer（judge 提示词与训练侧一致）")
    lines.append("")
    lines.append("## 总览")
    lines.append("")
    lines.append("| 指标 | vLLM (Voyager-4B) | DeepSeek (api) |")
    lines.append("|---|---:|---:|")
    labels = {
        "completion_rate": "完成率",
        "mean_required_tool_coverage": "必需工具覆盖率",
        "tool_error_rate": "工具错误率",
        "mean_steps": "平均模型轮次",
        "mean_tool_calls": "平均工具调用数",
        "mean_tokens": "平均 Token",
        "mean_elapsed_seconds": "平均耗时（秒）",
        "total_repeat_blocks": "重复调用阻断次数",
        "total_validation_errors": "Schema 校验错误数",
        "rl_mixed_phase3_mean": "RL 混合分（phase3 权重）",
        "mean_answer_chars": "平均答案长度（字）",
    }
    for key, label in labels.items():
        lines.append(f"| {label} | {summary['vllm'][key]} | {summary['api'][key]} |")
    lines.append("")
    lines.append("## RL 子项均分")
    lines.append("")
    lines.append("| 子 reward | vLLM | DeepSeek |")
    lines.append("|---|---:|---:|")
    for k in SUB_KEYS:
        lines.append(f"| {k} | {summary['vllm']['rl_sub_means'][k]} | {summary['api']['rl_sub_means'][k]} |")
    lines.append("")
    lines.append("## 逐条明细")
    lines.append("")
    lines.append("| case | query | vLLM 状态/混合分 | DeepSeek 状态/混合分 |")
    lines.append("|---|---|---|---|")
    vllm_map = {r["case_id"]: r for r in load("vllm")}
    api_map = {r["case_id"]: r for r in load("api")}
    for case_id, rv in vllm_map.items():
        ra = api_map.get(case_id, {})
        q = rv["query"][:24].replace("|", "/")
        rv_s = f"{rv['harness']['status']} / {rv['rl']['mixed_reward_phase3']}"
        ra_s = f"{ra.get('harness', {}).get('status', '-')} / {ra.get('rl', {}).get('mixed_reward_phase3', '-')}"
        lines.append(f"| {case_id[:8]} | {q} | {rv_s} | {ra_s} |")
    lines.append("")
    lines.append("> 注意：n=10 抽样，RL 分数与 judge 均有方差；单次对比不构成总体优劣结论。")
    (OUT / "report.md").write_text("\n".join(lines), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
