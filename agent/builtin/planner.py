"""PlannerAgent: high-level task planner and replanner.

The PlannerAgent receives a task description and returns a structured plan
(PlannerOutput) with atomic next steps for the NavigatorAgent. It can be
called repeatedly to replan when errors occur.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import structlog
from pydantic import BaseModel, Field

from agent.base import BaseAgent
from agent.views import AgentSettings, StepResult

logger = structlog.get_logger(__name__)

_PROMPT_PATH = Path(__file__).parent.parent / "prompts" / "planner.md"
_SYSTEM_PROMPT: str = _PROMPT_PATH.read_text(encoding="utf-8") if _PROMPT_PATH.exists() else ""

_MAX_REPLANS = 5


# ---------------------------------------------------------------------------
# Output models
# ---------------------------------------------------------------------------


class PlannerOutput(BaseModel):
    """Result of a single planning or replanning call."""

    observation: str = ""
    """What the planner observed about the task or current state."""

    next_steps: list[str] = Field(default_factory=list)
    """Ordered list of atomic instructions for the NavigatorAgent."""

    done: bool = False
    """True when the task is complete or cannot be completed."""

    final_answer: str | None = None
    """Summary result when done=True."""

    reasoning: str = ""
    """Internal reasoning / chain-of-thought for the plan."""

    data: dict[str, Any] = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# Agent implementation
# ---------------------------------------------------------------------------


class PlannerAgent(BaseAgent):
    """High-level task planner integrated with BaseAgent run-loop.

    Args:
        llm:         An LLM instance (BaseLLM) for generating plans.
        settings:    Optional AgentSettings.
    """

    def __init__(self, llm, settings: AgentSettings | None = None) -> None:
        super().__init__(settings=settings)
        self.llm = llm
        self._replan_count: int = 0
        self._last_output: PlannerOutput | None = None
        self._current_task: str = ""

    # ------------------------------------------------------------------
    # BaseAgent interface
    # ------------------------------------------------------------------

    async def _step(self) -> StepResult:
        """One planning step — returns done when plan is final."""
        output = await self.plan(self._current_task)
        return StepResult(
            action="plan",
            output=str(output.next_steps),
            done=output.done,
            success=True,
            data=output.model_dump(),
        )

    async def run(self, task: str):  # type: ignore[override]
        """Override to store task before running."""
        self._current_task = task
        return await super().run(task)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def plan(self, task: str) -> PlannerOutput:
        """Generate an initial step-by-step plan for the task.

        Args:
            task: Natural-language task description.

        Returns:
            PlannerOutput with next_steps and optional final_answer.
        """
        self._current_task = task
        self._replan_count = 0

        prompt = _build_plan_prompt(task, error_context=None)
        output = await self._call_llm(prompt)
        self._last_output = output

        logger.info(
            "planner.plan",
            task=task[:60],
            steps=len(output.next_steps),
            done=output.done,
        )
        return output

    async def replan(
        self,
        error: str,
        step_results: list[str] | None = None,
        last_obs_full: str = "",
    ) -> PlannerOutput:
        """Replan or confirm completion after the navigator's round of steps.

        Args:
            error:         Description of what went wrong (empty string if all
                           steps succeeded).
            step_results:  List of per-step observation strings collected by the
                           Executor.  Passed to the LLM so it can evaluate
                           whether the task goal was already achieved.
            last_obs_full: Full (untruncated) observation from the last
                           navigator step.  Included separately so important
                           extraction results (e.g. full FAQ text) are never
                           lost to truncation.

        Returns:
            New PlannerOutput with an alternative plan, or done=True if the
            task is complete / irrecoverably failed.
        """
        self._replan_count += 1

        if self._replan_count > _MAX_REPLANS:
            msg = (
                f"Task could not be completed after {_MAX_REPLANS} replan "
                f"attempts. Last error: {error}"
            )
            logger.warning("planner.replan exhausted", replans=self._replan_count)
            output = PlannerOutput(
                observation=f"Exhausted {_MAX_REPLANS} replan attempts.",
                done=True,
                final_answer=msg,
                reasoning="Maximum replan limit reached.",
            )
            self._last_output = output
            return output

        prompt = _build_plan_prompt(
            self._current_task,
            error_context=error,
            replan_count=self._replan_count,
            step_results=step_results or [],
            last_obs_full=last_obs_full,
        )
        output = await self._call_llm(prompt)
        self._last_output = output

        logger.info(
            "planner.replan",
            replan_count=self._replan_count,
            error=error[:60],
            steps=len(output.next_steps),
        )
        return output

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _call_llm(self, user_content: str) -> PlannerOutput:
        """Send prompt to LLM and parse the PlannerOutput."""
        messages = [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": user_content},
        ]

        try:
            raw = await self.llm.generate(messages)
            return _parse_planner_output(raw)
        except Exception as exc:
            logger.warning("planner LLM call failed", error=str(exc))
            return PlannerOutput(
                observation=f"LLM error: {exc}",
                done=True,
                final_answer=f"Planning failed: {exc}",
                reasoning="LLM call failed.",
            )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _build_plan_prompt(
    task: str,
    error_context: str | None = None,
    replan_count: int = 0,
    step_results: list[str] | None = None,
    last_obs_full: str = "",
) -> str:
    """Build the user message for the planner LLM call."""
    results_section = ""
    if step_results:
        formatted = "\n".join(f"  - {r}" for r in step_results)
        # Cap total step_results summary to 12000 chars to avoid LLM context overflow
        _MAX_RESULTS_CHARS = 12000
        if len(formatted) > _MAX_RESULTS_CHARS:
            formatted = "...(earlier steps truncated)...\n" + formatted[-_MAX_RESULTS_CHARS:]
        results_section = f"\n\nCompleted steps and their results:\n{formatted}"

    # Always include the last step's full observation separately
    last_obs_section = ""
    if last_obs_full:
        last_obs_section = f"\n\n## Last step full result (untruncated):\n{last_obs_full}"

    if error_context:
        return (
            f"Task: {task}{results_section}{last_obs_section}\n\n"
            f"Replanning attempt {replan_count}/{_MAX_REPLANS}.\n"
            f"Previous step failed with error: {error_context}\n\n"
            "Review the completed steps above, then provide an alternative plan "
            "that avoids the same error. If the task is actually already done "
            "based on the results, set done=true and provide final_answer."
        )

    if step_results:
        return (
            f"Task: {task}{results_section}{last_obs_section}\n\n"
            "Review the completed steps and their results above.\n"
            "- If the task goal is fully achieved, set done=true and populate "
            "final_answer with the answer/summary the user asked for.\n"
            "- If more steps are needed to complete the task, provide them in next_steps."
        )

    return f"Task: {task}\n\nPlease generate a step-by-step plan."


def _parse_planner_output(raw: str) -> PlannerOutput:
    """Parse LLM raw output into PlannerOutput.

    Handles JSON in markdown code fences and plain JSON.
    """
    if not raw:
        return PlannerOutput(done=True, final_answer="Empty LLM response.")

    # Strip markdown code fences
    cleaned = re.sub(r"```(?:json)?\s*", "", raw).replace("```", "").strip()

    # Find first JSON object
    match = re.search(r"\{.*\}", cleaned, re.DOTALL)
    if match:
        cleaned = match.group(0)

    try:
        data = json.loads(cleaned)
        return PlannerOutput(
            reasoning=data.get("reasoning", ""),
            observation=data.get("observation", ""),
            next_steps=data.get("next_steps", []),
            done=bool(data.get("done", False)),
            final_answer=data.get("final_answer"),
            data=data,
        )
    except (json.JSONDecodeError, Exception):
        # LLM returned plain text — treat as a single-step plan
        return PlannerOutput(
            observation="LLM returned unstructured response.",
            next_steps=[raw.strip()[:500]] if raw.strip() else [],
        )
