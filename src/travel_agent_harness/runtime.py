from __future__ import annotations

import json
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from hashlib import sha256
from typing import Any

from .config import HarnessConfig
from .llm import ModelBackend, ModelError
from .models import TaskState, TaskStatus, ToolCall
from .prompts import build_planner_system_prompt
from .store import SQLiteStore
from .tools import ToolRegistry, ToolValidationError

# Terminal-rule messages copied verbatim from the RL training loop
# (travel_agentic_rl/run_tool_loop_infer.py). The trained planner has seen
# these exact strings, so paraphrasing them would shift the distribution.
FORCE_ANSWER_MESSAGE = "信息已经足够。禁止继续调用工具，请直接给最终方案，并严格用<answer>...</answer>输出。"
REPEAT_ANSWER_CHANCE_MESSAGE = (
    "检测到你连续重复了相同工具调用与相同结果。不要重复循环。"
    "现在给你最后一次机会：禁止继续调用工具，"
    "请直接输出最终结果，并严格使用<answer>...</answer>。"
)


class AgentRuntime:
    def __init__(
        self,
        config: HarnessConfig,
        model: ModelBackend,
        tools: ToolRegistry,
        store: SQLiteStore,
    ) -> None:
        self.config = config
        self.model = model
        self.tools = tools
        self.store = store

    def create(self, objective: str) -> TaskState:
        now = time.time()
        system_prompt = build_planner_system_prompt(
            protocol=self.config.model_protocol,
            max_tool_rounds=self.config.max_steps,
            tools=self.tools.api_schemas(),
            current_date=self.config.current_date,
        )
        state = TaskState(
            task_id=uuid.uuid4().hex,
            objective=objective,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": objective},
            ],
            created_at=now,
            updated_at=now,
            model_fingerprint=self.model.fingerprint,
        )
        self.store.save(state)
        self._trace(state, "task_created", {"objective": objective, "model": self.model.fingerprint})
        return state

    def run(self, state: TaskState) -> TaskState:
        if state.status in {TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.EXHAUSTED}:
            return state
        if state.status == TaskStatus.WAITING_APPROVAL:
            return state
        started = time.monotonic()
        state.status = TaskStatus.RUNNING
        self._transition(state, "running")

        while True:
            state.elapsed_seconds += time.monotonic() - started
            started = time.monotonic()
            budget_error = self._budget_error(state)
            if budget_error:
                state.status = TaskStatus.EXHAUSTED
                state.error = budget_error
                self._trace(state, "budget_exhausted", {"reason": budget_error})
                self.store.save(state)
                return state

            state.step += 1
            self._trace(
                state,
                "model_request",
                {
                    "message_count": len(state.messages),
                    "available_tools": [item["function"]["name"] for item in self.tools.api_schemas()],
                    "budget": self._budget_snapshot(state),
                },
            )
            model_started = time.monotonic()
            try:
                turn = self._complete_with_retry(state)
            except ModelError as exc:
                state.status = TaskStatus.FAILED
                state.error = str(exc)
                self._trace(state, "model_failed", {"error": str(exc)})
                self.store.save(state)
                return state
            model_duration_ms = round((time.monotonic() - model_started) * 1000, 1)

            state.prompt_tokens += turn.prompt_tokens
            state.completion_tokens += turn.completion_tokens
            state.messages.append(turn.raw_assistant_message)
            self._trace(
                state,
                "model_response",
                {
                    "model": turn.model,
                    "tool_calls": [call.name for call in turn.tool_calls],
                    "has_content": bool(turn.content),
                    "duration_ms": model_duration_ms,
                    "usage": {
                        "prompt_tokens": turn.prompt_tokens,
                        "completion_tokens": turn.completion_tokens,
                    },
                },
            )

            if turn.tool_calls:
                # Phase 1 (sequential): budget / repeat / schema / approval checks.
                # No messages are appended here so the merge phase can preserve
                # the exact call order required by the training contract.
                plans: list[tuple[ToolCall, str, dict[str, Any]]] = []
                control: tuple[str, ToolCall | None] | None = None
                for call in turn.tool_calls:
                    action, meta = self._prepare_tool_call(state, call)
                    if action in {"paused", "exhausted"}:
                        control = (action, call if action == "paused" else None)
                        break
                    plans.append((call, action, meta))

                # Phase 2 (parallel): execute the ready calls only.
                ready = [
                    (index, call)
                    for index, (call, action, _) in enumerate(plans)
                    if action == "ready"
                ]
                outcomes: dict[int, tuple[dict[str, Any], str, float]] = {}
                if ready:
                    for _, call in ready:
                        self._trace(
                            state,
                            "tool_started",
                            {"call_id": call.call_id, "tool": call.name, "arguments": call.arguments, "approved": False},
                        )
                    if len(ready) == 1 or not self.config.parallel_tool_calls:
                        results = [self._call_tool(call) for _, call in ready]
                    else:
                        with ThreadPoolExecutor(max_workers=len(ready)) as pool:
                            results = list(pool.map(lambda item: self._call_tool(item[1]), ready))
                    outcomes = {index: result for (index, _), result in zip(ready, results)}

                # Phase 3 (ordered): counters, traces and tool messages land in
                # the original call order regardless of execution timing.
                for index, (call, action, meta) in enumerate(plans):
                    if action == "ready":
                        envelope, kind, duration_ms = outcomes[index]
                        self._apply_tool_outcome(state, call, envelope, kind, duration_ms)
                    elif action == "blocked":
                        state.tool_errors += 1
                        self._append_tool_message(
                            state, call, {"ok": False, "error": "repeated identical tool call blocked"}
                        )
                        self._trace(
                            state,
                            "tool_blocked",
                            {"tool": call.name, "reason": "repeat_limit", "count": meta["count"]},
                        )
                        # Training-loop parity: instead of letting the planner
                        # spin on blocked repeats, offer one forced-answer
                        # chance per task (mirrors the repeated-loop branch of
                        # run_tool_loop_infer.py).
                        if self.config.repeat_answer_chance and not state.repeat_answer_chance_used:
                            state.repeat_answer_chance_used = True
                            state.messages.append(
                                {"role": "user", "content": REPEAT_ANSWER_CHANCE_MESSAGE}
                            )
                            self._trace(
                                state,
                                "repeat_answer_chance",
                                {"step": state.step, "tool": call.name},
                            )
                    elif action == "invalid":
                        state.tool_errors += 1
                        state.validation_errors += 1
                        self._append_tool_message(state, call, {"ok": False, "error": meta["error"]})
                        self._trace(
                            state,
                            "tool_failed",
                            {"call_id": call.call_id, "tool": call.name, "error": meta["error"]},
                        )

                if control is not None:
                    action, pending = control
                    if action == "paused" and pending is not None:
                        state.pending_call = {
                            "call_id": pending.call_id,
                            "name": pending.name,
                            "arguments": pending.arguments,
                        }
                        state.status = TaskStatus.WAITING_APPROVAL
                        self._trace(
                            state,
                            "approval_required",
                            {"call_id": pending.call_id, "tool": pending.name, "arguments": pending.arguments},
                        )
                    else:
                        state.status = TaskStatus.EXHAUSTED
                        state.error = "tool-call budget exhausted"
                        self._trace(state, "budget_exhausted", {"reason": state.error})
                    state.elapsed_seconds += time.monotonic() - started
                    self.store.save(state)
                    return state

                state.elapsed_seconds += time.monotonic() - started
                started = time.monotonic()
                # Training-loop parity (max_same_tool_call_rounds=3): a round
                # whose calls AND observations are identical to the previous
                # round is a loop. The third consecutive identical round gets
                # one forced-answer chance; if it still loops, the task stops.
                if self.config.repeat_answer_chance:
                    round_signature = sha256(
                        json.dumps(
                            {
                                "calls": [
                                    {"name": c.name, "arguments": c.arguments}
                                    for c in turn.tool_calls
                                ],
                                "results": [
                                    m.get("content", "")
                                    for m in state.messages
                                    if m.get("role") == "tool"
                                ][-len(turn.tool_calls):],
                            },
                            ensure_ascii=False,
                            sort_keys=True,
                        ).encode("utf-8")
                    ).hexdigest()
                    if round_signature == state.last_round_signature:
                        state.consecutive_same_rounds += 1
                    else:
                        state.consecutive_same_rounds = 1
                        state.last_round_signature = round_signature
                    if state.consecutive_same_rounds >= 3:
                        if not state.repeat_answer_chance_used:
                            state.repeat_answer_chance_used = True
                            state.messages.append(
                                {"role": "user", "content": REPEAT_ANSWER_CHANCE_MESSAGE}
                            )
                            self._trace(
                                state,
                                "repeat_answer_chance",
                                {"step": state.step, "reason": "consecutive_identical_rounds"},
                            )
                        else:
                            state.status = TaskStatus.EXHAUSTED
                            state.error = "repeated_tool_call_loop"
                            self._trace(state, "budget_exhausted", {"reason": state.error})
                            state.elapsed_seconds += time.monotonic() - started
                            self.store.save(state)
                            return state
                # Training-loop parity (--force_answer_after_turns 12): past the
                # threshold, every tool round ends with the forced-answer
                # instruction so the policy closes instead of exhausting.
                if (
                    self.config.force_answer_after_steps > 0
                    and state.step >= self.config.force_answer_after_steps
                ):
                    state.messages.append({"role": "user", "content": FORCE_ANSWER_MESSAGE})
                    self._trace(state, "force_answer_injected", {"step": state.step})
                self.store.save(state)
                continue

            answer = (turn.content or "").strip()
            if not answer:
                state.messages.append(
                    {"role": "user", "content": "你的上一轮没有输出。请继续，并遵守工具与证据规则。"}
                )
                self._trace(state, "final_rejected", {"reason": "empty answer"})
                self.store.save(state)
                continue
            if self.config.require_tool_before_final and state.successful_tool_calls == 0:
                # Training-loop parity (tool_first_enforce): the tagged planner
                # gets the verbatim training message; native-protocol planners
                # keep the harness wording.
                if self.config.model_protocol == "tagged":
                    from .protocol import GUIDANCE_TOOL_FIRST

                    state.messages.append({"role": "user", "content": GUIDANCE_TOOL_FIRST})
                else:
                    state.messages.append(
                        {"role": "user", "content": "最终答案被证据门禁拒绝：请至少成功调用一个工具后再总结。"}
                    )
                self._trace(state, "final_rejected", {"reason": "no successful tool evidence"})
                self.store.save(state)
                continue

            state.answer = answer
            state.status = TaskStatus.COMPLETED
            state.elapsed_seconds += time.monotonic() - started
            self._transition(state, "completed")
            self.store.save(state)
            return state

    def resume(self, task_id: str) -> TaskState:
        state = self.store.load(task_id)
        return self.run(state)

    def decide_approval(self, task_id: str, call_id: str, approved: bool) -> TaskState:
        state = self.store.load(task_id)
        pending = state.pending_call
        if state.status != TaskStatus.WAITING_APPROVAL or not pending:
            raise ValueError("task has no pending approval")
        if pending["call_id"] != call_id:
            raise ValueError(f"pending call id is {pending['call_id']}")
        call = ToolCall(
            call_id=pending["call_id"],
            name=pending["name"],
            arguments=pending["arguments"],
        )
        state.pending_call = None
        state.status = TaskStatus.RUNNING
        if approved:
            self._execute_tool(state, call, approval_granted=True)
            self._trace(state, "approval_granted", {"call_id": call_id, "tool": call.name})
        else:
            self._append_tool_message(state, call, {"ok": False, "error": "rejected by human"})
            state.tool_errors += 1
            self._trace(state, "approval_rejected", {"call_id": call_id, "tool": call.name})
        self.store.save(state)
        return self.run(state)

    def fork(self, task_id: str, checkpoint_seq: int) -> TaskState:
        source = self.store.load_checkpoint(task_id, checkpoint_seq)
        now = time.time()
        forked = replace(
            source,
            task_id=uuid.uuid4().hex,
            status=TaskStatus.CREATED,
            checkpoint_seq=0,
            created_at=now,
            updated_at=now,
            answer=None,
            report=None,
            report_status="not_requested",
            report_error=None,
            error=None,
            pending_call=None,
            forked_from={"task_id": task_id, "checkpoint_seq": checkpoint_seq},
            model_fingerprint=self.model.fingerprint,
        )
        self.store.save(forked)
        self._trace(forked, "task_forked", forked.forked_from or {})
        return forked

    def _complete_with_retry(self, state: TaskState):
        from .protocol import ProtocolGuidanceError

        attempts = self.config.model_retries + 1
        for attempt in range(1, attempts + 1):
            try:
                return self.model.complete(state.messages, self.tools.api_schemas())
            except ProtocolGuidanceError as exc:
                # Training-loop parity: malformed tagged turns get the verbatim
                # guidance message appended to the conversation, then the model
                # is re-asked with that feedback in context.
                state.messages.append({"role": "user", "content": exc.guidance})
                self._trace(
                    state,
                    "protocol_guidance",
                    {"attempt": attempt, "error": str(exc)},
                )
                if attempt == attempts:
                    raise ModelError(str(exc), retryable=False) from exc
            except ModelError as exc:
                self._trace(
                    state,
                    "model_error",
                    {"attempt": attempt, "retryable": exc.retryable, "error": str(exc)},
                )
                if not exc.retryable or attempt == attempts:
                    raise
                time.sleep(min(2 ** (attempt - 1), 4))
        raise AssertionError("unreachable")

    def _prepare_tool_call(self, state: TaskState, call: ToolCall) -> tuple[str, dict[str, Any]]:
        """Sequential gate before execution: budget, repeat limit, schema,
        approval. Pure check — messages/traces are deferred to the ordered
        merge phase. Returns (action, meta) with action in
        ready / blocked / invalid / paused / exhausted."""
        if state.tool_calls >= self.config.max_tool_calls:
            return "exhausted", {}
        state.tool_calls += 1
        fingerprint = sha256(
            f"{call.name}:{json.dumps(call.arguments, sort_keys=True, ensure_ascii=False)}".encode("utf-8")
        ).hexdigest()[:16]
        repeat_count = state.seen_call_fingerprints.get(fingerprint, 0) + 1
        state.seen_call_fingerprints[fingerprint] = repeat_count
        if repeat_count > self.config.repeat_call_limit:
            return "blocked", {"count": repeat_count}
        try:
            spec = self.tools.get(call.name)
        except ToolValidationError as exc:
            return "invalid", {"error": str(exc)}
        if spec.requires_approval:
            return "paused", {}
        return "ready", {}

    def _call_tool(self, call: ToolCall) -> tuple[dict[str, Any], str, float]:
        """Pure execution: no state mutation, safe to run in worker threads."""
        started = time.monotonic()
        try:
            result = self.tools.execute(call.name, call.arguments)
            envelope: dict[str, Any] = {"ok": True, "result": result}
            kind = "ok"
        except ToolValidationError as exc:
            envelope = {"ok": False, "error": str(exc)}
            kind = "validation_error"
        except (TypeError, ValueError) as exc:
            envelope = {"ok": False, "error": str(exc)}
            kind = "error"
        duration_ms = round((time.monotonic() - started) * 1000, 1)
        return envelope, kind, duration_ms

    def _apply_tool_outcome(
        self, state: TaskState, call: ToolCall, envelope: dict[str, Any], kind: str, duration_ms: float
    ) -> None:
        if kind == "ok":
            state.successful_tool_calls += 1
            self._trace(
                state,
                "tool_succeeded",
                {
                    "call_id": call.call_id,
                    "tool": call.name,
                    "duration_ms": duration_ms,
                    "observation": envelope["result"],
                },
            )
        else:
            state.tool_errors += 1
            if kind == "validation_error":
                state.validation_errors += 1
            self._trace(
                state,
                "tool_failed",
                {
                    "call_id": call.call_id,
                    "tool": call.name,
                    "duration_ms": duration_ms,
                    "error": envelope["error"],
                },
            )
        self._append_tool_message(state, call, envelope)

    def _execute_tool(self, state: TaskState, call: ToolCall, *, approval_granted: bool) -> None:
        self._trace(
            state,
            "tool_started",
            {"call_id": call.call_id, "tool": call.name, "arguments": call.arguments, "approved": approval_granted},
        )
        envelope, kind, duration_ms = self._call_tool(call)
        self._apply_tool_outcome(state, call, envelope, kind, duration_ms)

    def _append_tool_message(self, state: TaskState, call: ToolCall, payload: dict[str, Any]) -> None:
        serialized = json.dumps(payload, ensure_ascii=False)
        # Tagged mode: observation rendering (markdown conversion + head/tail
        # truncation) happens at the protocol boundary, so the stored envelope
        # must stay intact — truncating the JSON here would destroy the
        # structure before the renderer sees it.
        if (
            self.config.model_protocol != "tagged"
            and len(serialized) > self.config.max_tool_output_chars
        ):
            serialized = json.dumps(
                {
                    "ok": payload.get("ok", False),
                    "truncated": True,
                    "original_chars": len(serialized),
                    "content_prefix": serialized[: self.config.max_tool_output_chars],
                },
                ensure_ascii=False,
            )
        state.messages.append(
            {
                "role": "tool",
                "tool_call_id": call.call_id,
                "name": call.name,
                "content": serialized,
            }
        )

    def _budget_error(self, state: TaskState) -> str | None:
        if state.step >= self.config.max_steps:
            return f"step budget exhausted ({state.step}/{self.config.max_steps})"
        if state.elapsed_seconds >= self.config.max_seconds:
            return f"time budget exhausted ({state.elapsed_seconds:.2f}s/{self.config.max_seconds:.2f}s)"
        if state.total_tokens >= self.config.max_total_tokens:
            return f"token budget exhausted ({state.total_tokens}/{self.config.max_total_tokens})"
        return None

    def _budget_snapshot(self, state: TaskState) -> dict[str, Any]:
        return {
            "step": [state.step, self.config.max_steps],
            "elapsed_seconds": [round(state.elapsed_seconds, 3), self.config.max_seconds],
            "tokens": [state.total_tokens, self.config.max_total_tokens],
            "tool_calls": [state.tool_calls, self.config.max_tool_calls],
        }

    def _transition(self, state: TaskState, to_status: str) -> None:
        self._trace(state, "state_transition", {"to": to_status})

    def _trace(self, state: TaskState, kind: str, payload: dict[str, Any]) -> None:
        if self.config.trace_payloads:
            self.store.append_trace(state.task_id, state.step, kind, payload)
        else:
            self.store.append_trace(state.task_id, state.step, kind, {"redacted": True})
