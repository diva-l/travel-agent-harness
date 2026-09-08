# -*- coding: utf-8 -*-
"""4-way comparison: base (Qwen3-4B) vs SFT vs RL-150 vs DeepSeek.

Aggregates results_vllm_{base,sft,rl150}.jsonl + results_api.jsonl with the
same metrics as summarize.py and writes compare_report.md.
"""
from __future__ import annotations

import json
from pathlib import Path

import importlib.util
spec = importlib.util.spec_from_file_location(
    "summarize", "/root/autodl-tmp/TravelAgentHarness/eval_results/scripts/summarize.py")
summarize = importlib.util.module_from_spec(spec)
spec.loader.exec_module(summarize)

OUT = Path("/root/autodl-tmp/TravelAgentHarness/eval_results")
DATA = OUT / "data"
SOURCES = {
    "base": ("基座 Qwen3-4B（未微调）", DATA / "results_vllm_base.jsonl"),
    "sft": ("SFT checkpoint-420", DATA / "results_vllm_sft.jsonl"),
    "rl150": ("TravelPlanner-4B（RL 最终）", DATA / "results_vllm_rl150.jsonl"),
    "api": ("DeepSeek（托管参照）", DATA / "results_api.jsonl"),
}


def load(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(l) for l in open(path, encoding="utf-8") if l.strip()]


def main() -> None:
    aggs: dict[str, dict] = {}
    for key, (_, path) in SOURCES.items():
        records = load(path)
        if records:
            aggs[key] = summarize.aggregate(records)

    lines = ["# 训练流水线对比：基座 → SFT → RL，DeepSeek 作参照", ""]
    lines.append("- 同一 10 条样本、同一 tagged 契约、同一真实工具链（高德 + Firecrawl）")
    lines.append("- 基座模型未见过本任务的工具标签协议，其得分即「训练前起点」")
    lines.append("- RL 分数为训练侧六项子 reward 原代码复算（phase3 权重），judge=deepseek-v4-flash")
    lines.append(
        "- 运行环境已对齐训练循环（run_tool_loop_infer.py）：第 12 步强制收尾、"
        "重复循环检测+一次强制作答机会、工具观测截断 5000 字（json2md 头尾）、"
        "visit 页面经 deepseek-v4-flash 按 EXTRACTOR_PROMPT 提炼、火车/航班为"
        "训练同款 LLM 模拟器、search/visit 训练文本格式、固定日期 2026-04-15、"
        "tagged 解析器容错（json_repair/变体/未闭合 answer）与训练原文引导消息"
    )
    lines.append("")
    lines.append("## 总览")
    lines.append("")
    header = "| 指标 |"
    sep = "|---|"
    for key in SOURCES:
        header += f" {SOURCES[key][0]} |"
        sep += "---:|"
    lines.append(header)
    lines.append(sep)
    labels = {
        "completion_rate": "完成率",
        "mean_required_tool_coverage": "必需工具覆盖率",
        "tool_error_rate": "工具错误率",
        "mean_steps": "平均轮次",
        "mean_tool_calls": "平均工具调用",
        "total_repeat_blocks": "重复阻断",
        "total_validation_errors": "Schema 错误",
        "rl_mixed_phase3_mean": "RL 混合分（phase3）",
        "mean_answer_chars": "平均答案长度（字）",
    }
    for key, label in labels.items():
        row = f"| {label} |"
        for src in SOURCES:
            value = aggs.get(src, {}).get(key)
            row += f" {value if value is not None else '-'} |"
        lines.append(row)
    lines.append("")
    lines.append("## RL 子项均分")
    lines.append("")
    lines.append(header.replace(" 指标 ", " 子 reward "))
    lines.append(sep)
    for k in summarize.SUB_KEYS:
        row = f"| {k} |"
        for src in SOURCES:
            value = aggs.get(src, {}).get("rl_sub_means", {}).get(k)
            row += f" {value if value is not None else '-'} |"
        lines.append(row)
    lines.append("")
    lines.append("## 逐条状态")
    lines.append("")
    lines.append("| case |" + "".join(f" {SOURCES[k][0].split('（')[0]} |" for k in SOURCES))
    lines.append("|---|---|---|---|---|")
    case_ids = []
    for records_path in (SOURCES["rl150"][1], SOURCES["sft"][1], SOURCES["base"][1]):
        for r in load(records_path):
            if r["case_id"] not in case_ids:
                case_ids.append(r["case_id"])
    maps = {k: {r["case_id"]: r for r in load(p)} for k, (_, p) in SOURCES.items()}
    for cid in case_ids:
        row = f"| {cid[:8]} |"
        for src in SOURCES:
            r = maps[src].get(cid)
            if not r or r.get("runner_error"):
                row += " - |"
            else:
                row += f" {r['harness']['status']} / {r['rl']['mixed_reward_phase3']} |"
        lines.append(row)
    lines.append("")
    lines.append("> n=10 抽样 + 实时外部数据 + judge 方差：关注趋势（base→SFT→RL 的单调性），勿读小数点。")
    (OUT / "compare_report.md").write_text("\n".join(lines), encoding="utf-8")
    print("\n".join(lines[:30]))


if __name__ == "__main__":
    main()
