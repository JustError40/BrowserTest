"""MessageManager: conversation history with automatic token-budget compaction."""

from __future__ import annotations

import structlog

from memory.views import MessageEntry, MessageManagerState

logger = structlog.get_logger(__name__)

# Rough chars-per-token approximation (standard ~4 chars/token for English)
_CHARS_PER_TOKEN = 4


def _estimate_tokens(text: str) -> int:
    """Estimate token count for a string using a 4-chars-per-token heuristic."""
    return max(1, len(text) // _CHARS_PER_TOKEN)


class MessageManager:
    """Manages LLM conversation history with automatic compaction.

    Priority during compaction (highest → lowest):
        1. system prompt  (always kept)
        2. task message   (always kept)
        3. last N user/assistant messages  (kept intact)
        4. older messages  (replaced by summary or dropped)

    Args:
        system_prompt: Static system instructions (pinned, never compacted).
        task:          Task description (pinned, never compacted).
        token_budget:  Maximum total tokens before compaction triggers.
        llm:           Optional LLM for summarisation; if None, old messages
                       are dropped instead of summarised.
        keep_last_n:   How many recent (non-pinned) messages to always keep.
        state:         Optional pre-existing state (for restore / persistence).
    """

    def __init__(
        self,
        system_prompt: str = "",
        task: str = "",
        token_budget: int = 4000,
        llm=None,
        keep_last_n: int = 3,
        state: MessageManagerState | None = None,
    ) -> None:
        self._token_budget = token_budget
        self._llm = llm
        self._keep_last_n = keep_last_n

        if state is not None:
            self.state = state
        else:
            self.state = MessageManagerState(
                system_prompt=system_prompt,
                task=task,
            )
            # Pin the system prompt and task as the first two entries
            if system_prompt:
                self._add_entry("system", system_prompt, pinned=True)
            if task:
                self._add_entry("user", f"Task: {task}", pinned=True)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def add_message(
        self,
        role: str,
        content: str,
        images: list | None = None,
    ) -> None:
        """Append a message to the history (compacts synchronously if needed)."""
        self._add_entry(role, content, images=images or [])
        # Trigger synchronous compaction (drops old messages) when over budget
        if self.state.total_tokens > self._token_budget:
            self._compact_sync()

    def get_messages(self) -> list[dict]:
        """Return all messages in OpenAI-format dicts, ordered for LLM call."""
        return [e.to_llm_dict() for e in self.state.entries]

    def get_token_count(self) -> int:
        """Return the current estimated total token count."""
        return self.state.total_tokens

    async def add_message_async(
        self,
        role: str,
        content: str,
        images: list | None = None,
    ) -> None:
        """Async variant — uses LLM for rich summarisation when compacting."""
        self._add_entry(role, content, images=images or [])
        if self.state.total_tokens > self._token_budget:
            await self._compact_async()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _add_entry(
        self,
        role: str,
        content: str,
        images: list | None = None,
        pinned: bool = False,
    ) -> None:
        tokens = _estimate_tokens(content)
        entry = MessageEntry(
            role=role,
            content=content,
            images=images or [],
            is_pinned=pinned,
            token_count=tokens,
        )
        self.state.entries.append(entry)
        self.state.total_tokens += tokens

    def _compact_sync(self) -> None:
        """Compact by replacing old non-pinned messages with a brief summary.

        Strategy (no LLM available):
            - Keep all pinned entries (system / task).
            - Keep the last `keep_last_n` non-pinned entries intact.
            - Drop everything in between, optionally inserting a placeholder.
        """
        pinned = [e for e in self.state.entries if e.is_pinned]
        non_pinned = [e for e in self.state.entries if not e.is_pinned]

        if len(non_pinned) <= self._keep_last_n:
            # Nothing to compact yet
            return

        to_keep = non_pinned[-self._keep_last_n :]
        to_drop = non_pinned[: len(non_pinned) - self._keep_last_n]

        # Build a simple textual summary of dropped messages
        dropped_text = "\n".join(f"[{e.role}]: {e.content[:200]}" for e in to_drop)
        summary_content = f"[Compacted {len(to_drop)} messages]\n{dropped_text[:800]}"
        summary_tokens = _estimate_tokens(summary_content)

        summary_entry = MessageEntry(
            role="user",
            content=summary_content,
            is_pinned=False,
            token_count=summary_tokens,
        )

        new_entries = pinned + [summary_entry] + to_keep
        new_total = sum(e.token_count for e in new_entries)

        self.state.entries = new_entries
        self.state.total_tokens = new_total
        self.state.compaction_count += 1
        self.state.compacted_summary = summary_content

        logger.info(
            "compaction done (sync)",
            dropped=len(to_drop),
            compaction_count=self.state.compaction_count,
            tokens_before=self.state.total_tokens + sum(e.token_count for e in to_drop) - summary_tokens,
            tokens_after=new_total,
        )

    async def _compact_async(self) -> None:
        """Compact using LLM for rich summarisation (if available)."""
        if self._llm is None:
            self._compact_sync()
            return

        pinned = [e for e in self.state.entries if e.is_pinned]
        non_pinned = [e for e in self.state.entries if not e.is_pinned]

        if len(non_pinned) <= self._keep_last_n:
            return

        to_keep = non_pinned[-self._keep_last_n :]
        to_summarise = non_pinned[: len(non_pinned) - self._keep_last_n]

        # Build text for LLM to summarise
        history_text = "\n".join(
            f"[{e.role}]: {e.content}" for e in to_summarise
        )
        prompt = [
            {
                "role": "user",
                "content": (
                    "Summarise the following conversation history concisely "
                    "(max 300 words):\n\n" + history_text
                ),
            }
        ]

        try:
            summary = await self._llm.generate(prompt)
        except Exception as exc:
            logger.warning("LLM compaction failed, falling back to sync", error=str(exc))
            self._compact_sync()
            return

        summary_entry = MessageEntry(
            role="user",
            content=f"[Summary of earlier conversation]:\n{summary}",
            is_pinned=False,
            token_count=_estimate_tokens(summary),
        )

        new_entries = pinned + [summary_entry] + to_keep
        new_total = sum(e.token_count for e in new_entries)

        self.state.entries = new_entries
        self.state.total_tokens = new_total
        self.state.compaction_count += 1
        self.state.compacted_summary = summary

        logger.info(
            "compaction done (llm)",
            compaction_count=self.state.compaction_count,
            tokens_after=new_total,
        )
