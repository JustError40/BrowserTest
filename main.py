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
    cdp_url: str = "",
    auto_attach: bool = False,
) -> int:
    """Full E2E execution: Settings → LLM → Browser → Executor.

    Args:
        task:        Natural-language task to execute.
        headless:    Run a fresh Playwright browser headless (ignored in CDP mode).
        max_steps:   Maximum agent steps before giving up.
        vision:      Enable screenshot vision in NavigatorAgent.
        cdp_url:     Explicit CDP URL, e.g. ``http://localhost:9222``.
                     Overrides ``BROWSER_CDP_URL`` from settings.
        auto_attach: If True and no cdp_url given, scan for a running Chrome
                     and attach to it automatically.

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

    # 5. Resolve CDP URL (priority: explicit arg > env/settings > auto-discover)
    from browser.discover import find_running_chrome, require_chrome_or_raise
    effective_cdp = cdp_url.strip() or settings.browser_cdp_url.strip()

    if not effective_cdp and auto_attach:
        discovered = find_running_chrome()
        if discovered:
            console.print(f"[bold cyan]🔗 Auto-attached to browser:[/bold cyan] {discovered}")
            effective_cdp = discovered
        else:
            console.print(
                "[bold yellow]⚠  No running browser found.[/bold yellow] "
                "Start Chrome with [cyan]--remote-debugging-port=9222[/cyan] or "
                "use [cyan]cowork-os discover[/cyan] for instructions."
            )

    # 6. Browser session ──────────────────────────────────────────────────────
    from browser.profile import BrowserProfile
    from browser.session import BrowserSession
    if effective_cdp:
        # Validate the CDP endpoint before we go any further
        try:
            require_chrome_or_raise(effective_cdp)
        except RuntimeError as exc:
            console.print(f"[bold red]Browser not reachable:[/bold red]\n{exc}")
            return 1
        logger.info("Attaching to browser via CDP", cdp_url=effective_cdp)
        console.print(f"[dim]Attaching to browser at {effective_cdp}[/dim]")
        browser = BrowserSession(cdp_url=effective_cdp)
    else:
        profile = BrowserProfile(headless=headless)
        logger.info("Launching browser", profile=profile.name)
        browser = BrowserSession(profile=profile)
    _browser_ref = browser

    # 7. Agent context ────────────────────────────────────────────────────────
    from agent.context import AgentContext
    from agent.views import AgentSettings
    agent_settings = AgentSettings(max_steps=max_steps, vision_enabled=vision)
    context = AgentContext.create(settings=agent_settings).with_llm(llm)

    # Subscribe live-progress callbacks ─────────────────────────────────────
    step_counter: list[int] = [0]
    callbacks = _make_event_callbacks(step_counter)
    for event_type, cb in callbacks.items():
        context.event_manager.subscribe(event_type, cb)

    # 8. Planner ──────────────────────────────────────────────────────────────
    from agent.builtin.planner import PlannerAgent
    planner = PlannerAgent(llm=llm, settings=agent_settings)

    # 9. Executor (navigator created after browser launches) ──────────────────
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
    headless: bool = typer.Option(False, "--headless/--no-headless", help="Run a new browser headless (ignored in CDP mode)"),
    max_steps: int = typer.Option(20, "--max-steps", help="Maximum executor steps"),
    vision: bool = typer.Option(False, "--vision", help="Enable screenshot vision"),
    cdp_url: str = typer.Option(
        "",
        "--cdp-url",
        envvar="BROWSER_CDP_URL",
        help="Attach to a running Chrome via CDP, e.g. http://localhost:9222",
        show_default=False,
    ),
    auto_attach: bool = typer.Option(
        False,
        "--attach",
        help="Auto-discover and attach to any running Chrome with --remote-debugging-port",
    ),
) -> None:
    """Execute a browser automation task end-to-end.

    By default a fresh isolated Playwright browser is launched.  To use your
    own running Chrome instead, either:

    \b
      --cdp-url http://localhost:9222   attach to a specific Chrome instance
      --attach                          auto-discover the first running Chrome

    Start Chrome with remote-debugging enabled:

    \b
      Linux/macOS: google-chrome --remote-debugging-port=9222 --no-first-run
      Windows:     scripts\\start_chrome_windows.bat
    """
    if not task:
        task = typer.prompt("Enter task")

    try:
        exit_code = asyncio.run(
            run_task(
                task,
                headless=headless,
                max_steps=max_steps,
                vision=vision,
                cdp_url=cdp_url,
                auto_attach=auto_attach,
            )
        )
    except KeyboardInterrupt:
        console.print("\n[bold red]Interrupted.[/bold red]")
        exit_code = 130

    raise typer.Exit(exit_code)


@app.command(name="discover")
def discover_cmd() -> None:
    """Scan this machine for running Chrome/Chromium instances with remote-debugging.

    Prints the CDP URL that can be passed to --cdp-url.
    """
    from browser.discover import _DEFAULT_PROBE_PORTS, _fetch_json_version, _scan_proc_for_debug_ports

    console.print("[bold]Scanning for running browsers with remote-debugging enabled…[/bold]")

    found: list[tuple[str, dict]] = []
    probe_ports: list[int] = []

    # Linux /proc scan
    proc_ports = _scan_proc_for_debug_ports()
    if proc_ports:
        console.print(f"  [dim]/proc scan found port(s):[/dim] {proc_ports}")
    for p in proc_ports + _DEFAULT_PROBE_PORTS:
        if p not in probe_ports:
            probe_ports.append(p)

    for port in probe_ports:
        info = _fetch_json_version("localhost", port)
        if info:
            url = f"http://localhost:{port}"
            found.append((url, info))

    if not found:
        console.print("[yellow]No running browsers found.[/yellow]")
        console.print()
        console.print("To enable remote debugging, start Chrome with:")
        console.print()
        console.print("  [cyan]Linux/macOS:[/cyan]")
        console.print("    google-chrome --remote-debugging-port=9222 --no-first-run")
        console.print()
        console.print("  [cyan]Windows (batch script):[/cyan]")
        console.print(r"    scripts\start_chrome_windows.bat")
        console.print()
        console.print("  [cyan]Windows (PowerShell):[/cyan]")
        console.print(r"    powershell -ExecutionPolicy Bypass -File scripts\start_chrome_windows.ps1")
        raise typer.Exit(1)

    from rich.table import Table
    table = Table(title="Running browsers", show_lines=True)
    table.add_column("CDP URL", style="cyan bold")
    table.add_column("Browser", style="green")
    table.add_column("Protocol version")
    for url, info in found:
        table.add_row(
            url,
            info.get("Browser", "unknown"),
            info.get("Protocol-Version", "-"),
        )
    console.print(table)
    console.print()
    console.print("[bold]Use one of the URLs above:[/bold]")
    console.print(f"  cowork-os --cdp-url {found[0][0]} \"your task here\"")
    console.print(f"  [dim]— or set [bold]BROWSER_CDP_URL={found[0][0]}[/bold] in .env[/dim]")


def run() -> None:
    """Alias for CLI entry point (used by pyproject.toml script)."""
    app()


if __name__ == "__main__":
    app()
