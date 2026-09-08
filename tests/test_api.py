from __future__ import annotations

import tempfile
import time
import unittest
from datetime import date, timedelta
from pathlib import Path

from fastapi.testclient import TestClient

from travel_agent_harness.api.app import create_app
from travel_agent_harness.config import HarnessConfig
from travel_agent_harness.harness import build_default_harness
from travel_agent_harness.llm import ModelTurn, ScriptedModel
from travel_agent_harness.models import ToolCall
from travel_agent_harness.reporting import ScriptedReportModel


class ApiTests(unittest.TestCase):
    def test_plan_api_uses_runtime_and_exposes_trace(self):
        with tempfile.TemporaryDirectory() as folder:
            config = HarnessConfig(
                api_key="test-key",
                base_url="https://example.invalid",
                model="scripted",
                db_path=Path(folder) / "api.db",
                max_steps=5,
                max_seconds=30,
                max_total_tokens=1000,
                max_tool_calls=5,
                model_retries=0,
            )
            model = ScriptedModel(
                [
                    ModelTurn(
                        tool_calls=[
                            ToolCall(
                                "api-call-1",
                                "weather_search",
                                {"city": "杭州"},
                            )
                        ]
                    ),
                    ModelTurn(content="已基于演示天气生成行程；请在出发前复核实时信息。"),
                ]
            )
            reporter = ScriptedReportModel(
                {
                    "title": "上海到杭州三日路线",
                    "subtitle": "沿西湖人文轴线展开",
                    "summary": "先湖滨漫步，再进入灵隐与龙井片区。",
                    "map_kind": "geo",
                    "days": [
                        {
                            "day": 1,
                            "date": "2026-10-02",
                            "theme": "西湖城市漫步",
                            "stops": [
                                {
                                    "name": "断桥残雪",
                                    "time": "09:30",
                                    "category": "sight",
                                    "transport_to_next": "步行",
                                    "coordinates": {"lng": 120.1484, "lat": 30.2591},
                                },
                                {
                                    "name": "北山街",
                                    "time": "11:00",
                                    "category": "activity",
                                    "coordinates": {"lng": 120.1427, "lat": 30.2587},
                                },
                            ],
                        }
                    ],
                    "budget": {"total_cny": 1800, "items": []},
                    "alerts": ["出发前复核天气"],
                    "evidence_notes": ["天气来自演示数据"],
                }
            )
            harness = build_default_harness(config, model=model, reporter=reporter)
            app = create_app(config, harness)
            with TestClient(app) as client:
                self.assertEqual(200, client.get("/api/health").status_code)
                safe_config = client.get("/api/config").json()
                self.assertNotIn("api_key", safe_config)
                self.assertEqual("AgentRuntime", safe_config["runtime"]["engine"])
                self.assertEqual(8, len(safe_config["tools"]))
                self.assertEqual(
                    ["agentic_rl_planner", "report_model", "interactive_route_view"],
                    safe_config["product_pipeline"],
                )
                response = client.post(
                    "/api/plans",
                    json={
                        "origin": "上海",
                        "destination": "杭州",
                        "start_date": (date.today() + timedelta(days=7)).isoformat(),
                        "days": 2,
                        "budget_cny": 1800,
                        "travelers": 1,
                        "pace": "balanced",
                        "preferences": ["人文"],
                        "notes": "不早起",
                    },
                )
                self.assertEqual(202, response.status_code)
                task_id = response.json()["task_id"]
                deadline = time.monotonic() + 3
                state = {}
                while time.monotonic() < deadline:
                    state = client.get(f"/api/plans/{task_id}").json()
                    if state["status"] == "completed" and state["report_status"] == "completed":
                        break
                    time.sleep(0.02)
                self.assertEqual("completed", state["status"])
                self.assertEqual("completed", state["report_status"])
                self.assertEqual("上海到杭州三日路线", state["report"]["title"])
                self.assertEqual(1, state["metrics"]["successful_tool_calls"])
                trace = client.get(f"/api/plans/{task_id}/trace").json()["events"]
                self.assertIn("tool_succeeded", [event["kind"] for event in trace])
                self.assertIn("report_completed", [event["kind"] for event in trace])
                checkpoints = client.get(
                    f"/api/plans/{task_id}/checkpoints"
                ).json()["checkpoints"]
                self.assertGreaterEqual(len(checkpoints), 3)
                forked = client.post(
                    f"/api/plans/{task_id}/fork",
                    json={"checkpoint_seq": checkpoints[0]["seq"], "run": False},
                )
                self.assertEqual(202, forked.status_code)
                self.assertEqual("created", forked.json()["status"])
                self.assertTrue(state["guardrails"]["evidence_gate"]["passed"])
                self.assertEqual(200, client.get("/").status_code)

    def test_queue_full_returns_503_with_retry_after(self):
        with tempfile.TemporaryDirectory() as folder:
            config = HarnessConfig(
                api_key="test-key",
                base_url="https://example.invalid",
                model="scripted",
                db_path=Path(folder) / "api.db",
                max_steps=2,
                max_seconds=30,
                max_total_tokens=1000,
                max_tool_calls=5,
                model_retries=0,
                report_enabled=False,
                api_workers=1,
                api_queue_size=0,
            )

            class SlowModel:
                fingerprint = "slow:test"

                def complete(self, messages, tools):
                    time.sleep(0.5)
                    return ModelTurn(content="done")

            harness = build_default_harness(config, model=SlowModel())
            app = create_app(config, harness)
            payload = {
                "origin": "上海",
                "destination": "杭州",
                "start_date": (date.today() + timedelta(days=7)).isoformat(),
                "days": 2,
                "budget_cny": 1800,
            }
            with TestClient(app) as client:
                first = client.post("/api/plans", json=payload)
                self.assertEqual(202, first.status_code)
                # The single worker is busy for ~0.5s and the queue holds 0,
                # so an immediate second submission must fail fast.
                second = client.post("/api/plans", json=payload)
                self.assertEqual(503, second.status_code)
                self.assertEqual("30", second.headers.get("Retry-After"))
                task_id = first.json()["task_id"]
                deadline = time.monotonic() + 5
                while time.monotonic() < deadline:
                    if client.get(f"/api/plans/{task_id}").json()["status"] == "completed":
                        break
                    time.sleep(0.02)
                # After the worker frees up, submissions are accepted again.
                third = client.post("/api/plans", json=payload)
                self.assertEqual(202, third.status_code)
                third_id = third.json()["task_id"]
                deadline = time.monotonic() + 5
                while time.monotonic() < deadline:
                    if client.get(f"/api/plans/{third_id}").json()["status"] == "completed":
                        break
                    time.sleep(0.02)
    def test_metrics_endpoint_aggregates_persisted_runs(self):
        with tempfile.TemporaryDirectory() as folder:
            config = HarnessConfig(
                api_key="test-key",
                base_url="https://example.invalid",
                model="scripted",
                db_path=Path(folder) / "api.db",
                max_steps=5,
                max_seconds=30,
                max_total_tokens=1000,
                max_tool_calls=5,
                model_retries=0,
                report_enabled=False,
            )
            model = ScriptedModel(
                [
                    ModelTurn(
                        tool_calls=[
                            ToolCall("m-call-1", "weather_search", {"city": "杭州"})
                        ]
                    ),
                    ModelTurn(content="基于演示数据完成规划。"),
                ]
            )
            harness = build_default_harness(config, model=model)
            app = create_app(config, harness)
            with TestClient(app) as client:
                empty = client.get("/api/metrics").json()
                self.assertEqual(0, empty["tasks_total"])
                self.assertIsNone(empty["success_rate"])
                response = client.post(
                    "/api/plans",
                    json={
                        "origin": "上海",
                        "destination": "杭州",
                        "start_date": (date.today() + timedelta(days=7)).isoformat(),
                        "days": 2,
                        "budget_cny": 1800,
                    },
                )
                self.assertEqual(202, response.status_code)
                task_id = response.json()["task_id"]
                deadline = time.monotonic() + 3
                status_value = ""
                while time.monotonic() < deadline:
                    status_value = client.get(f"/api/plans/{task_id}").json()["status"]
                    if status_value == "completed":
                        break
                    time.sleep(0.02)
                self.assertEqual("completed", status_value)
                metrics = client.get("/api/metrics").json()
                self.assertEqual(1, metrics["tasks_total"])
                self.assertEqual(1, metrics["tasks_by_status"]["completed"])
                self.assertEqual(1, metrics["terminal_tasks"])
                self.assertEqual(1.0, metrics["success_rate"])
                self.assertGreater(metrics["steps"]["max"], 0)
                self.assertEqual(1, metrics["tool_calls"]["successful"])
                self.assertEqual(0, metrics["tool_calls"]["validation_errors"])
                self.assertEqual({"weather_search": 1}, metrics["tool_usage"])

    def test_optional_bearer_token_guards_api_routes(self):
        with tempfile.TemporaryDirectory() as folder:
            config = HarnessConfig(
                api_key="test-key",
                base_url="https://example.invalid",
                model="scripted",
                db_path=Path(folder) / "api.db",
                report_enabled=False,
                api_token="secret-token",
            )
            model = ScriptedModel([ModelTurn(content="ok")])
            harness = build_default_harness(config, model=model)
            app = create_app(config, harness)
            with TestClient(app) as client:
                # Health probes and the static UI stay open; the API is gated.
                self.assertEqual(200, client.get("/api/health").status_code)
                self.assertEqual(200, client.get("/").status_code)
                self.assertEqual(401, client.get("/api/config").status_code)
                self.assertEqual(401, client.get("/api/metrics").status_code)
                denied = client.post("/api/plans", json={})
                self.assertEqual(401, denied.status_code)
                self.assertEqual("Bearer", denied.headers.get("WWW-Authenticate"))
                wrong = client.get(
                    "/api/config", headers={"Authorization": "Bearer wrong"}
                )
                self.assertEqual(401, wrong.status_code)
                allowed = client.get(
                    "/api/config", headers={"Authorization": "Bearer secret-token"}
                )
                self.assertEqual(200, allowed.status_code)


if __name__ == "__main__":
    unittest.main()
