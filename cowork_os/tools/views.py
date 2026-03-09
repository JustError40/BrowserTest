"""Views (data models) for the ToolRegistry."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class ActionResult(BaseModel):
    """Result of executing an action."""

    success: bool = True
    extracted_content: str | None = None
    error: str | None = None
    data: dict[str, Any] = Field(default_factory=dict)

    @classmethod
    def ok(cls, content: str | None = None, **data: Any) -> ActionResult:
        """Create a successful ActionResult."""
        return cls(success=True, extracted_content=content, data=data)

    @classmethod
    def fail(cls, error: str, **data: Any) -> ActionResult:
        """Create a failed ActionResult."""
        return cls(success=False, error=error, data=data)


class RegisteredAction(BaseModel):
    """Metadata for a registered action."""

    name: str
    description: str
    param_schema: dict[str, Any]  # JSON Schema for parameters
    requires_browser: bool = False  # True if function takes browser_session param
