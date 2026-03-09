"""Data models for MessageManager."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class MessageEntry(BaseModel):
    """A single message in the conversation history."""

    role: str  # 'system', 'user', 'assistant'
    content: str
    images: list[dict[str, Any]] = Field(default_factory=list)
    is_pinned: bool = False  # pinned messages are never compacted
    token_count: int = 0  # estimated token count

    model_config = ConfigDict(arbitrary_types_allowed=True)

    def to_llm_dict(self) -> dict[str, Any]:
        """Return OpenAI-format message dict."""
        if self.images:
            content_parts: list[dict[str, Any]] = [{"type": "text", "text": self.content}]
            for img in self.images:
                content_parts.append(img)
            return {"role": self.role, "content": content_parts}
        return {"role": self.role, "content": self.content}


class MessageManagerState(BaseModel):
    """Serialisable state for MessageManager — supports save/restore."""

    system_prompt: str = ""
    task: str = ""
    entries: list[MessageEntry] = Field(default_factory=list)
    total_tokens: int = 0
    compaction_count: int = 0
    compacted_summary: str | None = None  # latest compaction summary

    model_config = ConfigDict(arbitrary_types_allowed=True)
