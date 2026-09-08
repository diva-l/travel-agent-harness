from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
import uuid
from dataclasses import dataclass
from typing import Any

from .llm import ModelError
from .markdown import json2md, truncate_text
from .models import ModelTurn, ToolCall


_TOOL_BLOCK = re.compile(r"<tool_call>\s*(.*?)\s*</tool_call>", re.I | re.S)
_TOOL_BLOCK_PLURAL = re.compile(r"<tool_calls>\s*(.*?)\s*</tool_calls>", re.I | re.S)
_ANSWER_BLOCK = re.compile(r"<answer>\s*(.*?)\s*</answer>", re.I | re.S)
_ANSWER_START = re.compile(r"<answer>\s*(.*)$", re.I | re.S)

# Guidance messages copied verbatim from travel_agentic_rl/run_tool_loop_infer.py.
# The trained planner has seen these exact strings on malformed turns.
GUIDANCE_NO_TOOL_NO_ANSWER = (
    "请优先调用最合适的工具来获取事实信息，"
    "并仅输出结构化 tool_call内容。用<tool_call>...</tool_call>包裹，正确格式示例："
    '<tool_call>'
    '{"name": "search", "arguments": {"query":["杭州西湖门票价格"]}}'
    '</tool_call>'
    "请严格输出合法JSON"
)
GUIDANCE_PARTIAL_TOOL_CALL = (
    "你上一轮的工具调用不完整。请仅输出完整、可解析的结构化<tool_call>...</tool_call>。"
)
# Training loop's tool-first enforcement message (run_tool_loop_infer.py).
GUIDANCE_TOOL_FIRST = "请先通过 tool_call 调用至少一个工具，并在收到工具结果后再输出最终<answer>。"


class ProtocolGuidanceError(ModelError):
    """Malformed tagged turn that the training loop answers with an in-context
    guidance message (not a silent resample). Carries the verbatim message so
    the runtime can append it to the conversation before retrying."""

    def __init__(self, message: str, *, guidance: str) -> None:
        super().__init__(message, retryable=True)
        self.guidance = guidance


def _unwrap_fence(value: str) -> str:
    text = value.strip()
    if text.startswith("```") and text.endswith("```"):
        lines = text.splitlines()
        if len(lines) >= 3:
            return "\n".join(lines[1:-1]).strip()
    return text


def _loads_tolerant(raw: str) -> Any:
    """json.loads with a json_repair fallback, mirroring the training loop."""
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        import json_repair

        repaired = json_repair.loads(raw)
        if repaired in (None, "", {}, []):
            raise ValueError("unparseable tool_call payload")
        return repaired


def _normalize_tag_call(payload: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    # Variant shapes the training loop accepts (run_tool_loop_infer.py
    # _normalize_calls / extract_call): {"tool":..,"parameters":..},
    # {"tool_name":..,"tool_input":..}, single-key {"search": {...}},
    # {"function": {...}} and arguments given as a JSON string.
    if isinstance(payload.get("function"), dict):
        function = payload["function"]
        name = function.get("name") or payload.get("name")
        arguments = function.get("arguments", function.get("parameters", {}))
    elif isinstance(payload.get("function"), str):
        name = payload["function"] or payload.get("name")
        arguments = payload.get("arguments", payload.get("parameters", {}))
    elif payload.get("tool") and payload.get("parameters") is not None:
        name, arguments = payload["tool"], payload["parameters"]
    elif payload.get("tool_name") and payload.get("tool_input") is not None:
        name, arguments = payload["tool_name"], payload["tool_input"]
    elif payload.get("name"):
        name = payload["name"]
        arguments = payload.get("arguments", payload.get("parameters", payload.get("input", {})))
    elif len(payload) == 1:
        name, arguments = next(iter(payload.items()))
    else:
        raise ValueError("missing tool name")
    if isinstance(arguments, str):
        arguments = _loads_tolerant(arguments)
    if not isinstance(name, str) or not name.strip():
        raise ValueError("missing tool name")
    if not isinstance(arguments, dict):
        arguments = {"raw_arguments": str(arguments)}
    return name.strip(), arguments


def parse_tagged_turn(text: str) -> tuple[list[ToolCall], str | None]:
    """Parse the protocol used by the trained travel model.

    Mirrors run_tool_loop_infer.py semantics: an <answer> (even unclosed) wins
    over tool calls in the same turn; malformed JSON falls back to json_repair;
    a lone <tool_calls> plural wrapper is accepted. Raises ValueError with a
    training-verbatim guidance message for turns the training loop would
    answer with guidance instead of executing.
    """
    text = text or ""
    answer_match = _ANSWER_BLOCK.search(text)
    if answer_match is None:
        # Training accepts a bare "<answer>" start as the final answer
        # (has_answer_start); the prediction is whatever follows the tag.
        start_match = _ANSWER_START.search(text)
        if start_match and start_match.group(1).strip():
            answer_match = start_match
    if answer_match:
        # Training checks for the answer before parsing tool calls: an answer
        # wins even if the turn also contains tool_call blocks.
        return [], answer_match.group(1).strip()

    tool_blocks = _TOOL_BLOCK.findall(text)
    if not tool_blocks:
        plural = _TOOL_BLOCK_PLURAL.search(text)
        if plural:
            tool_blocks = [plural.group(1)]
    if not tool_blocks:
        if "<tool_call>" in text.lower():
            raise ValueError("incomplete tool_call tag")
        raise ValueError("response contains neither tool_call nor answer")

    calls: list[ToolCall] = []
    for raw in tool_blocks:
        payload = _loads_tolerant(_unwrap_fence(raw))
        items = payload if isinstance(payload, list) else [payload]
        # OpenAI-style {"tool_calls": [...]} wrapper.
        if (
            len(items) == 1
            and isinstance(items[0], dict)
            and isinstance(items[0].get("tool_calls"), list)
        ):
            items = items[0]["tool_calls"]
        for item in items:
            if not isinstance(item, dict):
                raise ValueError("tool_call payload must be an object")
            name, arguments = _normalize_tag_call(item)
            calls.append(ToolCall(f"tag-{uuid.uuid4().hex[:16]}", name, arguments))
    if not calls:
        raise ValueError("response contains neither tool_call nor answer")
    return calls, None


def _tag_protocol_prompt(tools: list[dict[str, Any]]) -> str:
    definitions = [item.get("function", item) for item in tools]
    return """\
# Tagged tool protocol
你必须只在以下两种动作中选择一种：
1. 调用工具：<tool_call>{"name":"工具名","arguments":{}}</tool_call>
2. 最终回答：<answer>最终出行方案</answer>
同一轮禁止同时输出 tool_call 和 answer。工具参数必须是合法 JSON，并严格匹配工具定义。
可用工具：
<tools>
%s
</tools>
""" % json.dumps(definitions, ensure_ascii=False)


# Keys the harness adds for guardrails/reporting that never existed in the
# training observations; stripped before markdown rendering.
_HARNESS_ONLY_KEYS = {"data_source", "notice", "truncated", "original_chars"}


def _to_training_observation(content: str, max_chars: int = 5000) -> str:
    """Render a Runtime tool envelope the way the RL training side did.

    Training observations were markdown (json2md of the tool payload) or plain
    text, truncated head+tail at 5000 chars; failures arrived as
    ``TOOL_ERROR: ...`` lines. Unwrapping and re-rendering the Harness
    ``{"ok": ..., ...}`` envelope here keeps the online observation
    distribution aligned with what the planner was trained on.
    """
    try:
        payload = json.loads(content)
    except json.JSONDecodeError:
        return truncate_text(content, max_chars)
    if not isinstance(payload, dict):
        return truncate_text(str(payload), max_chars)
    if payload.get("ok") is False:
        return truncate_text(f"TOOL_ERROR: {payload.get('error', 'unknown error')}", max_chars)
    if payload.get("ok") is True and "result" in payload:
        result = payload["result"]
        if isinstance(result, dict):
            # Handler-provided training-format text (search/visit/simulators).
            if isinstance(result.get("text"), str) and result["text"].strip():
                return truncate_text(result["text"], max_chars)
            result = {
                key: value
                for key, value in result.items()
                if key not in _HARNESS_ONLY_KEYS
            }
            return truncate_text(json2md(result), max_chars)
        if isinstance(result, list):
            return truncate_text(json2md(result), max_chars)
        return truncate_text(str(result), max_chars)
    return truncate_text(content, max_chars)


@dataclass(slots=True)
class TaggedOpenAICompatibleModel:
    """OpenAI-compatible text backend for the RL model's tag-based tool protocol."""

    api_key: str
    base_url: str
    model: str
    timeout_seconds: float = 90.0
    max_output_tokens: int = 1800
    temperature: float = 0.1
    top_p: float = 1.0
    top_k: int = 0
    # Observation truncation applied after markdown rendering, matching the
    # training loop's tool_response_max_chars.
    tool_output_max_chars: int = 5000

    @property
    def fingerprint(self) -> str:
        return f"openai-compatible-tagged:{self.base_url.rstrip('/')}:{self.model}"

    def complete(
        self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]
    ) -> ModelTurn:
        try:
            return self._complete_once(messages, tools, self.max_output_tokens)
        except ModelError as exc:
            # Guidance errors go back to the runtime for in-context feedback,
            # not a same-context resample.
            if isinstance(exc, ProtocolGuidanceError):
                raise
            # Truncation at the max_tokens boundary is sticky: resampling the
            # same context with the same budget almost always truncates again
            # (observed in load tests — runtime-level retries didn't help).
            # Re-issue once with 1.5x budget so the closing tag can land.
            if not (exc.retryable and "incomplete" in str(exc)):
                raise
            return self._complete_once(messages, tools, int(self.max_output_tokens * 1.5))

    def _complete_once(
        self, messages: list[dict[str, Any]], tools: list[dict[str, Any]], max_output_tokens: int
    ) -> ModelTurn:
        request_messages = self._to_tagged_messages(messages)
        protocol_prompt = _tag_protocol_prompt(tools)
        if request_messages and request_messages[0].get("role") == "system":
            current_prompt = str(request_messages[0].get("content") or "")
            if "<tools>" not in current_prompt:
                request_messages[0]["content"] = current_prompt + "\n\n" + protocol_prompt
        else:
            request_messages.insert(0, {"role": "system", "content": protocol_prompt})
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": request_messages,
            "temperature": self.temperature,
            "top_p": self.top_p,
            "max_tokens": max_output_tokens,
        }
        if self.top_k > 0:
            # vLLM extension; plain OpenAI-compatible endpoints reject top_k.
            payload["top_k"] = self.top_k
        endpoint = f"{self.base_url.rstrip('/')}/chat/completions"
        request = urllib.request.Request(
            endpoint,
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
                body = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")[:600]
            raise ModelError(
                f"model HTTP {exc.code}: {detail}",
                retryable=exc.code == 429 or exc.code >= 500,
            ) from exc
        except (urllib.error.URLError, TimeoutError) as exc:
            raise ModelError(f"model transport error: {exc}", retryable=True) from exc
        except json.JSONDecodeError as exc:
            raise ModelError("model returned invalid JSON", retryable=True) from exc
        try:
            choice = body["choices"][0]
            message = choice["message"]
            raw_content = str(message.get("content") or "")
            calls, answer = parse_tagged_turn(raw_content)
        except (KeyError, IndexError, TypeError, ValueError, json.JSONDecodeError) as exc:
            # Output truncated at the max_tokens boundary ("length") leaves
            # dangling <tool_call>/<answer> tags; resampling will very likely
            # produce a complete turn, so treat truncation as retryable.
            finish = None
            try:
                finish = body["choices"][0].get("finish_reason")
            except (KeyError, IndexError, TypeError, AttributeError):
                pass
            if finish == "length":
                raise ModelError(f"tagged protocol error: {exc}", retryable=True) from exc
            # Non-truncation malformed turns: the training loop answers these
            # with an in-context guidance message, not a silent resample.
            guidance = (
                GUIDANCE_PARTIAL_TOOL_CALL
                if "incomplete tool_call" in str(exc)
                else GUIDANCE_NO_TOOL_NO_ANSWER
            )
            raise ProtocolGuidanceError(
                f"tagged protocol error: {exc}", guidance=guidance
            ) from exc
        usage = body.get("usage") or {}
        return ModelTurn(
            content=answer,
            tool_calls=calls,
            prompt_tokens=int(usage.get("prompt_tokens") or 0),
            completion_tokens=int(usage.get("completion_tokens") or 0),
            model=str(body.get("model") or self.model),
            raw_assistant_message={"role": "assistant", "content": raw_content},
        )

    def _to_tagged_messages(self, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Translate native Runtime observations into the RL model's text protocol.

        Runtime and persistence keep one provider-neutral message model. Only this
        boundary adapts tool observations, so local RL serving does not fork the
        Harness state machine or its trace/checkpoint semantics.
        """
        converted: list[dict[str, Any]] = []
        for message in messages:
            if message.get("role") == "tool":
                content = str(message.get("content") or "")
                converted.append(
                    {
                        "role": "user",
                        "content": f"<tool_response>\n{_to_training_observation(content, self.tool_output_max_chars)}\n</tool_response>",
                    }
                )
                continue
            converted.append(
                {
                    key: value
                    for key, value in message.items()
                    if key in {"role", "content", "reasoning_content"}
                }
            )
        return converted
