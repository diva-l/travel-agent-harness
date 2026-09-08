from __future__ import annotations

import json
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from .models import TaskState
from .redaction import redact


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
