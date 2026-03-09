"""Data models for the agent layer."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class LoopDetectedError(RuntimeError):
    """Raised when the agent repeats the same action too many times in a row."""

    def __init__(self, action: str, count: int) -> None:
        self.action = action
        self.count = count
        super().__init__(f"Loop detected: action '{action}' repeated {count} times in a row")


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------


class AgentSettings(BaseModel):
    """Runtime settings for an agent execution."""

    max_steps: int = 50
    """Maximum number of steps before the run is aborted."""

    vision_enabled: bool = True
    """Whether to capture and send screenshots to the LLM."""

    thinking_enabled: bool = False
    """Whether to include an internal chain-of-thought step."""

    loop_detection_threshold: int = 3
    """How many identical consecutive actions trigger LoopDetectedError."""

    retry_on_error: bool = True
    """Retry a step once on transient errors (network, timeout)."""


# ---------------------------------------------------------------------------
# Step result
# ---------------------------------------------------------------------------


class StepResult(BaseModel):
    """Outcome of a single agent step."""

    action: str = ""
    """Short name / description of the action taken."""

    output: str = ""
    """Any extracted content or observation from the step."""

    done: bool = False
    """True when the agent signals task completion."""

    success: bool = True
    """False when the step resulted in an error."""

    error: str | None = None
    """Error message if the step failed."""

    data: dict[str, Any] = Field(default_factory=dict)
    """Optional structured data from the step."""


# ---------------------------------------------------------------------------
# Agent output
# ---------------------------------------------------------------------------


class AgentOutput(BaseModel):
    """Final result returned by BaseAgent.run()."""

    success: bool
    """True if the task was completed successfully."""

    result: str = ""
    """Human-readable summary of what was accomplished."""

    steps_taken: int = 0
    """Total number of steps executed."""

    error: str | None = None
    """Error message if the run was aborted."""

    history: list[StepResult] = Field(default_factory=list)
    """Full step-by-step history of the run."""

    data: dict[str, Any] = Field(default_factory=dict)
    """Optional structured output data."""
