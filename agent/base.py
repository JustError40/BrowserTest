"""BaseAgent — abstract base class for all coworkOS agents."""

from __future__ import annotations

import abc

import structlog

from agent.views import AgentOutput, AgentSettings, LoopDetectedError, StepResult

logger = structlog.get_logger(__name__)


class BaseAgent(abc.ABC):
    """Abstract base class for task-executing agents.

    Subclasses must implement ``_step()`` which performs one unit of work
    and returns a ``StepResult``.

    Usage
    -----
    ::

        class MyAgent(BaseAgent):
            async def _step(self) -> StepResult:
                ...

        agent = MyAgent(settings=AgentSettings(max_steps=10))
        output = await agent.run("Do something useful")
    """

    def __init__(self, settings: AgentSettings | None = None) -> None:
        self.settings: AgentSettings = settings or AgentSettings()
        self._current_task: str = ""
        self._step_history: list[StepResult] = []
        self._recent_actions: list[str] = []  # for loop detection

    # ------------------------------------------------------------------
    # Abstract interface
    # ------------------------------------------------------------------

    @abc.abstractmethod
    async def _step(self) -> StepResult:
        """Execute one agent step and return the result.

        Implement this in a concrete subclass:
          - Gather browser state / observations
          - Ask LLM what to do
          - Execute the chosen action
          - Return a StepResult (set done=True when task is complete)
        """

    # ------------------------------------------------------------------
    # Run loop
    # ------------------------------------------------------------------

    async def run(self, task: str) -> AgentOutput:
        """Execute a task and return the final AgentOutput.

        Args:
            task: Natural-language description of the task to perform.

        Returns:
            AgentOutput with success status, result, and step history.
        """
        self._current_task = task
        self._step_history = []
        self._recent_actions = []
        steps_taken = 0

        logger.info("agent.run started", task=task, max_steps=self.settings.max_steps)

        try:
            for step_num in range(1, self.settings.max_steps + 1):
                logger.debug("agent.step", step=step_num)
                step_result = await self._execute_step_with_retry(step_num)
                self._step_history.append(step_result)
                steps_taken = step_num

                # Loop detection
                self._check_loop(step_result.action)

                if step_result.done:
                    logger.info(
                        "agent.run completed",
                        steps=steps_taken,
                        success=step_result.success,
                    )
                    return AgentOutput(
                        success=step_result.success,
                        result=step_result.output,
                        steps_taken=steps_taken,
                        history=list(self._step_history),
                        data=step_result.data,
                    )

            # Exhausted max_steps without done=True
            logger.warning("agent.run exhausted max_steps", steps=steps_taken)
            return AgentOutput(
                success=False,
                result="",
                steps_taken=steps_taken,
                error=f"Reached max_steps ({self.settings.max_steps}) without completing task",
                history=list(self._step_history),
            )

        except LoopDetectedError as exc:
            logger.warning("loop detected", action=exc.action, count=exc.count)
            return AgentOutput(
                success=False,
                result="",
                steps_taken=steps_taken,
                error=str(exc),
                history=list(self._step_history),
            )
        except Exception as exc:
            logger.error("agent.run error", error=str(exc), exc_info=True)
            return AgentOutput(
                success=False,
                result="",
                steps_taken=steps_taken,
                error=str(exc),
                history=list(self._step_history),
            )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _execute_step_with_retry(self, step_num: int) -> StepResult:
        """Wrap ``_step()`` with a single retry on error if configured."""
        try:
            return await self._step()
        except Exception as exc:
            if self.settings.retry_on_error:
                logger.warning("step error, retrying", step=step_num, error=str(exc))
                try:
                    return await self._step()
                except Exception as exc2:
                    return StepResult(
                        action="error",
                        output="",
                        success=False,
                        error=str(exc2),
                    )
            return StepResult(
                action="error",
                output="",
                success=False,
                error=str(exc),
            )

    def _check_loop(self, action: str) -> None:
        """Detect repeated identical actions and raise LoopDetectedError."""
        if not action:
            return
        self._recent_actions.append(action)
        # Keep only the last N actions for comparison
        threshold = self.settings.loop_detection_threshold
        self._recent_actions = self._recent_actions[-threshold:]

        if len(self._recent_actions) == threshold and len(set(self._recent_actions)) == 1:
            raise LoopDetectedError(action=action, count=threshold)

    # ------------------------------------------------------------------
    # Convenience properties
    # ------------------------------------------------------------------

    @property
    def current_task(self) -> str:
        return self._current_task

    @property
    def step_count(self) -> int:
        return len(self._step_history)
