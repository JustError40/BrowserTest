# coworkOS

Browser Automation Platform — multi-agent OS for web tasks.

## Overview

coworkOS is a browser automation platform that combines the best patterns from:
- **browser-use** — Python, CDP-based, event-driven
- **nanobrowser** — Multi-agent Planner/Navigator pattern
- **skyvern** — Enterprise Playwright workflow engine

## Quick Start

```bash
# Install dependencies
uv sync

# Copy and configure environment
cp .env.example .env
# Edit .env with your API keys

# Run a task
uv run cowork-os "Open example.com and tell me the page title"
```

## Architecture

```
cowork_os/
  agent/          ← Orchestrator + specialized agents (Planner, Navigator)
  browser/        ← Browser management (Playwright + CDP)
  config/         ← Configuration (LLM providers, settings)
  memory/         ← State management + message history
  tools/          ← Action registry (click, type, navigate...)
  ui/             ← REST API + React frontend
  vision/         ← Screenshot analysis, DOM pipeline
```

## Requirements

- Python >= 3.12
- Playwright browsers (installed via `playwright install chromium`)
- Anthropic or OpenAI API key
