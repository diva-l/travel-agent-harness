from __future__ import annotations

import json
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from .models import TaskState
from .redaction import redact


def _distribution(values: Iterator[float]) -> dict[str, float]:
    """avg / p50 / max summary for a numeric series (empty series -> zeros)."""
    ordered = sorted(float(value) for value in values)
    if not ordered:
        return {"avg": 0.0, "p50": 0.0, "max": 0.0}
    mid = len(ordered) // 2
    p50 = ordered[mid] if len(ordered) % 2 else (ordered[mid - 1] + ordered[mid]) / 2
    return {
        "avg": round(sum(ordered) / len(ordered), 3),
        "p50": round(p50, 3),
        "max": round(ordered[-1], 3),
    }


class SQLiteStore:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=15)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=WAL")
        return connection

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        """Commit or roll back and always release the Windows file handle."""
        connection = self._connect()
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def _initialize(self) -> None:
        with self._connection() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS tasks (
                    task_id TEXT PRIMARY KEY,
                    status TEXT NOT NULL,
                    updated_at REAL NOT NULL,
                    state_json TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS checkpoints (
                    task_id TEXT NOT NULL,
                    seq INTEGER NOT NULL,
                    created_at REAL NOT NULL,
                    state_json TEXT NOT NULL,
                    PRIMARY KEY (task_id, seq)
                );
                CREATE TABLE IF NOT EXISTS traces (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    task_id TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    step INTEGER NOT NULL,
                    kind TEXT NOT NULL,
                    payload_json TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_traces_task_id ON traces(task_id, id);
                """
            )

    def save(self, state: TaskState, *, checkpoint: bool = True) -> None:
        now = time.time()
        state.updated_at = now
        if checkpoint:
            state.checkpoint_seq += 1
        serialized = json.dumps(state.to_dict(), ensure_ascii=False, separators=(",", ":"))
        with self._connection() as connection:
            connection.execute(
                """
                INSERT INTO tasks(task_id, status, updated_at, state_json)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(task_id) DO UPDATE SET
                    status=excluded.status,
                    updated_at=excluded.updated_at,
                    state_json=excluded.state_json
                """,
                (state.task_id, state.status.value, now, serialized),
            )
            if checkpoint:
                connection.execute(
                    """
                    INSERT INTO checkpoints(task_id, seq, created_at, state_json)
                    VALUES (?, ?, ?, ?)
                    """,
                    (state.task_id, state.checkpoint_seq, now, serialized),
                )

    def load(self, task_id: str) -> TaskState:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT state_json FROM tasks WHERE task_id = ?", (task_id,)
            ).fetchone()
        if row is None:
            raise KeyError(f"task not found: {task_id}")
        return TaskState.from_dict(json.loads(row["state_json"]))

    def load_checkpoint(self, task_id: str, seq: int) -> TaskState:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT state_json FROM checkpoints WHERE task_id = ? AND seq = ?",
                (task_id, seq),
            ).fetchone()
        if row is None:
            raise KeyError(f"checkpoint not found: {task_id}@{seq}")
        return TaskState.from_dict(json.loads(row["state_json"]))

    def list_checkpoints(self, task_id: str) -> list[dict[str, Any]]:
        with self._connection() as connection:
            rows = connection.execute(
                """
                SELECT seq, created_at, json_extract(state_json, '$.status') AS status,
                       json_extract(state_json, '$.step') AS step
                FROM checkpoints WHERE task_id = ? ORDER BY seq
                """,
                (task_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def append_trace(
        self, task_id: str, step: int, kind: str, payload: dict[str, Any]
    ) -> None:
        safe_payload = redact(payload)
        with self._connection() as connection:
            connection.execute(
                """
                INSERT INTO traces(task_id, created_at, step, kind, payload_json)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    task_id,
                    time.time(),
                    step,
                    kind,
                    json.dumps(safe_payload, ensure_ascii=False, separators=(",", ":")),
                ),
            )

    def metrics_summary(self) -> dict[str, Any]:
        """Aggregate runtime metrics over all persisted tasks (observability).

        Reads only the tasks/traces tables — no model involvement, so the
        numbers are identical whichever planner backend produced the runs.
        """
        with self._connection() as connection:
            status_counts = {
                row["status"]: row["n"]
                for row in connection.execute(
                    "SELECT status, COUNT(*) AS n FROM tasks GROUP BY status"
                ).fetchall()
            }
            rows = [
                dict(row)
                for row in connection.execute(
                    """
                    SELECT json_extract(state_json, '$.status') AS status,
                           json_extract(state_json, '$.step') AS step,
                           json_extract(state_json, '$.elapsed_seconds') AS elapsed_seconds,
                           json_extract(state_json, '$.prompt_tokens') AS prompt_tokens,
                           json_extract(state_json, '$.completion_tokens') AS completion_tokens,
                           json_extract(state_json, '$.tool_calls') AS tool_calls,
                           json_extract(state_json, '$.successful_tool_calls') AS successful_tool_calls,
                           json_extract(state_json, '$.tool_errors') AS tool_errors,
                           json_extract(state_json, '$.validation_errors') AS validation_errors,
                           json_extract(state_json, '$.report_status') AS report_status
                    FROM tasks
                    """
                ).fetchall()
            ]
            tool_usage = {
                row["tool"]: row["n"]
                for row in connection.execute(
                    """
                    SELECT json_extract(payload_json, '$.tool') AS tool, COUNT(*) AS n
                    FROM traces WHERE kind = 'tool_succeeded'
                    GROUP BY tool ORDER BY n DESC
                    """
                ).fetchall()
                if row["tool"]
            }
        terminal_statuses = {"completed", "exhausted", "failed"}
        finished = [row for row in rows if row["status"] in terminal_statuses]
        completed = status_counts.get("completed", 0)
        return {
            "tasks_total": len(rows),
            "tasks_by_status": status_counts,
            "terminal_tasks": len(finished),
            # Success rate is measured over terminal tasks only: tasks still
            # running/queued would drag it down without saying anything.
            "success_rate": round(completed / len(finished), 4) if finished else None,
            "steps": _distribution(row["step"] or 0 for row in finished),
            "tokens": {
                "prompt_total": sum(row["prompt_tokens"] or 0 for row in rows),
                "completion_total": sum(row["completion_tokens"] or 0 for row in rows),
                "per_task_avg": round(
                    sum((row["prompt_tokens"] or 0) + (row["completion_tokens"] or 0) for row in finished)
                    / len(finished),
                    1,
                )
                if finished
                else 0.0,
            },
            "tool_calls": {
                "total": sum(row["tool_calls"] or 0 for row in rows),
                "successful": sum(row["successful_tool_calls"] or 0 for row in rows),
                "errors": sum(row["tool_errors"] or 0 for row in rows),
                "validation_errors": sum(row["validation_errors"] or 0 for row in rows),
            },
            "elapsed_seconds": _distribution(row["elapsed_seconds"] or 0.0 for row in finished),
            "tool_usage": tool_usage,
            "reports": {
                status: sum(1 for row in rows if row["report_status"] == status)
                for status in ("completed", "failed", "skipped", "pending", "running")
            },
        }

    def trace(self, task_id: str, after_id: int = 0) -> list[dict[str, Any]]:
        with self._connection() as connection:
            rows = connection.execute(
                """
                SELECT id, created_at, step, kind, payload_json
                FROM traces WHERE task_id = ? AND id > ? ORDER BY id
                """,
                (task_id, after_id),
            ).fetchall()
        return [
            {
                "id": row["id"],
                "created_at": row["created_at"],
                "step": row["step"],
                "kind": row["kind"],
                "payload": json.loads(row["payload_json"]),
            }
            for row in rows
        ]
