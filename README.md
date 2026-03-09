# coworkOS

Browser Automation Platform — multi-agent OS for web tasks.


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

## Running on Windows (direct Chrome connection)

The agent can control your real Chrome browser on Windows instead of launching a headless one.
This gives it access to your existing sessions, cookies, and logins.

**Step 1 — Launch Chrome with remote debugging** (run in Windows PowerShell or cmd, leave it open):

> **Important — two requirements for CDP to work:**
> 1. `--user-data-dir` must point to a **custom directory** (without it Chrome ignores the debug port)
> 2. No other Chrome must be running with the **same** user-data-dir (the new process just reuses the old one)

In PowerShell (kill any existing Chrome first, then start fresh):

```powershell
taskkill /F /IM chrome.exe 2>$null; Start-Sleep 1
& "C:\Program Files\Google\Chrome\Application\chrome.exe" `
  --remote-debugging-port=9222 `
  --user-data-dir="$env:TEMP\chrome-coworkos" `
  --no-first-run --no-default-browser-check
```

In cmd:

```bat
taskkill /F /IM chrome.exe
ping -n 2 127.0.0.1 >nul
"C:\Program Files\Google\Chrome\Application\chrome.exe" ^
  --remote-debugging-port=9222 ^
  --user-data-dir="%TEMP%\chrome-coworkos" ^
  --no-first-run --no-default-browser-check
```

> If Chrome is installed in a different location, check `%LOCALAPPDATA%\Google\Chrome\Application\` or `%PROGRAMFILES(X86)%\Google\Chrome\Application\`.

**Step 2 — Set the CDP URL in `.env`**

| Where the agent runs | Value |
|---|---|
| Windows (same machine) | `BROWSER_CDP_URL=http://localhost:9222` |
| WSL 2 | `BROWSER_CDP_URL=http://172.24.80.1:9222` *(replace IP: run `ip route show \| grep default` in WSL)* |
| Docker on Windows | `BROWSER_CDP_URL=http://host.docker.internal:9222` |

**Step 3 — Verify Chrome is reachable:**

In PowerShell:
```powershell
Invoke-WebRequest http://localhost:9222/json/version | Select-Object -ExpandProperty Content
```

In cmd:
```bat
curl.exe http://localhost:9222/json/version
```

In WSL:
```bash
curl http://172.24.80.1:9222/json/version
```

Expected response: JSON with `"Browser": "Chrome/..."`.

**Step 4 — Run the agent** (from WSL / Linux / Docker):

```bash
uv run cowork-os "Open example.com and tell me the page title"
```

The agent will connect to your running Chrome window and operate it directly.
Leave `BROWSER_CDP_URL` empty to fall back to an isolated Playwright browser.

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
