"""ask_human tool — pause execution and request user input."""

from __future__ import annotations

import asyncio
import sys
from typing import TYPE_CHECKING

import structlog

from tools.registry import registry
from tools.views import ActionResult

if TYPE_CHECKING:
    from agent.event import EventManager

logger = structlog.get_logger(__name__)

_DEFAULT_TIMEOUT = 300.0  # 5 minutes


async def ask_human(
    question: str,
    options: list[str] | None = None,
    timeout: float = _DEFAULT_TIMEOUT,
    event_manager: EventManager | None = None,
) -> str:
    """Pause the agent and ask the human for input.

    Prints the *question* (and numbered *options* if provided) to stdout,
    then waits up to *timeout* seconds for a response.

    If an *event_manager* is supplied, :attr:`~agent.event.EventType.HUMAN_NEEDED`
    is emitted **before** the question is shown.

    Args:
        question:      The question to display.
        options:       Optional list of choices; user enters a number 1–N.
        timeout:       Seconds before raising :exc:`TimeoutError`.
        event_manager: Optional event bus; if provided emits HUMAN_NEEDED.

    Returns:
        The user's response text (or the selected option text).

    Raises:
        TimeoutError: If the user does not respond within *timeout* seconds.
    """
    # Emit HUMAN_NEEDED event if event manager available
    if event_manager is not None:
        from agent.event import Actors, EventType

        await event_manager.emit(
            EventType.HUMAN_NEEDED,
            Actors.EXECUTOR,
            {"question": question, "options": options},
        )

    # Build prompt
    prompt_lines = ["\n[HUMAN INPUT REQUIRED]", question]
    if options:
        for i, opt in enumerate(options, start=1):
            prompt_lines.append(f"  {i}. {opt}")
        prompt_lines.append("Enter number or text: ")
    else:
        prompt_lines.append("Your response: ")

    prompt = "\n".join(prompt_lines)

    loop = asyncio.get_event_loop()

    def _read_input() -> str:
        sys.stdout.write(prompt)
        sys.stdout.flush()
        return sys.stdin.readline().strip()

    try:
        raw = await asyncio.wait_for(
            loop.run_in_executor(None, _read_input),
            timeout=timeout,
        )
    except TimeoutError as exc:
        raise TimeoutError(
            f"User did not respond within {timeout:.0f}s"
        ) from exc

    # Map numeric selection to option text
    if options and raw.isdigit():
        idx = int(raw) - 1
        if 0 <= idx < len(options):
            return options[idx]

    return raw


# ── ToolRegistry integration ──────────────────────────────────────────────────


@registry.action(description=(
    "Ask the human user for confirmation or additional information. "
    "Use when confidence is below 70% or when sensitive data is required. "
    "Provide numbered options for multiple choice, or leave options empty for free text."
))
async def ask_human_tool(
    question: str,
    options: str = "",
    timeout_seconds: float = _DEFAULT_TIMEOUT,
) -> ActionResult:
    """Registry-compatible wrapper for :func:`ask_human`.

    *options* is a comma-separated string (e.g. ``"Yes,No,Cancel"``); pass an
    empty string for free-text input.
    """
    opts: list[str] | None = None
    if options.strip():
        opts = [o.strip() for o in options.split(",") if o.strip()]

    try:
        response = await ask_human(
            question=question,
            options=opts,
            timeout=timeout_seconds,
        )
        logger.info("ask_human.response", response=response)
        return ActionResult.ok(content=response)
    except TimeoutError as exc:
        logger.warning("ask_human.timeout", timeout=timeout_seconds)
        return ActionResult.fail(str(exc))
