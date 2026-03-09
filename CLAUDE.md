# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

**coworkOS** is a browser automation platform implementing a multi-agent orchestration system for complex web tasks. Agents: **PlannerAgent** (task decomposition) → **NavigatorAgent** (browser execution), orchestrated by **Executor**. Supports Anthropic, OpenAI, Ollama, and ZAI LLM providers.

## Commands

```bash
# Setup
uv sync                              # Install dependencies
uv run playwright install chromium   # Download browser

# Run
uv run cowork-os "your task here"    # Execute a task
uv run cowork-os "task" --headless --max-steps 30 --vision

# Lint
uv run ruff check .                  # Must pass with zero errors
uv run ruff format .                 # Auto-format

# Test
uv run pytest tests/                 # Run all tests
uv run pytest tests/test_foo.py -k "test_name"  # Single test
```

## Architecture

### Agent Flow
```
main.py → Executor.run(task, context)
             ├── PlannerAgent.plan(task)   → PlannerOutput(next_steps, done)
             ├── NavigatorAgent.execute_step(instruction, browser)  → NavigatorResult
             └── [replan on failure] → loop until done or max_steps
```

### Key Modules

| Path | Role |
|------|------|
| `agent/executor.py` | Orchestrates Planner ↔ Navigator loop; emits events |
| `agent/builtin/planner.py` | LLM-based high-level task planner |
| `agent/builtin/navigator.py` | LLM-based step executor (DOM + vision) |
| `agent/context.py` | `AgentContext` — shared state (LLM, browser, settings) |
| `agent/event.py` | `EventManager` pub/sub (TASK_START, STEP_END, etc.) |
| `browser/session.py` | Playwright lifecycle (local Chromium or CDP attach) |
| `browser/dom.py` | Extracts interactive elements from DOM |
| `tools/registry.py` | Dynamic action registry with Pydantic schema generation |
| `tools/builtin.py` | 14+ built-in actions (navigate, click, type, extract…) |
| `memory/message_manager.py` | Token-budget-aware conversation history with compaction |
| `config/settings.py` | `Settings(BaseSettings)` loaded from `.env` |

### Adding a Custom Action

```python
# In tools/my_actions.py
from tools.registry import registry
from tools.views import ActionResult

@registry.action(description="My action description")
async def my_action(param: str, browser_session: BrowserSession) -> ActionResult:
    page = browser_session.get_current_page()
    return ActionResult.ok(content="result")

# In main.py, add import to register:
import tools.my_actions  # side-effect registers the action
```

### Browser Modes

- **Local**: launches isolated Chromium (default)
- **CDP**: connects to existing Chrome — set `BROWSER_CDP_URL=http://localhost:9222` in `.env`

## Environment

Copy `.env.example` → `.env`. Required for chosen provider:

```
LLM_PROVIDER=anthropic          # anthropic | openai | ollama | zai
LLM_MODEL=claude-haiku-4-5-20251001
ANTHROPIC_API_KEY=...
BROWSER_HEADLESS=true
MAX_STEPS=50
TOKEN_BUDGET=100000
LOG_LEVEL=INFO
```

## Code Conventions

- All I/O is `async/await` — never block the event loop
- Use `structlog.get_logger(__name__)` for logging; pass structured key-value pairs, not f-strings
- Use Pydantic v2 models for all data contracts
- Line length: 120; target Python 3.12+
- `tools/builtin.py` must be imported (not just defined) for actions to register — `import tools.builtin` in `main.py`

## Implementation Status

Phase 0–2 complete (core infra, browser, agents, LLM, tools, memory, vision).
Phase 3–4 pending: `WorkflowEngine`, REST API (`ui/`), React frontend, persistent memory.
Task tracking in `tasks.json` (28 tasks); roadmap in `MULTI_AGENT_PLAN.md`.
