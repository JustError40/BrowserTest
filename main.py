"""coworkOS entry point."""

import asyncio
import sys

import typer
from rich.console import Console

console = Console()
app = typer.Typer(name="cowork-os", help="Browser Automation Platform")


async def main(task: str) -> None:
    """Run a browser automation task."""
    console.print(f"[bold blue]coworkOS[/bold blue] — running task: {task!r}")
    console.print("[yellow]Agent components not yet initialized (run further setup steps)[/yellow]")


@app.command()
def cli(
    task: str = typer.Argument(default="", help="Task to execute"),
) -> None:
    """Execute a browser automation task."""
    if not task:
        task = typer.prompt("Enter task")
    try:
        asyncio.run(main(task))
    except KeyboardInterrupt:
        console.print("\n[bold red]Interrupted by user. Shutting down...[/bold red]")
        sys.exit(0)


def run() -> None:
    """Alias for CLI entry point."""
    app()


if __name__ == "__main__":
    app()
