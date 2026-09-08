from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .harness import TravelHarness


@dataclass(slots=True)
class EvalCase:
    case_id: str
    prompt: str
    required_tools: list[str]


def load_cases(path: str | Path) -> list[EvalCase]:
    cases: list[EvalCase] = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        item = json.loads(line)
        cases.append(EvalCase(**item))
    return cases


def run_evaluation(harness: TravelHarness, cases: list[EvalCase]) -> dict[str, Any]:
    records: list[dict[str, Any]] = []
    for case in cases:
        state = harness.run(case.prompt)
        trace = harness.runtime.store.trace(state.task_id)
        used_tools = [
            event["payload"].get("tool")
            for event in trace
            if event["kind"] == "tool_succeeded"
        ]
        required = set(case.required_tools)
        coverage = len(required.intersection(used_tools)) / len(required) if required else 1.0
        records.append(
            {
                "case_id": case.case_id,
                "task_id": state.task_id,
                "status": state.status.value,
                "required_tool_coverage": coverage,
                "tool_errors": state.tool_errors,
                "steps": state.step,
                "total_tokens": state.total_tokens,
            }
        )
    count = len(records) or 1
    return {
        "cases": records,
        "summary": {
            "case_count": len(records),
            "completion_rate": sum(item["status"] == "completed" for item in records) / count,
            "mean_required_tool_coverage": sum(item["required_tool_coverage"] for item in records) / count,
            "tool_error_rate": sum(item["tool_errors"] for item in records)
            / max(1, sum(item["steps"] for item in records)),
            "mean_steps": sum(item["steps"] for item in records) / count,
            "total_tokens": sum(item["total_tokens"] for item in records),
        },
    }

