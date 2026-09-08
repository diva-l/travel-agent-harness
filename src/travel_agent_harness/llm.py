from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any, Protocol

from .models import ModelTurn, ToolCall


class ModelError(RuntimeError):
    def __init__(self, message: str, *, retryable: bool = False) -> None:
        super().__init__(message)
        self.retryable = retryable


class ModelBackend(Protocol):
    @property
    def fingerprint(self) -> str: ...

    def complete(
        self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]
    ) -> ModelTurn: ...


@dataclass(slots=True)
class OpenAICompatibleModel:
    api_key: str
    base_url: str
    model: str
    timeout_seconds: float = 90.0
    max_output_tokens: int = 1800
    temperature: float = 0.1
    top_p: float = 1.0
    top_k: int = 0

    @property
    def fingerprint(self) -> str:
        return f"openai-compatible:{self.base_url.rstrip('/')}:{self.model}"

    def complete(
        self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]
    ) -> ModelTurn:
        endpoint = f"{self.base_url.rstrip('/')}/chat/completions"
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "tools": tools,
            "tool_choice": "auto",
            "temperature": self.temperature,
            "top_p": self.top_p,
            "max_tokens": self.max_output_tokens,
        }
        if self.top_k > 0:
            # vLLM extension; plain OpenAI-compatible endpoints reject top_k.
            payload["top_k"] = self.top_k
        if "deepseek.com" in self.base_url:
            payload["thinking"] = {"type": "disabled"}
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
            message = body["choices"][0]["message"]
        except (KeyError, IndexError, TypeError) as exc:
            raise ModelError("model response has no assistant message") from exc

        tool_calls: list[ToolCall] = []
        for raw_call in message.get("tool_calls") or []:
            function = raw_call.get("function") or {}
            raw_arguments = function.get("arguments") or "{}"
            try:
                arguments = json.loads(raw_arguments)
            except json.JSONDecodeError as exc:
                raise ModelError(
                    f"tool arguments are not valid JSON for {function.get('name', '<unknown>')}"
                ) from exc
            if not isinstance(arguments, dict):
                raise ModelError("tool arguments must be a JSON object")
            tool_calls.append(
                ToolCall(
                    call_id=str(raw_call.get("id") or f"call-{len(tool_calls)}"),
                    name=str(function.get("name") or ""),
                    arguments=arguments,
                )
            )

        usage = body.get("usage") or {}
        assistant_message: dict[str, Any] = {
            "role": "assistant",
            "content": message.get("content"),
        }
        if message.get("reasoning_content") is not None:
            assistant_message["reasoning_content"] = message["reasoning_content"]
        if message.get("tool_calls"):
            assistant_message["tool_calls"] = message["tool_calls"]
        return ModelTurn(
            content=message.get("content"),
            tool_calls=tool_calls,
            prompt_tokens=int(usage.get("prompt_tokens") or 0),
            completion_tokens=int(usage.get("completion_tokens") or 0),
            model=str(body.get("model") or self.model),
            raw_assistant_message=assistant_message,
        )


class ScriptedModel:
    """Deterministic backend for orchestration tests; it never calls an API."""

    def __init__(self, turns: list[ModelTurn], fingerprint: str = "scripted:v1") -> None:
        self._turns = list(turns)
        self._index = 0
        self._fingerprint = fingerprint

    @property
    def fingerprint(self) -> str:
        return self._fingerprint

    def complete(
        self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]
    ) -> ModelTurn:
        del messages, tools
        if self._index >= len(self._turns):
            raise ModelError("scripted model ran out of turns")
        turn = self._turns[self._index]
        self._index += 1
        if not turn.raw_assistant_message:
            if turn.tool_calls:
                turn.raw_assistant_message = {
                    "role": "assistant",
                    "content": turn.content,
                    "tool_calls": [
                        {
                            "id": call.call_id,
                            "type": "function",
                            "function": {
                                "name": call.name,
                                "arguments": json.dumps(call.arguments, ensure_ascii=False),
                            },
                        }
                        for call in turn.tool_calls
                    ],
                }
            else:
                turn.raw_assistant_message = {
                    "role": "assistant",
                    "content": turn.content,
                }
        return turn

