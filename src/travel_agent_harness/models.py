from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import StrEnum
from typing import Any


class TaskStatus(StrEnum):
    CREATED = "created"
    RUNNING = "running"
    WAITING_APPROVAL = "waiting_approval"
    COMPLETED = "completed"
    EXHAUSTED = "exhausted"
    FAILED = "failed"


@dataclass(slots=True)
class ToolCall:
    call_id: str
    name: str
    arguments: dict[str, Any]


@dataclass(slots=True)
class ModelTurn:
    content: str | None = None
    tool_calls: list[ToolCall] = field(default_factory=list)
    prompt_tokens: int = 0
    completion_tokens: int = 0
    model: str = "unknown"
    raw_assistant_message: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class TaskState:
    task_id: str
    objective: str
    messages: list[dict[str, Any]]
    status: TaskStatus = TaskStatus.CREATED
    step: int = 0
    checkpoint_seq: int = 0
    created_at: float = 0.0
    updated_at: float = 0.0
    elapsed_seconds: float = 0.0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    tool_calls: int = 0
    successful_tool_calls: int = 0
    tool_errors: int = 0
    validation_errors: int = 0
    seen_call_fingerprints: dict[str, int] = field(default_factory=dict)
    repeat_answer_chance_used: bool = False
    # Consecutive identical-round detection (training-loop parity): signature of
    # the round's tool calls + observations, and how many rounds in a row matched.
    last_round_signature: str = ""
    consecutive_same_rounds: int = 0
    pending_call: dict[str, Any] | None = None
    answer: str | None = None
    report: dict[str, Any] | None = None
    report_status: str = "not_requested"
    report_error: str | None = None
    error: str | None = None
    model_fingerprint: str = "unknown"
    forked_from: dict[str, Any] | None = None

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["status"] = self.status.value
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "TaskState":
        restored = dict(data)
        restored["status"] = TaskStatus(restored["status"])
        return cls(**restored)
