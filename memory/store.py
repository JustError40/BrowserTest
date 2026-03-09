"""Persistent SQLite store for task history."""

from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any

import aiosqlite
from pydantic import BaseModel


class TaskRecord(BaseModel):
    """A single completed-task record persisted in the SQLite store."""

    id: int | None = None
    task: str
    result: str
    steps_summary: str
    created_at: datetime = None  # type: ignore[assignment]

    def model_post_init(self, __context: Any) -> None:
        if self.created_at is None:
            object.__setattr__(self, "created_at", datetime.utcnow())


_STATEMENTS = [
    """CREATE TABLE IF NOT EXISTS task_history (
        id            INTEGER PRIMARY KEY AUTOINCREMENT,
        task          TEXT NOT NULL,
        result        TEXT NOT NULL,
        steps_summary TEXT NOT NULL,
        created_at    TEXT NOT NULL
    )""",
    """CREATE VIRTUAL TABLE IF NOT EXISTS task_history_fts
        USING fts5(task, result, steps_summary, content='task_history', content_rowid='id')""",
    """CREATE TRIGGER IF NOT EXISTS task_history_ai
        AFTER INSERT ON task_history BEGIN
            INSERT INTO task_history_fts(rowid, task, result, steps_summary)
            VALUES (new.id, new.task, new.result, new.steps_summary);
        END""",
    """CREATE TRIGGER IF NOT EXISTS task_history_ad
        AFTER DELETE ON task_history BEGIN
            INSERT INTO task_history_fts(task_history_fts, rowid, task, result, steps_summary)
            VALUES ('delete', old.id, old.task, old.result, old.steps_summary);
        END""",
]


class SQLiteStore:
    """Async SQLite-backed store for :class:`TaskRecord` objects.

    The database file is created automatically on first use.
    """

    def __init__(self, db_path: str | Path) -> None:
        self._db_path = Path(db_path)
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialized = False

    async def _ensure_init(self) -> None:
        if self._initialized:
            return
        async with aiosqlite.connect(self._db_path) as db:
            for stmt in _STATEMENTS:
                await db.execute(stmt)
            await db.commit()
        self._initialized = True

    async def insert(self, record: TaskRecord) -> TaskRecord:
        await self._ensure_init()
        ts = record.created_at.isoformat()
        async with aiosqlite.connect(self._db_path) as db:
            cursor = await db.execute(
                "INSERT INTO task_history (task, result, steps_summary, created_at)"
                " VALUES (?, ?, ?, ?)",
                (record.task, record.result, record.steps_summary, ts),
            )
            await db.commit()
            return record.model_copy(update={"id": cursor.lastrowid})

    async def get_recent(self, n: int = 5) -> list[TaskRecord]:
        await self._ensure_init()
        async with aiosqlite.connect(self._db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT id, task, result, steps_summary, created_at"
                " FROM task_history ORDER BY id DESC LIMIT ?",
                (n,),
            ) as cur:
                rows = await cur.fetchall()
        return [_row_to_record(r) for r in rows]

    async def search(self, query: str) -> list[TaskRecord]:
        await self._ensure_init()
        async with aiosqlite.connect(self._db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT h.id, h.task, h.result, h.steps_summary, h.created_at"
                " FROM task_history_fts f"
                " JOIN task_history h ON h.id = f.rowid"
                " WHERE task_history_fts MATCH ?"
                " ORDER BY h.id DESC",
                (query,),
            ) as cur:
                rows = await cur.fetchall()
        return [_row_to_record(r) for r in rows]

    # ── Sync helpers (for test persistence check) ────────────────────────────

    def get_recent_sync(self, n: int = 5) -> list[TaskRecord]:
        """Synchronous variant — useful for verifying persistence after restart."""
        conn = sqlite3.connect(self._db_path)
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT id, task, result, steps_summary, created_at"
            " FROM task_history ORDER BY id DESC LIMIT ?",
            (n,),
        ).fetchall()
        conn.close()
        return [_row_to_record(r) for r in rows]


def _row_to_record(row: Any) -> TaskRecord:
    return TaskRecord(
        id=row["id"],
        task=row["task"],
        result=row["result"],
        steps_summary=row["steps_summary"],
        created_at=datetime.fromisoformat(row["created_at"]),
    )
