from __future__ import annotations

import io
import json
import unittest
from unittest import mock

from travel_agent_harness.llm import ModelError
from travel_agent_harness.protocol import (
    GUIDANCE_NO_TOOL_NO_ANSWER,
    ProtocolGuidanceError,
    TaggedOpenAICompatibleModel,
    parse_tagged_turn,
)


def _fake_response(content: str, finish_reason: str) -> io.BytesIO:
    body = {
        "choices": [{"message": {"role": "assistant", "content": content},
                     "finish_reason": finish_reason}],
        "usage": {"prompt_tokens": 10, "completion_tokens": 5},
        "model": "travel-planner",
    }
    payload = json.dumps(body).encode("utf-8")
    return io.BytesIO(payload)


class TaggedProtocolTests(unittest.TestCase):
    def test_parses_agentic_rl_tool_shape(self):
        calls, answer = parse_tagged_turn(
            '<tool_call>{"tool_name":"weather_search","tool_input":{"city":"杭州"}}</tool_call>'
        )
        self.assertIsNone(answer)
        self.assertEqual("weather_search", calls[0].name)
        self.assertEqual("杭州", calls[0].arguments["city"])

    def test_parses_final_answer(self):
        calls, answer = parse_tagged_turn("<answer>这是最终方案。</answer>")
        self.assertEqual([], calls)
        self.assertEqual("这是最终方案。", answer)

    def test_answer_wins_over_tool_calls(self):
        # Training-loop semantics (run_tool_loop_infer.py): the answer check
        # runs before tool parsing, so a turn containing both yields the answer.
        calls, answer = parse_tagged_turn(
            '<tool_call>{"name":"weather_search","arguments":{}}</tool_call><answer>完成</answer>'
        )
        self.assertEqual([], calls)
        self.assertEqual("完成", answer)

    def test_rejects_partial_tag(self):
        with self.assertRaisesRegex(ValueError, "incomplete"):
            parse_tagged_turn('<tool_call>{"name":"weather_search"}')

    def test_parses_variant_shapes(self):
        # Single-key {"search": {...}} variant accepted by the training loop.
        calls, _ = parse_tagged_turn('<tool_call>{"search": {"query": ["杭州"]}}</tool_call>')
        self.assertEqual("search", calls[0].name)
        self.assertEqual(["杭州"], calls[0].arguments["query"])
        # Arguments as a JSON string.
        calls, _ = parse_tagged_turn(
            '<tool_call>{"name":"weather_search","arguments":"{\\"city\\":\\"杭州\\"}"}</tool_call>'
        )
        self.assertEqual("杭州", calls[0].arguments["city"])
        # Plural <tool_calls> wrapper with a JSON array.
        calls, _ = parse_tagged_turn(
            '<tool_calls>[{"name":"weather_search","arguments":{"city":"杭州"}}]</tool_calls>'
        )
        self.assertEqual("weather_search", calls[0].name)

    def test_json_repair_fallback(self):
        # Trailing comma is invalid JSON; the training loop repairs it.
        calls, _ = parse_tagged_turn(
            '<tool_call>{"name":"weather_search","arguments":{"city":"杭州",}}</tool_call>'
        )
        self.assertEqual("杭州", calls[0].arguments["city"])

    def test_unclosed_answer_accepted(self):
        calls, answer = parse_tagged_turn("<answer>方案前半部分")
        self.assertEqual([], calls)
        self.assertEqual("方案前半部分", answer)

    def test_translates_tool_observation_for_text_model(self):
        model = TaggedOpenAICompatibleModel(api_key="k", base_url="http://x/v1", model="m")
        converted = model._to_tagged_messages(
            [
                {"role": "assistant", "content": "<tool_call>{}</tool_call>"},
                {
                    "role": "tool",
                    "tool_call_id": "c1",
                    "name": "weather_search",
                    "content": '{"ok":true,"result":{"data_source":"demo","weather":"晴","notice":"n"}}',
                },
                {
                    "role": "tool",
                    "tool_call_id": "c2",
                    "name": "search",
                    "content": '{"ok":false,"error":"boom"}',
                },
            ]
        )
        self.assertEqual("user", converted[1]["role"])
        # Observations mirror the training-side format: bare <tool_response>
        # without a name attribute, result payload rendered as json2md markdown
        # (harness-only data_source/notice keys stripped), failures as
        # TOOL_ERROR lines.
        self.assertIn("<tool_response>", converted[1]["content"])
        self.assertNotIn('name="weather_search"', converted[1]["content"])
        self.assertIn("weather: 晴", converted[1]["content"])
        self.assertNotIn("data_source", converted[1]["content"])
        self.assertIn("TOOL_ERROR: boom", converted[2]["content"])

    def test_observation_prefers_handler_text_and_truncates(self):
        model = TaggedOpenAICompatibleModel(
            api_key="k", base_url="http://x/v1", model="m", tool_output_max_chars=100
        )
        long_text = "开头内容 " * 40 + "结尾内容 " * 40
        envelope = json.dumps(
            {"ok": True, "result": {"data_source": "firecrawl_search", "text": long_text}},
            ensure_ascii=False,
        )
        converted = model._to_tagged_messages([{"role": "tool", "content": envelope}])
        body = converted[0]["content"]
        self.assertIn("内容已截断", body)
        self.assertIn("开头", body)
        self.assertIn("结尾", body)  # head+tail truncation keeps both ends

    def test_malformed_turn_raises_guidance(self):
        model = TaggedOpenAICompatibleModel(api_key="k", base_url="http://x/v1", model="m")
        with mock.patch(
            "urllib.request.urlopen",
            side_effect=lambda req, timeout=None: _fake_response("我直接回答可以吗", "stop"),
        ):
            with self.assertRaises(ProtocolGuidanceError) as ctx:
                model.complete([], [])
        self.assertEqual(GUIDANCE_NO_TOOL_NO_ANSWER, ctx.exception.guidance)
        self.assertTrue(ctx.exception.retryable)


    def test_truncated_tag_retries_with_bigger_budget(self):
        model = TaggedOpenAICompatibleModel(
            api_key="k", base_url="http://x/v1", model="m", max_output_tokens=100)
        truncated = '<tool_call>{"name":"weather_search","arguments":{"city":"杭州"'
        good = '<answer>完成</answer>'
        seen_payloads = []

        def fake_urlopen(req, timeout=None):
            seen_payloads.append(json.loads(req.data.decode("utf-8")))
            return _fake_response(truncated if len(seen_payloads) == 1 else good,
                                  "length" if len(seen_payloads) == 1 else "stop")

        with mock.patch("urllib.request.urlopen", side_effect=fake_urlopen):
            turn = model.complete([], [])
        self.assertEqual("完成", turn.content)
        self.assertEqual(2, len(seen_payloads))
        self.assertEqual(100, seen_payloads[0]["max_tokens"])
        self.assertEqual(150, seen_payloads[1]["max_tokens"])

    def test_double_truncation_stays_retryable_for_runtime(self):
        model = TaggedOpenAICompatibleModel(
            api_key="k", base_url="http://x/v1", model="m", max_output_tokens=100)
        truncated = '<tool_call>{"name":"weather_search"'
        with mock.patch("urllib.request.urlopen",
                        side_effect=lambda req, timeout=None: _fake_response(truncated, "length")):
            with self.assertRaises(ModelError) as ctx:
                model.complete([], [])
        self.assertTrue(ctx.exception.retryable)

    def test_garbage_protocol_gets_guidance_not_retry(self):
        model = TaggedOpenAICompatibleModel(api_key="k", base_url="http://x/v1", model="m")
        calls = []
        with mock.patch("urllib.request.urlopen",
                        side_effect=lambda req, timeout=None: (calls.append(1), _fake_response('<tool_call>{"name":"x"}', "stop"))[1]):
            with self.assertRaises(ProtocolGuidanceError) as ctx:
                model.complete([], [])
        self.assertEqual(1, len(calls))  # no silent resample inside the model
        # '<tool_call>{"name":"x"}' has no closing tag → partial-call guidance.
        self.assertIn("不完整", ctx.exception.guidance)


if __name__ == "__main__":
    unittest.main()
