"""LongTermMemory — persists task history across sessions via SQLite."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import structlog

from memory.store import SQLiteStore, TaskRecord

logger = structlog.get_logger(__name__)

_DEFAULT_DB_NAME = "memory.db"


class LongTermMemory:
    """High-level API for storing and querying past task records.

    Parameters
    ----------
    data_dir:
        Directory in which ``memory.db`` is created.  Defaults to
        ``Settings.data_dir`` when created via :meth:`from_settings`.
    """

    def __init__(self, data_dir: str | Path = "./data") -> None:
        db_path = Path(data_dir) / _DEFAULT_DB_NAME
        self._store = SQLiteStore(db_path)
        logger.debug("long_term_memory.init", db=str(db_path))

    # ── Factory ───────────────────────────────────────────────────────────────

    @classmethod
    def from_settings(cls) -> LongTermMemory:
        """Create a LongTermMemory using the application Settings."""
        from config.settings import get_settings

        s = get_settings()
        return cls(data_dir=s.data_dir)

    # ── Public API ────────────────────────────────────────────────────────────

    async def save_task(
        self,
        task: str,
        result: str,
        steps_summary: str,
        created_at: datetime | None = None,
    ) -> TaskRecord:
        """Persist a completed task record.

        Args:
            task:          The original task description.
            result:        Final result / output of the task.
            steps_summary: Human-readable summary of steps taken.
            created_at:    Override timestamp (defaults to now).

        Returns:
            The saved :class:`~memory.store.TaskRecord` with its assigned ``id``.
        """
        record = TaskRecord(
            task=task,
            result=result,
            steps_summary=steps_summary,
            created_at=created_at or datetime.utcnow(),
        )
        saved = await self._store.insert(record)
        logger.info("long_term_memory.saved", task_id=saved.id, task=task[:60])
        return saved

    async def get_recent(self, n: int = 5) -> list[TaskRecord]:
        """Return the *n* most recently saved task records."""
        return await self._store.get_recent(n)

    async def search(self, query: str) -> list[TaskRecord]:
        """Full-text search across task, result and steps_summary fields."""
        results = await self._store.search(query)
        logger.debug("long_term_memory.search", query=query, hits=len(results))
        return results

    def get_recent_sync(self, n: int = 5) -> list[TaskRecord]:
        """Synchronous variant — verifies persistence without async runtime."""
        return self._store.get_recent_sync(n)
