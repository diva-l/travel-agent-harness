from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
from copy import deepcopy
from dataclasses import dataclass
from typing import Any, Protocol

from .prompts import REPORT_SYSTEM_PROMPT


class ReportError(RuntimeError):
    pass


class ReportBackend(Protocol):
    @property
    def fingerprint(self) -> str: ...

    def generate(
        self,
        *,
        objective: str,
        planner_answer: str,
        evidence: list[dict[str, Any]],
    ) -> dict[str, Any]: ...


def normalize_report(value: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ReportError("report root must be a JSON object")
    report = {
        "title": str(value.get("title") or "旅行路线"),
        "subtitle": str(value.get("subtitle") or ""),
        "summary": str(value.get("summary") or ""),
        "map_kind": "geo" if value.get("map_kind") == "geo" else "schematic",
        "days": [],
        "budget": value.get("budget") if isinstance(value.get("budget"), dict) else {},
        "alerts": [str(item) for item in value.get("alerts", []) if str(item).strip()][:8],
        "evidence_notes": [
            str(item) for item in value.get("evidence_notes", []) if str(item).strip()
        ][:8],
    }
    raw_days = value.get("days") if isinstance(value.get("days"), list) else []
    for fallback_day, raw_day in enumerate(raw_days, start=1):
        if not isinstance(raw_day, dict):
            continue
        try:
            day_number = int(raw_day.get("day") or fallback_day)
        except (TypeError, ValueError):
            day_number = fallback_day
        day = {
            "day": day_number,
            "date": str(raw_day.get("date") or ""),
            "theme": str(raw_day.get("theme") or f"Day {fallback_day}"),
            "stops": [],
        }
        raw_stops = raw_day.get("stops") if isinstance(raw_day.get("stops"), list) else []
        for raw_stop in raw_stops:
            if not isinstance(raw_stop, dict) or not str(raw_stop.get("name") or "").strip():
                continue
            coordinates = raw_stop.get("coordinates")
            if isinstance(coordinates, dict):
                try:
                    lng = float(coordinates["lng"])
                    lat = float(coordinates["lat"])
                    coordinates = {"lng": lng, "lat": lat} if -180 <= lng <= 180 and -90 <= lat <= 90 else None
                except (KeyError, TypeError, ValueError):
                    coordinates = None
            else:
                coordinates = None
            category = str(raw_stop.get("category") or "other")
            if category not in {"transport", "food", "stay", "sight", "activity", "other"}:
                category = "other"
            image_url = str(raw_stop.get("image_url") or "").strip()
            if not image_url.startswith(("http://", "https://")):
                image_url = ""
            day["stops"].append(
                {
                    "name": str(raw_stop["name"]).strip(),
                    "time": str(raw_stop.get("time") or ""),
                    "duration_minutes": _optional_number(raw_stop.get("duration_minutes")),
                    "cost_cny": _optional_number(raw_stop.get("cost_cny")),
                    "category": category,
                    "transport_to_next": str(raw_stop.get("transport_to_next") or ""),
                    "note": str(raw_stop.get("note") or ""),
                    "address": str(raw_stop.get("address") or "").strip(),
                    "image_url": image_url,
                    "coordinates": coordinates,
                }
            )
        if day["stops"]:
            report["days"].append(day)
    if not report["days"]:
        raise ReportError("report contains no route stops")
    if any(stop["coordinates"] is None for day in report["days"] for stop in day["stops"]):
        report["map_kind"] = "schematic"
    budget = report["budget"]
    report["budget"] = {
        "total_cny": _optional_number(budget.get("total_cny")),
        "items": [
            {
                "label": str(item.get("label") or "其他"),
                "amount_cny": _optional_number(item.get("amount_cny")),
            }
            for item in budget.get("items", [])
            if isinstance(item, dict)
        ][:10],
    }
    return report


def _optional_number(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return round(float(value), 2)
    except (TypeError, ValueError):
        return None


def _parse_json_object(text: str) -> dict[str, Any]:
    cleaned = (text or "").strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", cleaned, flags=re.I | re.S)
    try:
        value = json.loads(cleaned)
    except json.JSONDecodeError as exc:
        raise ReportError(f"report model returned invalid JSON: {exc}") from exc
    return normalize_report(value)


@dataclass(slots=True)
class OpenAICompatibleReportModel:
    api_key: str
    base_url: str
    model: str
    timeout_seconds: float = 90.0
    max_output_tokens: int = 4096

    @property
    def fingerprint(self) -> str:
        return f"report:{self.base_url.rstrip('/')}:{self.model}"

    def generate(
        self,
        *,
        objective: str,
        planner_answer: str,
        evidence: list[dict[str, Any]],
    ) -> dict[str, Any]:
        source = {
            "user_objective": objective,
            "planner_answer": planner_answer,
            "tool_evidence": evidence,
        }
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": REPORT_SYSTEM_PROMPT},
                {"role": "user", "content": json.dumps(source, ensure_ascii=False)},
            ],
            "temperature": 0.0,
            "max_tokens": self.max_output_tokens,
            "response_format": {"type": "json_object"},
        }
        if "deepseek.com" in self.base_url:
            payload["thinking"] = {"type": "disabled"}
        request = urllib.request.Request(
            f"{self.base_url.rstrip('/')}/chat/completions",
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
                body = json.loads(response.read().decode("utf-8"))
            content = body["choices"][0]["message"]["content"]
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")[:500]
            raise ReportError(f"report model HTTP {exc.code}: {detail}") from exc
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            raise ReportError(f"report model transport error: {exc}") from exc
        except (KeyError, IndexError, TypeError) as exc:
            raise ReportError("report model response has no content") from exc
        return _parse_json_object(str(content or ""))


class ScriptedReportModel:
    def __init__(self, report: dict[str, Any]) -> None:
        self._report = normalize_report(report)

    @property
    def fingerprint(self) -> str:
        return "report:scripted:v1"

    def generate(
        self,
        *,
        objective: str,
        planner_answer: str,
        evidence: list[dict[str, Any]],
    ) -> dict[str, Any]:
        del objective, planner_answer, evidence
        return deepcopy(self._report)
