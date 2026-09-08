# -*- coding: utf-8 -*-
"""Part 1: quantify harness value from existing eval traces (no new runs).

Counts every harness intervention — schema validation rejections, repeat-call
blocks, budget enforcements, absorbed model errors, evidence-gate rejections —
and estimates what would have happened without the harness layer.
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

OUT = Path("/root/autodl-tmp/TravelAgentHarness/eval_results/perf")
OUT.mkdir(parents=True, exist_ok=True)


def stats_for(db_path: str) -> dict:
    db = sqlite3.connect(db_path)
    rows = db.execute("select task_id, kind, payload_json from traces").fetchall()
    db.close()
    per_task: dict[str, dict[str, int]] = {}
    totals: dict[str, int] = {}
    for task_id, kind, payload in rows:
        per_task.setdefault(task_id, {})[kind] = per_task.setdefault(task_id, {}).get(kind, 0) + 1
        totals[kind] = totals.get(kind, 0) + 1

    n_tasks = len(per_task)
    interventions = {
        "repeat_call_blocks": totals.get("tool_blocked", 0),
        "validation_rejections": 0,   # tool_failed events that are schema/validation errors
        "budget_enforcements": totals.get("budget_exhausted", 0),
        "model_retries_absorbed": totals.get("model_error", 0),
        "evidence_gate_rejections": totals.get("final_rejected", 0),
        "tool_runtime_failures": 0,   # provider-side errors the harness surfaced as observations
    }
    for _, kind, payload in rows:
        if kind == "tool_failed":
            error = json.loads(payload).get("error", "")
            if any(k in error for k in ("must be", "missing required", "unexpected fields", "not found",
                                        "too many items", "too few items", "below minimum", "above maximum",
                                        "one of")):
                interventions["validation_rejections"] += 1
            else:
                interventions["tool_runtime_failures"] += 1

    tasks_with_intervention = sum(
        1 for kinds in per_task.values()
        if kinds.get("tool_blocked") or kinds.get("budget_exhausted")
        or kinds.get("final_rejected") or kinds.get("model_error") or kinds.get("tool_failed")
    )
    total_interventions = sum(interventions.values())
    return {
        "tasks": n_tasks,
        "interventions": interventions,
        "total_interventions": total_interventions,
        "interventions_per_task": round(total_interventions / max(1, n_tasks), 2),
        "tasks_with_at_least_one_intervention": tasks_with_intervention,
        "tasks_clean": n_tasks - tasks_with_intervention,
    }


result = {
    "vllm": stats_for("/root/autodl-tmp/eval-vllm.db"),
    "api": stats_for("/root/autodl-tmp/eval-api.db"),
    "interpretation": {
        "repeat_call_blocks": "没有 harness：这些重复调用会真实打到外部 API，浪费配额且把上下文灌满重复观测",
        "validation_rejections": "没有 harness：非法参数直接发往高德/Firecrawl，产生 4xx 与不可解析的报错文本",
        "budget_enforcements": "没有 harness：失控循环会无限燃烧 token/时间",
        "model_retries_absorbed": "没有 harness：模型输出解析失败 = 整个任务直接失败",
        "evidence_gate_rejections": "没有 harness：零取证的空想答案会直接交付给用户",
    },
}
(OUT / "harness_value.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
print(json.dumps(result, ensure_ascii=False, indent=2))
