from __future__ import annotations

import tempfile
import time
import unittest
from pathlib import Path

from travel_agent_harness.config import HarnessConfig
from travel_agent_harness.harness import build_default_harness
from travel_agent_harness.llm import ModelTurn, ScriptedModel
from travel_agent_harness.models import TaskStatus, ToolCall
from travel_agent_harness.tools import ToolRegistry, ToolSpec, build_travel_registry


def config_for(path: Path, **overrides):
    values = {
        "api_key": "test-key",
        "base_url": "https://example.invalid",
        "model": "scripted",
        "db_path": path,
        "max_steps": 5,
        "max_seconds": 30,
        "max_total_tokens": 1000,
        "max_tool_calls": 5,
        "model_retries": 0,
        "report_enabled": False,
    }
    values.update(overrides)
    return HarnessConfig(**values)


class RuntimeTests(unittest.TestCase):
    def test_tool_loop_completes_and_persists_trace(self):
        with tempfile.TemporaryDirectory() as folder:
            model = ScriptedModel(
                [
                    ModelTurn(
                        tool_calls=[
                            ToolCall(
                                "call-1",
                                "train_tickets_search",
                                {"from_city": "杭州", "to_city": "上海", "date": "2026-10-02"},
                            )
                        ],
                        prompt_tokens=20,
                        completion_tokens=8,
                    ),
                    ModelTurn(content="方案完成；所有价格均为演示数据。", prompt_tokens=30, completion_tokens=12),
                ]
            )
            harness = build_default_harness(
                config_for(Path(folder) / "test.db"), model=model, tools=build_travel_registry()
            )
            state = harness.run("测试行程")
            self.assertEqual(TaskStatus.COMPLETED, state.status)
            self.assertEqual(1, state.successful_tool_calls)
            self.assertEqual(70, state.total_tokens)
            kinds = [item["kind"] for item in harness.runtime.store.trace(state.task_id)]
            self.assertIn("tool_succeeded", kinds)
            self.assertIn("state_transition", kinds)
            self.assertGreaterEqual(len(harness.runtime.store.list_checkpoints(state.task_id)), 3)

    def test_evidence_gate_rejects_early_final(self):
        with tempfile.TemporaryDirectory() as folder:
            model = ScriptedModel(
                [
                    ModelTurn(content="未经工具的答案"),
                    ModelTurn(tool_calls=[ToolCall("call-2", "weather_search", {"city": "上海"})]),
                    ModelTurn(content="已基于演示天气给出方案，并明确不是实时预报。"),
                ]
            )
            harness = build_default_harness(config_for(Path(folder) / "test.db"), model=model)
            state = harness.run("测试证据门禁")
            self.assertEqual(TaskStatus.COMPLETED, state.status)
            kinds = [item["kind"] for item in harness.runtime.store.trace(state.task_id)]
            self.assertIn("final_rejected", kinds)

    def test_side_effect_tool_pauses_for_approval(self):
        with tempfile.TemporaryDirectory() as folder:
            registry = ToolRegistry()
            registry.register(
                ToolSpec(
                    name="book_ticket",
                    description="测试预订",
                    parameters={
                        "type": "object",
                        "properties": {"trip_id": {"type": "string"}},
                        "required": ["trip_id"],
                        "additionalProperties": False,
                    },
                    handler=lambda trip_id: {"data_source": "test", "booked": trip_id},
                    requires_approval=True,
                    side_effect_free=False,
                )
            )
            model = ScriptedModel(
                [
                    ModelTurn(tool_calls=[ToolCall("approval-1", "book_ticket", {"trip_id": "T1"})]),
                    ModelTurn(content="预订流程已完成。"),
                ]
            )
            harness = build_default_harness(config_for(Path(folder) / "test.db"), model=model, tools=registry)
            paused = harness.run("测试人工审批")
            self.assertEqual(TaskStatus.WAITING_APPROVAL, paused.status)
            completed = harness.approve(paused.task_id, "approval-1")
            self.assertEqual(TaskStatus.COMPLETED, completed.status)

    def test_fork_restores_historical_checkpoint(self):
        with tempfile.TemporaryDirectory() as folder:
            model = ScriptedModel(
                [
                    ModelTurn(tool_calls=[ToolCall("call-1", "weather_search", {"city": "苏州"})]),
                    ModelTurn(content="完成，天气为演示数据。"),
                ]
            )
            harness = build_default_harness(config_for(Path(folder) / "test.db"), model=model)
            state = harness.run("测试分叉")
            fork = harness.fork(state.task_id, 1)
            self.assertEqual(TaskStatus.CREATED, fork.status)
            self.assertEqual(state.task_id, fork.forked_from["task_id"])
            self.assertNotEqual(state.task_id, fork.task_id)

    def _slow_registry(self, delay: float = 0.3) -> ToolRegistry:
        def slow_handler(label: str) -> dict:
            time.sleep(delay)
            return {"data_source": "test", "label": label}

        registry = ToolRegistry()
        registry.register(
            ToolSpec(
                "slow_tool",
                "慢速测试工具",
                {
                    "type": "object",
                    "properties": {"label": {"type": "string"}},
                    "required": ["label"],
                    "additionalProperties": False,
                },
                slow_handler,
            )
        )
        return registry

    def test_parallel_tool_calls_preserve_order_and_run_concurrently(self):
        with tempfile.TemporaryDirectory() as folder:
            model = ScriptedModel(
                [
                    ModelTurn(
                        tool_calls=[
                            ToolCall("call-a", "slow_tool", {"label": "A"}),
                            ToolCall("call-b", "slow_tool", {"label": "B"}),
                        ]
                    ),
                    ModelTurn(content="两个结果都拿到了。"),
                ]
            )
            harness = build_default_harness(
                config_for(Path(folder) / "test.db"),
                model=model,
                tools=self._slow_registry(delay=0.5),
            )
            started = time.monotonic()
            state = harness.run("并行测试")
            elapsed = time.monotonic() - started
            self.assertEqual(TaskStatus.COMPLETED, state.status)
            self.assertEqual(2, state.successful_tool_calls)
            # Sequential would be >= 1.0s; parallel lands near 0.5s. The 0.85s
            # bound keeps a wide margin for loaded CI runners.
            self.assertLess(elapsed, 0.85)
            tool_messages = [m for m in state.messages if m["role"] == "tool"]
            self.assertEqual(["call-a", "call-b"], [m["tool_call_id"] for m in tool_messages])
            self.assertIn('"A"', tool_messages[0]["content"])
            self.assertIn('"B"', tool_messages[1]["content"])
            succeeded = [
                item for item in harness.runtime.store.trace(state.task_id)
                if item["kind"] == "tool_succeeded"
            ]
            self.assertTrue(all("duration_ms" in item["payload"] for item in succeeded))

    def test_parallel_tool_calls_disabled_runs_sequentially(self):
        with tempfile.TemporaryDirectory() as folder:
            model = ScriptedModel(
                [
                    ModelTurn(
                        tool_calls=[
                            ToolCall("call-a", "slow_tool", {"label": "A"}),
                            ToolCall("call-b", "slow_tool", {"label": "B"}),
                        ]
                    ),
                    ModelTurn(content="完成。"),
                ]
            )
            harness = build_default_harness(
                config_for(Path(folder) / "test.db", parallel_tool_calls=False), model=model,
                tools=self._slow_registry(delay=0.3),
            )
            started = time.monotonic()
            state = harness.run("串行测试")
            elapsed = time.monotonic() - started
            self.assertEqual(TaskStatus.COMPLETED, state.status)
            self.assertGreaterEqual(elapsed, 0.55)

    def test_repeat_block_still_applies_with_parallel_calls(self):
        with tempfile.TemporaryDirectory() as folder:
            calls = [
                ToolCall(f"call-{index}", "slow_tool", {"label": "same"})
                for index in range(4)
            ]
            model = ScriptedModel(
                [ModelTurn(tool_calls=calls), ModelTurn(content="完成。")]
            )
            harness = build_default_harness(
                config_for(Path(folder) / "test.db", repeat_call_limit=3),
                model=model,
                tools=self._slow_registry(delay=0.01),
            )
            state = harness.run("重复阻断测试")
            self.assertEqual(TaskStatus.COMPLETED, state.status)
            self.assertEqual(3, state.successful_tool_calls)
            self.assertEqual(1, state.tool_errors)
            tool_messages = [m for m in state.messages if m["role"] == "tool"]
            self.assertEqual(4, len(tool_messages))
            self.assertIn("repeated identical tool call blocked", tool_messages[-1]["content"])

    def test_force_answer_after_steps_injects_training_message(self):
        with tempfile.TemporaryDirectory() as folder:
            model = ScriptedModel(
                [
                    ModelTurn(
                        tool_calls=[
                            ToolCall("call-1", "weather_search", {"city": "杭州"}),
                        ]
                    ),
                    ModelTurn(content="最终方案。"),
                ]
            )
            harness = build_default_harness(
                config_for(Path(folder) / "test.db", force_answer_after_steps=1),
                model=model,
                tools=build_travel_registry(),
            )
            state = harness.run("强制收尾测试")
            self.assertEqual(TaskStatus.COMPLETED, state.status)
            user_messages = [m["content"] for m in state.messages if m["role"] == "user"]
            self.assertIn(
                "信息已经足够。禁止继续调用工具，请直接给最终方案，并严格用<answer>...</answer>输出。",
                user_messages,
            )
            kinds = [item["kind"] for item in harness.runtime.store.trace(state.task_id)]
            self.assertIn("force_answer_injected", kinds)

    def test_repeat_answer_chance_offers_forced_answer_once(self):
        with tempfile.TemporaryDirectory() as folder:
            repeat = [ToolCall(f"call-{index}", "weather_search", {"city": "杭州"}) for index in range(4)]
            model = ScriptedModel(
                [
                    ModelTurn(tool_calls=repeat),
                    ModelTurn(tool_calls=[ToolCall("call-x", "weather_search", {"city": "杭州"})]),
                    ModelTurn(content="被迫收尾。"),
                ]
            )
            harness = build_default_harness(
                config_for(
                    Path(folder) / "test.db",
                    repeat_call_limit=3,
                    repeat_answer_chance=True,
                ),
                model=model,
                tools=build_travel_registry(),
            )
            state = harness.run("重复强制作答测试")
            self.assertEqual(TaskStatus.COMPLETED, state.status)
            chance_messages = [
                m for m in state.messages
                if m["role"] == "user" and m["content"].startswith("检测到你连续重复了相同工具调用")
            ]
            self.assertEqual(1, len(chance_messages))
            self.assertTrue(state.repeat_answer_chance_used)

    def test_repeat_answer_chance_disabled_by_default(self):
        with tempfile.TemporaryDirectory() as folder:
            repeat = [ToolCall(f"call-{index}", "weather_search", {"city": "杭州"}) for index in range(4)]
            model = ScriptedModel(
                [ModelTurn(tool_calls=repeat), ModelTurn(content="完成。")]
            )
            harness = build_default_harness(
                config_for(Path(folder) / "test.db", repeat_call_limit=3),
                model=model,
                tools=build_travel_registry(),
            )
            state = harness.run("默认不注入")
            self.assertEqual(TaskStatus.COMPLETED, state.status)
            self.assertFalse(state.repeat_answer_chance_used)
            self.assertFalse(
                any(
                    m["role"] == "user" and m["content"].startswith("检测到你连续重复")
                    for m in state.messages
                )
            )


    def test_protocol_guidance_error_injected_as_user_message(self):
        from travel_agent_harness.protocol import GUIDANCE_NO_TOOL_NO_ANSWER, ProtocolGuidanceError

        class FlakyModel:
            fingerprint = "flaky:v1"

            def __init__(self):
                self.calls = 0

            def complete(self, messages, tools):
                self.calls += 1
                if self.calls == 1:
                    raise ProtocolGuidanceError("no tool_call or answer", guidance=GUIDANCE_NO_TOOL_NO_ANSWER)
                if self.calls == 2:
                    call = ToolCall("call-9", "weather_search", {"city": "杭州"})
                    return ModelTurn(
                        tool_calls=[call],
                        raw_assistant_message={"role": "assistant", "content": None},
                    )
                return ModelTurn(
                    content="收尾方案。",
                    raw_assistant_message={"role": "assistant", "content": "收尾方案。"},
                )

        with tempfile.TemporaryDirectory() as folder:
            harness = build_default_harness(
                config_for(Path(folder) / "test.db", model_retries=1),
                model=FlakyModel(),
                tools=build_travel_registry(),
            )
            state = harness.run("协议引导测试")
            self.assertEqual(TaskStatus.COMPLETED, state.status)
            user_messages = [m["content"] for m in state.messages if m["role"] == "user"]
            self.assertIn(GUIDANCE_NO_TOOL_NO_ANSWER, user_messages)
            kinds = [item["kind"] for item in harness.runtime.store.trace(state.task_id)]
            self.assertIn("protocol_guidance", kinds)

    def test_consecutive_identical_rounds_trigger_chance_then_exhaust(self):
        with tempfile.TemporaryDirectory() as folder:
            same = lambda index: ModelTurn(  # noqa: E731
                tool_calls=[ToolCall(f"call-{index}", "weather_search", {"city": "杭州"})]
            )
            model = ScriptedModel([same(i) for i in range(6)] + [ModelTurn(content="最后收尾。")])
            harness = build_default_harness(
                config_for(
                    Path(folder) / "test.db",
                    repeat_call_limit=99,  # isolate the consecutive-round rule
                    repeat_answer_chance=True,
                    max_steps=20,
                    max_tool_calls=20,
                ),
                model=model,
                tools=build_travel_registry(),
            )
            state = harness.run("连续整轮重复测试")
            # Third identical round → one forced-answer chance; the fourth
            # identical round → "repeated_tool_call_loop" stop.
            self.assertEqual(TaskStatus.EXHAUSTED, state.status)
            self.assertEqual("repeated_tool_call_loop", state.error)
            chance_messages = [
                m for m in state.messages
                if m["role"] == "user" and m["content"].startswith("检测到你连续重复了相同工具调用")
            ]
            self.assertEqual(1, len(chance_messages))


if __name__ == "__main__":
    unittest.main()
