"""Executor — orchestrates the Planner → Navigator → [replan] loop."""

from __future__ import annotations

import asyncio
from typing import Any

import structlog

from agent.builtin.navigator import NavigatorResult
from agent.builtin.planner import PlannerAgent, PlannerOutput
from agent.context import AgentContext
from agent.event import Actors, EventType

logger = structlog.get_logger(__name__)


class ExecutorResult:
    """Result returned by :meth:`Executor.run`."""

    def __init__(
        self,
        success: bool,
        output: str = "",
        steps: int = 0,
        events_log: list[dict[str, Any]] | None = None,
        error: str = "",
    ) -> None:
        self.success = success
        self.output = output
        self.steps = steps
        self.events_log: list[dict[str, Any]] = events_log or []
        self.error = error

    def __repr__(self) -> str:
        return (
            f"ExecutorResult(success={self.success}, steps={self.steps},"
            f" error={self.error!r})"
        )


class Executor:
    """Orchestrates the full Planner → Navigator → Planner (replan) loop.

    Parameters
    ----------
    planner:
        A :class:`~agent.builtin.planner.PlannerAgent` instance.
    navigator:
        Any object with an ``execute_step(instruction, session)`` async method
        that returns a :class:`~agent.builtin.navigator.NavigatorResult`.
    """

    def __init__(self, planner: PlannerAgent, navigator: Any) -> None:
        self._planner = planner
        self._navigator = navigator
        self._stopped = False

    async def _capture_post_action_state(self, context: AgentContext) -> str:
        """Capture deterministic browser state after an action.

        This verification is executed by the executor itself (without LLM tools),
        so planner always receives factual "where we are now" context even when
        navigator chose a suboptimal action.
        """
        try:
            session = context.browser_session
            if session is None:
                return "Browser session unavailable."

            page = session.get_current_page()
            url = page.url
            title = await page.title()
            snippet: str = await page.evaluate(
                """() => {
                    const text = (document.body?.innerText || '')
                        .replace(/[ \t]+/g, ' ')
                        .replace(/\n{2,}/g, '\n')
                        .trim();
                    return text.slice(0, 400);
                }"""
            )
            return f"URL: {url}\nTitle: {title}\nSnippet: {snippet}"
        except Exception as exc:
            return f"Post-action page state unavailable: {exc}"

    # ── Public API ────────────────────────────────────────────────────────────

    def stop(self) -> None:
        """Request a graceful stop after the current step finishes."""
        self._stopped = True
        logger.info("executor.stop_requested")

    async def run(self, task: str, context: AgentContext) -> ExecutorResult:
        """Run the full task loop until done, max_steps, or stop().

        Args:
            task:    Top-level task description.
            context: Shared :class:`~agent.context.AgentContext`.

        Returns:
            :class:`ExecutorResult` summarising the run.
        """
        self._stopped = False
        events_log: list[dict[str, Any]] = []
        max_steps = context.settings.max_steps
        steps = 0
        last_output = ""
        # Accumulated navigator observations (instruction → result text)
        step_results: list[str] = []
        # Full (untruncated) observation from the most recent step — passed to planner separately
        last_obs_full: str = ""

        async def emit(event_type: EventType, actor: Actors, **data: Any) -> None:
            events_log.append({"event": event_type.value, "actor": actor.value, **data})
            await context.event_manager.emit(event_type, actor, data or None)

        await emit(EventType.TASK_START, Actors.EXECUTOR, task=task)

        # ── Initial plan ──────────────────────────────────────────────────────
        plan: PlannerOutput = await self._planner.plan(task)
        logger.info("executor.plan_ready", steps=len(plan.next_steps))

        while not self._stopped:
            if plan.done:
                last_output = plan.final_answer or plan.observation
                await emit(EventType.TASK_COMPLETE, Actors.PLANNER, output=last_output)
                return ExecutorResult(
                    success=True,
                    output=last_output,
                    steps=steps,
                    events_log=events_log,
                )

            if steps >= max_steps:
                logger.warning("executor.max_steps", steps=steps)
                await emit(EventType.TASK_ERROR, Actors.EXECUTOR, error="max steps")
                return ExecutorResult(
                    success=False,
                    output=last_output,
                    steps=steps,
                    events_log=events_log,
                    error="max steps",
                )

            # ── Execute one step from the plan, then immediately replan ───────
            # This prevents long blind execution chains when page state drifts
            # (e.g. wrong redirect after click) and enforces step-by-step
            # validation via planner feedback after every action.
            nav_result: NavigatorResult | None = None
            executed_instruction: str = ""
            for instruction in plan.next_steps:
                if self._stopped:
                    break
                if steps >= max_steps:
                    break

                executed_instruction = instruction
                await emit(EventType.STEP_START, Actors.NAVIGATOR, step=steps, instruction=instruction)
                try:
                    nav_result = await self._navigator.execute_step(
                        instruction, context.browser_session
                    )
                    steps += 1
                    post_state = await self._capture_post_action_state(context)
                    nav_observation = nav_result.observation or ""
                    combined_observation = (
                        f"{nav_observation}\n\nPost-action page state:\n{post_state}"
                        if nav_observation
                        else f"Post-action page state:\n{post_state}"
                    )

                    last_output = combined_observation
                    last_obs_full = combined_observation
                    # Track every step result for planner context (cap individual obs at 4000 chars)
                    obs = combined_observation[:4000]
                    step_results.append(
                        f"Step {steps} [{instruction[:80]}]: {obs}"
                    )

                    if nav_result.success:
                        await emit(
                            EventType.STEP_END,
                            Actors.NAVIGATOR,
                            step=steps,
                            page_state=post_state,
                        )
                    else:
                        await emit(
                            EventType.STEP_FAIL,
                            Actors.NAVIGATOR,
                            step=steps,
                            error=nav_result.observation,
                            page_state=post_state,
                        )

                    if nav_result.done:
                        await emit(EventType.TASK_COMPLETE, Actors.NAVIGATOR, output=last_output)
                        return ExecutorResult(
                            success=True,
                            output=last_output,
                            steps=steps,
                            events_log=events_log,
                        )

                    # Replan after every executed step (success or failure).
                    break
                except Exception as exc:
                    steps += 1
                    error_msg = str(exc)
                    logger.exception("executor.step_error", step=steps, error=error_msg)
                    await emit(EventType.STEP_FAIL, Actors.NAVIGATOR, step=steps, error=error_msg)
                    nav_result = NavigatorResult(
                        action_taken="error",
                        success=False,
                        observation=error_msg,
                        done=False,
                    )
                    break  # replan after error

            if self._stopped:
                return ExecutorResult(
                    success=False,
                    output=last_output,
                    steps=steps,
                    events_log=events_log,
                    error="stopped",
                )

            # ── Replan after every executed step ───────────────────────────────
            # Small pause to avoid ZAI rate-limit stacking after navigator calls
            await asyncio.sleep(1.0)

            if nav_result is None:
                plan = await self._planner.replan(
                    error="Planner returned no executable steps.",
                    step_results=step_results,
                    last_obs_full=last_obs_full,
                )
                logger.info("executor.replanned_empty_plan", done=plan.done)
            elif not nav_result.success:
                plan = await self._planner.replan(
                    error=nav_result.observation or "unknown error",
                    step_results=step_results,
                    last_obs_full=last_obs_full,
                )
                logger.info("executor.replanned", done=plan.done)
            else:
                # Step succeeded; ask planner to verify current state before next action
                plan = await self._planner.replan(
                    error="",
                    step_results=step_results,
                    last_obs_full=last_obs_full,
                )
                logger.info(
                    "executor.step_verified",
                    step=steps,
                    instruction=executed_instruction[:80],
                    done=plan.done,
                )

        # Stopped externally
        return ExecutorResult(
            success=False,
            output=last_output,
            steps=steps,
            events_log=events_log,
            error="stopped",
        )
