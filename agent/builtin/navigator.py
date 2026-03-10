"""NavigatorAgent: single-step browser-interaction agent driven by an LLM.

The NavigatorAgent receives a natural-language instruction, observes the
current browser state (DOM + optional screenshot), asks the LLM to choose
one tool from the ToolRegistry, executes it, and returns a NavigatorResult.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import structlog
from pydantic import BaseModel

from agent.base import BaseAgent
from agent.views import AgentSettings, StepResult
from browser.dom import DomService
from browser.session import BrowserSession
from tools.registry import registry
from vision.screenshot import ScreenshotService

logger = structlog.get_logger(__name__)

# Load system prompt templates once at import time
_PROMPT_PATH = Path(__file__).parent.parent / "prompts" / "navigator.md"
_PROMPT_VISION_PATH = Path(__file__).parent.parent / "prompts" / "navigator_vision.md"
_SYSTEM_PROMPT: str = _PROMPT_PATH.read_text(encoding="utf-8") if _PROMPT_PATH.exists() else ""
_SYSTEM_PROMPT_VISION: str = (
    _PROMPT_VISION_PATH.read_text(encoding="utf-8") if _PROMPT_VISION_PATH.exists() else _SYSTEM_PROMPT
)

_dom_service = DomService(viewport_expansion=500)
_screenshot_service = ScreenshotService(session_id="navigator")


def _get_tool_schema_hint() -> str:
    """Build a concise tool listing with param signatures for the LLM prompt."""
    schema = registry.get_schema()
    lines: list[str] = []
    for name, meta in schema.items():
        params_info: dict = meta.get("parameters", {})
        props: dict = params_info.get("properties", {})
        required: list = params_info.get("required", [])
        param_strs: list[str] = []
        for pname, pinfo in props.items():
            ptype = pinfo.get("type", "any")
            is_req = pname in required
            flag = "" if is_req else "?"
            param_strs.append(f"{pname}{flag}: {ptype}")
        params_hint = ", ".join(param_strs) if param_strs else "no params"
        lines.append(f"  - {name}({params_hint}): {meta['description']}")
    return "Available tools:\n" + "\n".join(lines)


def _instruction_requires_nav_menu(instruction: str) -> bool:
    """Detect instructions that should use robust header-navigation opening.

    Mirrors atlas-style deterministic grounding for fragile UI affordances
    (hamburger/avatar/account menu triggers).
    """
    text = instruction.lower()
    trigger_words = [
        "menu",
        "hamburger",
        "avatar",
        "profile",
        "account",
        "resume",
        "навигац",
        "меню",
        "аватар",
        "профил",
        "аккаунт",
        "резюм",
        "кабинет",
        "шапк",
    ]
    action_words = ["open", "click", "reveal", "show", "find", "открой", "нажми", "покажи"]
    return any(w in text for w in trigger_words) and any(a in text for a in action_words)


def _inject_snapshot_xpath(tool_name: str, tool_params: dict[str, Any], elements: list[Any]) -> dict[str, Any]:
    """Attach snapshot_xpath for index-based actions.

    This keeps tool execution anchored to the exact DOM snapshot used by the
    LLM decision (visual+parser sync), reducing index drift after dynamic
    re-rendering.
    """
    index_tools = {
        "click_element",
        "input_text",
        "select_option",
        "hover",
        "check_checkbox",
        "upload_file",
        "search_and_submit",
    }
    if tool_name not in index_tools:
        return tool_params
    if "snapshot_xpath" in tool_params:
        return tool_params

    idx = tool_params.get("index")
    if not isinstance(idx, int):
        return tool_params

    target = None
    if 0 <= idx < len(elements):
        target = elements[idx]
    elif 1 <= idx <= len(elements):
        target = elements[idx - 1]

    if target and getattr(target, "xpath", ""):
        merged = dict(tool_params)
        merged["snapshot_xpath"] = target.xpath
        return merged
    return tool_params

# ---------------------------------------------------------------------------
# Result model
# ---------------------------------------------------------------------------


class NavigatorResult(BaseModel):
    """Result of a single navigator step."""

    action_taken: str = ""
    """Name of the tool that was executed."""

    success: bool = True
    """True if the action was carried out without errors."""

    observation: str = ""
    """What was observed / extracted after the action."""

    done: bool = False
    """True when the instruction is considered complete."""

    data: dict[str, Any] = {}
    """Optional structured data from the action."""


# ---------------------------------------------------------------------------
# Agent implementation
# ---------------------------------------------------------------------------


class NavigatorAgent(BaseAgent):
    """Browser-interaction agent that executes one tool per step.

    Args:
        llm:             An instance of BaseLLM (or compatible mock).
        browser_session: Active BrowserSession to operate on.
        instruction:     Default instruction for the agent's run() loop.
        settings:        Optional AgentSettings.
    """

    def __init__(
        self,
        llm,
        browser_session: BrowserSession,
        instruction: str = "",
        settings: AgentSettings | None = None,
    ) -> None:
        super().__init__(settings=settings)
        self.llm = llm
        self.browser_session = browser_session
        self.instruction = instruction

    # ------------------------------------------------------------------
    # BaseAgent interface
    # ------------------------------------------------------------------

    async def _step(self) -> StepResult:
        """Execute one step using the stored instruction and session."""
        result = await self.execute_step(self.instruction, self.browser_session)
        return StepResult(
            action=result.action_taken,
            output=result.observation,
            done=result.done,
            success=result.success,
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def execute_step(
        self,
        instruction: str,
        session: BrowserSession,
    ) -> NavigatorResult:
        """Observe browser state, ask LLM for a tool, execute it.

        Args:
            instruction: Natural-language instruction (e.g. "Go to example.com").
            session:     Active BrowserSession.

        Returns:
            NavigatorResult with action_taken, success, observation, done.
        """
        # 1. Observe current browser state --------------------------------
        page = session.get_current_page()
        current_url = page.url
        page_title: str = ""
        try:
            page_title = await page.title()
        except Exception:
            pass

        elements = await _dom_service.get_interactive_elements(page)
        dom_text = _dom_service.format_for_llm(elements)

        # 2. Capture SoM-annotated screenshot when vision is enabled ------
        # SoM = Set-of-Marks: numbered coloured badges injected into the
        # live DOM (Skyvern-style) so the LLM can visually identify elements
        # by the same [N] indices shown in the DOM text below.
        screenshot_bytes: bytes | None = None
        if self.settings.vision_enabled:
            try:
                screenshot_bytes = await _screenshot_service.capture_som(page, elements)
            except Exception as exc:
                logger.warning("som_screenshot_failed", error=str(exc))
                try:
                    screenshot_bytes = await page.screenshot(type="jpeg", quality=70)
                except Exception as exc2:
                    logger.debug("plain_screenshot_failed", error=str(exc2))

        # 3. Build LLM prompt ---------------------------------------------
        page_info = f"Title: {page_title}\nURL: {current_url}"
        elem_count = len(elements)
        dom_section = (
            f"Interactive elements ({elem_count} total):\n"
            f"{dom_text or '(none detected)'}"
        )
        vision_note = (
            "\n[VISION] The screenshot has coloured numbered badges matching the "
            "element indices above. Blue=link, Green=button, Amber=input/select."
            if screenshot_bytes
            else ""
        )
        user_content = (
            f"{page_info}\n\n"
            f"{_get_tool_schema_hint()}\n\n"
            f"{dom_section}{vision_note}\n\n"
            f"Instruction: {instruction}\n\n"
            "Respond with JSON: {\"tool\": \"<name>\", \"params\": {...}}"
        )

        system_prompt = _SYSTEM_PROMPT_VISION if self.settings.vision_enabled else _SYSTEM_PROMPT
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_content},
        ]

        images = []
        if screenshot_bytes:
            images = [screenshot_bytes]

        # 4. Call LLM with retry ------------------------------------------
        tool_call: dict[str, Any] | None = None
        last_error: str = ""

        for attempt in range(1, 4):  # up to 3 retries
            try:
                raw = await self.llm.generate(messages, images=images if images else None)
                tool_call = _parse_tool_call(raw)
                if tool_call:
                    break
                last_error = f"LLM response not parseable: {raw[:200]}"
            except Exception as exc:
                last_error = str(exc)
                logger.warning("LLM call failed", attempt=attempt, error=last_error)

        if not tool_call:
            logger.warning("navigator: no valid tool call from LLM", error=last_error)
            return NavigatorResult(
                success=False,
                observation=f"Failed to get valid tool choice from LLM: {last_error}",
            )

        # 5. Execute the chosen tool --------------------------------------
        tool_name = tool_call.get("tool", "")
        tool_params = tool_call.get("params", {})

        # Deterministic UI-grounding override: for header/profile menu tasks,
        # prefer robust selector-based helper over brittle index clicks.
        if _instruction_requires_nav_menu(instruction) and tool_name in {
            "click_element",
            "find_elements_by_selector",
            "scroll_page",
        }:
            logger.info(
                "navigator: override tool to open_navigation_menu",
                original_tool=tool_name,
                instruction=instruction[:80],
            )
            tool_name = "open_navigation_menu"
            tool_params = {}

        tool_params = _inject_snapshot_xpath(tool_name, tool_params, elements)

        logger.info(
            "navigator: executing tool",
            tool=tool_name,
            params=str(tool_params)[:80],
            instruction=instruction[:60],
        )

        action_result = await registry.execute(tool_name, tool_params, session)

        done_flag = bool(action_result.data.get("done", False)) if action_result.data else False

        logger.info(
            "navigator: tool result",
            tool=tool_name,
            success=action_result.success,
            done=done_flag,
        )

        return NavigatorResult(
            action_taken=tool_name,
            success=action_result.success,
            observation=action_result.extracted_content or action_result.error or "",
            done=done_flag,
            data=action_result.data or {},
        )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _parse_tool_call(raw: str) -> dict[str, Any] | None:
    """Extract JSON tool call from raw LLM output.

    Handles:
    - Plain JSON string
    - JSON wrapped in a markdown code block (```json ... ```)
    """
    if not raw:
        return None

    # Strip markdown code fences if present
    cleaned = re.sub(r"```(?:json)?\s*", "", raw).replace("```", "").strip()

    # Try to find the first {...} block
    match = re.search(r"\{.*\}", cleaned, re.DOTALL)
    if match:
        cleaned = match.group(0)

    try:
        parsed = json.loads(cleaned)
        if isinstance(parsed, dict) and "tool" in parsed:
            if "params" not in parsed:
                parsed["params"] = {}
            return parsed
    except json.JSONDecodeError:
        pass

    return None
