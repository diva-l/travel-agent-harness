from __future__ import annotations

import re
import threading
from concurrent.futures import Future, ThreadPoolExecutor
from typing import Any

from ..config import HarnessConfig
from ..harness import TravelHarness
from ..models import TaskState, TaskStatus
from .schemas import TripPlanRequest


TERMINAL_STATUSES = {
    TaskStatus.COMPLETED,
    TaskStatus.EXHAUSTED,
    TaskStatus.FAILED,
}


class QueueFullError(RuntimeError):
    """Submission rejected: workers and the bounded queue are all busy."""


class TaskService:
    """Thin async boundary around the single AgentRuntime execution path."""

    def __init__(
        self,
        harness: TravelHarness,
        config: HarnessConfig,
        *,
        max_workers: int = 2,
        max_queue: int = 32,
    ) -> None:
        self.harness = harness
        self.config = config
        self._executor = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="travel-agent")
        self._lock = threading.Lock()
        self._active: dict[str, Future[TaskState]] = {}
        # Backpressure: cap in-flight submissions (running + queued) so
        # overload fails fast with 503 instead of queueing unboundedly.
        self._slots = threading.BoundedSemaphore(max_workers + max(0, max_queue))

    def submit(self, request: TripPlanRequest) -> TaskState:
        state = self.harness.runtime.create(request.to_objective())
        state.report_status = "pending" if self.harness.reporter else "disabled"
        self.harness.runtime.store.save(state, checkpoint=False)
        self._schedule(
            state.task_id,
            lambda: self._run_pipeline(lambda: self.harness.runtime.run(state)),
        )
        return state

    def get(self, task_id: str) -> TaskState:
        return self.harness.runtime.store.load(task_id)

    def trace(self, task_id: str, after_id: int = 0) -> list[dict[str, Any]]:
        self.get(task_id)
        return self.harness.runtime.store.trace(task_id, after_id=after_id)

    def checkpoints(self, task_id: str) -> list[dict[str, Any]]:
        self.get(task_id)
        return self.harness.runtime.store.list_checkpoints(task_id)

    def metrics(self) -> dict[str, Any]:
        return self.harness.runtime.store.metrics_summary()

    def fork(self, task_id: str, checkpoint_seq: int, *, run: bool) -> TaskState:
        self.get(task_id)
        forked = self.harness.fork(task_id, checkpoint_seq)
        forked.report_status = "pending" if run and self.harness.reporter else "disabled"
        self.harness.runtime.store.save(forked, checkpoint=False)
        if run:
            self._schedule(
                forked.task_id,
                lambda: self._run_pipeline(lambda: self.harness.runtime.run(forked)),
            )
        return forked

    def resume(self, task_id: str) -> TaskState:
        state = self.get(task_id)
        if state.status in TERMINAL_STATUSES:
            return state
        if state.status == TaskStatus.WAITING_APPROVAL:
            raise ValueError("任务正在等待人工审批，请先处理待审批工具调用")
        self._schedule(
            task_id,
            lambda: self._run_pipeline(lambda: self.harness.resume(task_id)),
        )
        return state

    def decide(self, task_id: str, call_id: str, approved: bool) -> TaskState:
        state = self.get(task_id)
        if state.status != TaskStatus.WAITING_APPROVAL:
            raise ValueError("任务当前没有待审批工具调用")
        self._schedule(
            task_id,
            lambda: self._run_pipeline(
                lambda: self.harness.runtime.decide_approval(task_id, call_id, approved)
            ),
        )
        return state

    def task_view(self, state: TaskState) -> dict[str, Any]:
        return {
            "task_id": state.task_id,
            "status": state.status.value,
            "objective": state.objective,
            "answer": state.answer,
            "report": state.report,
            "report_status": state.report_status,
            "report_error": state.report_error,
            "error": state.error,
            "pending_call": state.pending_call,
            "model": state.model_fingerprint,
            "report_model": self.harness.reporter.fingerprint if self.harness.reporter else None,
            "checkpoint_seq": state.checkpoint_seq,
            "metrics": {
                "step": state.step,
                "elapsed_seconds": round(state.elapsed_seconds, 3),
                "total_tokens": state.total_tokens,
                "tool_calls": state.tool_calls,
                "successful_tool_calls": state.successful_tool_calls,
                "tool_errors": state.tool_errors,
                "validation_errors": state.validation_errors,
            },
            "limits": {
                "max_steps": self.config.max_steps,
                "max_seconds": self.config.max_seconds,
                "max_total_tokens": self.config.max_total_tokens,
                "max_tool_calls": self.config.max_tool_calls,
            },
            "guardrails": {
                "schema_validation": {
                    "enabled": True,
                    "passed": state.validation_errors == 0,
                    "validation_errors": state.validation_errors,
                },
                "evidence_gate": {
                    "required": self.config.require_tool_before_final,
                    "passed": (
                        state.successful_tool_calls > 0
                        if self.config.require_tool_before_final
                        else True
                    ),
                    "successful_tools": state.successful_tool_calls,
                },
                "repeat_call_limit": self.config.repeat_call_limit,
                "tool_output_limit_chars": self.config.max_tool_output_chars,
                "trace_payloads": self.config.trace_payloads,
            },
            "created_at": state.created_at,
            "updated_at": state.updated_at,
        }

    def safe_config(self) -> dict[str, Any]:
        return {
            "model": self.config.model,
            "report_model": self.config.report_model if self.harness.reporter else None,
            "protocol": self.config.model_protocol,
            "product_pipeline": [
                "agentic_rl_planner",
                "report_model",
                "interactive_route_view",
            ],
            "runtime": {
                "engine": "AgentRuntime",
                "state_store": "SQLite / WAL",
                "checkpoint": "per committed state",
                "trace_transport": "SQLite + SSE",
                "final_policy": "tool evidence required",
                "tool_provider": self.config.tool_provider,
            },
            "state_machine": [
                "created",
                "running",
                "waiting_approval",
                "completed",
                "exhausted",
                "failed",
            ],
            "capabilities": [
                "bounded_loop",
                "schema_guardrails",
                "checkpoint_resume",
                "trace_stream",
                "human_approval",
                "checkpoint_fork",
                "offline_evaluation",
                "runtime_metrics",
                "optional_api_auth",
                "planner_report_separation",
                "interactive_route_report",
            ],
            "tools": self.harness.runtime.tools.catalog(),
            "limits": {
                "max_steps": self.config.max_steps,
                "max_seconds": self.config.max_seconds,
                "max_total_tokens": self.config.max_total_tokens,
                "max_tool_calls": self.config.max_tool_calls,
                "max_tool_output_chars": self.config.max_tool_output_chars,
            },
        }

    def shutdown(self) -> None:
        self._executor.shutdown(wait=False, cancel_futures=False)

    @staticmethod
    def is_stream_terminal(state: TaskState) -> bool:
        if state.status not in TERMINAL_STATUSES:
            return False
        return state.report_status not in {"pending", "running"}

    def _run_pipeline(self, operation) -> TaskState:
        state = operation()
        if state.status != TaskStatus.COMPLETED or not self.harness.reporter:
            if state.report_status == "pending":
                state.report_status = "skipped"
                self.harness.runtime.store.save(state, checkpoint=False)
            return state
        if state.report is not None and state.report_status == "completed":
            return state

        state.report_status = "running"
        state.report_error = None
        self.harness.runtime.store.append_trace(
            state.task_id,
            state.step,
            "report_started",
            {"model": self.harness.reporter.fingerprint, "input": "planner_answer+tool_evidence"},
        )
        self.harness.runtime.store.save(state, checkpoint=False)
        try:
            evidence = self._tool_evidence(state)
            state.report = self.harness.reporter.generate(
                objective=state.objective,
                planner_answer=state.answer or "",
                evidence=evidence,
            )
            photo_count = self._enrich_report_stops(state)
            state.report_status = "completed"
            stop_count = sum(len(day["stops"]) for day in state.report["days"])
            self.harness.runtime.store.append_trace(
                state.task_id,
                state.step,
                "report_completed",
                {
                    "model": self.harness.reporter.fingerprint,
                    "days": len(state.report["days"]),
                    "stops": stop_count,
                    "map_kind": state.report["map_kind"],
                    "photos": photo_count,
                },
            )
        except Exception as exc:
            state.report_status = "failed"
            state.report_error = str(exc)
            self.harness.runtime.store.append_trace(
                state.task_id,
                state.step,
                "report_failed",
                {"model": self.harness.reporter.fingerprint, "error": str(exc)},
            )
        self.harness.runtime.store.save(state, checkpoint=False)
        return state

    def _enrich_report_stops(self, state: TaskState) -> int:
        """Attach real POI addresses/photos when the AMap provider is configured.

        Best-effort: any lookup failure leaves the stop untouched, so the report
        still renders with its text content alone.
        """
        if not state.report or self.config.tool_provider != "amap" or not self.config.amap_api_key:
            return 0
        from ..amap import AmapProvider

        region = self._objective_destination(state.objective)
        provider = AmapProvider(self.config.amap_api_key, timeout=self.config.amap_timeout_seconds)
        enriched = 0
        stops = [stop for day in state.report["days"] for stop in day["stops"]][:16]
        for stop in stops:
            if stop.get("image_url") or stop.get("category") == "transport":
                continue
            try:
                snapshot = provider.poi_snapshot(stop["name"], region)
            except Exception:
                continue
            if snapshot.get("image_url"):
                stop["image_url"] = snapshot["image_url"]
                enriched += 1
            if snapshot.get("address") and not stop.get("address"):
                stop["address"] = snapshot["address"]
        return enriched

    @staticmethod
    def _objective_destination(objective: str) -> str:
        match = re.search(r"目的地[:：]\s*([^\n，,]+)", objective or "")
        return match.group(1).strip() if match else ""

    @staticmethod
    def _tool_evidence(state: TaskState) -> list[dict[str, Any]]:
        evidence: list[dict[str, Any]] = []
        budget = 12_000
        for message in state.messages:
            if message.get("role") != "tool":
                continue
            content = str(message.get("content") or "")
            if budget <= 0:
                break
            clipped = content[:budget]
            budget -= len(clipped)
            evidence.append(
                {
                    "tool": message.get("name") or "unknown",
                    "call_id": message.get("tool_call_id"),
                    "content": clipped,
                }
            )
        return evidence

    def _schedule(self, task_id: str, operation) -> None:
        with self._lock:
            current = self._active.get(task_id)
            if current and not current.done():
                raise ValueError("任务已在执行中")
            if not self._slots.acquire(blocking=False):
                raise QueueFullError("服务繁忙：当前执行与排队名额已满，请稍后重试")
            try:
                future = self._executor.submit(self._guarded_run, task_id, operation)
            except Exception:
                self._slots.release()
                raise
            self._active[task_id] = future
        future.add_done_callback(lambda done: self._clear_active(task_id, done))

    def _clear_active(self, task_id: str, future: Future[TaskState]) -> None:
        try:
            with self._lock:
                if self._active.get(task_id) is future:
                    self._active.pop(task_id, None)
        finally:
            self._slots.release()

    def _guarded_run(self, task_id: str, operation) -> TaskState:
        try:
            return operation()
        except Exception as exc:  # API workers must persist unexpected terminal failures.
            state = self.harness.runtime.store.load(task_id)
            state.status = TaskStatus.FAILED
            state.error = f"unexpected runtime error: {exc}"
            self.harness.runtime.store.append_trace(
                task_id,
                state.step,
                "runtime_failed",
                {"error": str(exc)},
            )
            self.harness.runtime.store.save(state)
            return state
