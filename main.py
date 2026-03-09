"""coworkOS — CLI entry point.

Full execution flow:
  Settings → LLM → BrowserSession → AgentContext → Executor.run(task)

Usage::
    python main.py "Open example.com and tell me the title"
    python -m cowork_os "Open example.com and tell me the title"
"""

from __future__ import annotations

import asyncio
import signal
from typing import Any

import structlog
import typer
from rich.console import Console
from rich.panel import Panel
from rich.text import Text

console = Console()
logger = structlog.get_logger(__name__)

app = typer.Typer(name="cowork-os", help="Browser Automation Platform", add_completion=False)

# ─── Global executor reference for graceful-stop on SIGINT ───────────────────
_executor_ref: Any = None
_browser_ref: Any = None


def _on_sigint(sig: int, frame: Any) -> None:  # noqa: ARG001
    """Handle Ctrl+C: stop the executor and schedule browser close."""
    console.print("\n[bold yellow]Interrupted — stopping gracefully…[/bold yellow]")
    if _executor_ref is not None:
        _executor_ref.stop()


# ─── Event callbacks for real-time progress ──────────────────────────────────


def _make_event_callbacks(step_counter: list[int]) -> dict:
    """Return a mapping of EventType → callback for live progress display."""
    from agent.event import EventType

    def on_task_start(event: Any) -> None:
        task = (event.data or {}).get("task", "")
        console.print(f"\n[bold blue]▶  Task:[/bold blue] {task}")

    def on_step_start(event: Any) -> None:
        step_counter[0] += 1
        instr = (event.data or {}).get("instruction", "")
        console.print(f"  [dim]step {step_counter[0]}:[/dim] {instr}")

    def on_step_end(event: Any) -> None:  # noqa: ARG001
        console.print("  [green]✓[/green]")

    def on_step_fail(event: Any) -> None:
        err = (event.data or {}).get("error", "")
        console.print(f"  [red]✗[/red] {err[:120]}")

    def on_task_complete(event: Any) -> None:
        out = (event.data or {}).get("output", "")
        console.print(
            Panel(
                Text(out, style="bold green"),
                title="[bold green]✔  Task complete[/bold green]",
                border_style="green",
            )
        )

    def on_task_error(event: Any) -> None:
        err = (event.data or {}).get("error", "")
        console.print(
            Panel(
                Text(err or "Unknown error", style="bold red"),
                title="[bold red]✘  Task failed[/bold red]",
                border_style="red",
            )
        )

    return {
        EventType.TASK_START: on_task_start,
        EventType.STEP_START: on_step_start,
        EventType.STEP_END: on_step_end,
        EventType.STEP_FAIL: on_step_fail,
        EventType.TASK_COMPLETE: on_task_complete,
        EventType.TASK_ERROR: on_task_error,
    }


# ─── Core async runner ────────────────────────────────────────────────────────


async def run_task(
    task: str,
    *,
    headless: bool = True,
    max_steps: int = 20,
    vision: bool = False,
) -> int:
    """Full E2E execution: Settings → LLM → Browser → Executor.

    Returns:
        0 on success, 1 on failure.
    """
    global _executor_ref, _browser_ref

    # 1. Application settings ────────────────────────────────────────────────
    from config.settings import get_settings
    settings = get_settings()

    # 2. Logging ─────────────────────────────────────────────────────────────
    from config.logging import setup_logging
    setup_logging(settings.log_level)

    # 3. Register built-in tools (navigate_to, click_element, extract_content, …)
    import tools.builtin  # noqa: F401  — side-effect: registers actions in registry

    # 4. LLM ─────────────────────────────────────────────────────────────────
    from agent.llm.factory import LLMFactory
    llm = LLMFactory.create_from_settings()
    logger.info("LLM ready", provider=settings.llm_provider, model=settings.llm_model)

    # 4. Browser session ──────────────────────────────────────────────────────
    from browser.profile import BrowserProfile
    from browser.session import BrowserSession
    cdp_url = settings.browser_cdp_url.strip()
    if cdp_url:
        # Attach to an existing Chrome/Edge running on the host
        logger.info("Launching browser", profile="cdp", cdp_url=cdp_url)
        browser = BrowserSession(cdp_url=cdp_url)
    else:
        profile = BrowserProfile(headless=headless)
        logger.info("Launching browser", profile=profile.name)
        browser = BrowserSession(profile=profile)
    _browser_ref = browser

    # 5. Agent context ────────────────────────────────────────────────────────
    from agent.context import AgentContext
    from agent.views import AgentSettings
    agent_settings = AgentSettings(max_steps=max_steps, vision_enabled=vision)
    context = AgentContext.create(settings=agent_settings).with_llm(llm)

    # Subscribe live-progress callbacks ─────────────────────────────────────
    step_counter: list[int] = [0]
    callbacks = _make_event_callbacks(step_counter)
    for event_type, cb in callbacks.items():
        context.event_manager.subscribe(event_type, cb)

    # 6. Planner ──────────────────────────────────────────────────────────────
    from agent.builtin.planner import PlannerAgent
    planner = PlannerAgent(llm=llm, settings=agent_settings)

    # 7. Executor (navigator created after browser launches) ──────────────────
    from agent.builtin.navigator import NavigatorAgent
    from agent.executor import Executor

    # Register signal handler for graceful Ctrl+C ────────────────────────────
    signal.signal(signal.SIGINT, _on_sigint)

    result_code = 0
    try:
        await browser.launch()
        navigator = NavigatorAgent(llm=llm, browser_session=browser, settings=agent_settings)
        executor = Executor(planner=planner, navigator=navigator)
        _executor_ref = executor
        context = context.with_browser_session(browser)
        result = await executor.run(task, context)
        if not result.success:
            console.print(f"[red]Task ended unsuccessfully:[/red] {result.error}")
            result_code = 1
    except Exception as exc:
        console.print(f"[bold red]Fatal error:[/bold red] {exc}")
        logger.exception("run_task.fatal", error=str(exc))
        result_code = 1
    finally:
        await browser.close()
        logger.info("Browser closed")

    return result_code


# ─── CLI ──────────────────────────────────────────────────────────────────────


@app.command()
def cli(
    task: str = typer.Argument(default="", help="Task to execute"),
    headless: bool = typer.Option(False, "--headless/--no-headless", help="Run browser headless"),
    max_steps: int = typer.Option(20, "--max-steps", help="Maximum executor steps"),
    vision: bool = typer.Option(False, "--vision", help="Enable screenshot vision"),
) -> None:
    """Execute a browser automation task end-to-end."""
    if not task:
        task = typer.prompt("Enter task")

    try:
        exit_code = asyncio.run(run_task(task, headless=headless, max_steps=max_steps, vision=vision))
    except KeyboardInterrupt:
        console.print("\n[bold red]Interrupted.[/bold red]")
        exit_code = 130

    raise typer.Exit(exit_code)


def run() -> None:
    """Alias for CLI entry point (used by pyproject.toml script)."""
    app()


if __name__ == "__main__":
    app()
