"""Built-in browser tools registered in the global ToolRegistry.

Import this module to register all standard browser actions:
    navigate_to, go_back, click_element, input_text, send_keys,
    scroll_page, extract_content, read_page_text,
    search_page_text, find_elements_by_selector,
    get_page_state, done, wait
"""

import asyncio
import json
from typing import Literal

import structlog

from browser.dom import DomService
from browser.session import BrowserSession
from tools.registry import registry
from tools.views import ActionResult

logger = structlog.get_logger(__name__)

_dom_service = DomService(viewport_expansion=200)


# ── Shared helper ─────────────────────────────────────────────────────────────


async def _page_summary(page) -> str:
    """Return 'URL: ... | Title: ...' for use in action result observations."""
    try:
        url = page.url
        title = await page.title()
        return f"URL: {url} | Title: {title}"
    except Exception:
        return ""


# ---------------------------------------------------------------------------
# 1. navigate_to
# ---------------------------------------------------------------------------


@registry.action(
    description=(
        "Navigate to a URL in the current browser tab. "
        "Use for any 'go to', 'open', 'visit' instruction. "
        "Always provide a fully-qualified URL starting with http:// or https://."
    )
)
async def navigate_to(url: str, browser_session: BrowserSession) -> ActionResult:
    """Navigate to the given URL and wait for the page to load."""
    try:
        page = browser_session.get_current_page()
        await page.goto(url, wait_until="domcontentloaded", timeout=30_000)
        summary = await _page_summary(page)
        logger.debug("navigated", url=url)
        return ActionResult.ok(content=f"Navigated to {url}. {summary}")
    except Exception as exc:
        logger.warning("navigate_to failed", url=url, error=str(exc))
        return ActionResult.fail(error=str(exc))


# ---------------------------------------------------------------------------
# 2. go_back
# ---------------------------------------------------------------------------


@registry.action(
    description=(
        "Navigate back to the previous page in browser history. "
        "Use when you clicked something that led to the wrong page, "
        "or when the task requires returning to a previous step."
    )
)
async def go_back(browser_session: BrowserSession) -> ActionResult:
    """Go back one step in the browser history."""
    try:
        page = browser_session.get_current_page()
        await page.go_back(wait_until="domcontentloaded", timeout=15_000)
        summary = await _page_summary(page)
        logger.debug("go_back", summary=summary)
        return ActionResult.ok(content=f"Went back. {summary}")
    except Exception as exc:
        logger.warning("go_back failed", error=str(exc))
        return ActionResult.fail(error=str(exc))


# ---------------------------------------------------------------------------
# 3. click_element
# ---------------------------------------------------------------------------


@registry.action(
    description=(
        "Click a numbered interactive element on the page. "
        "Use the integer `index` from the DOM elements list shown in context. "
        "After clicking, the result includes the new page URL and title so you know what changed."
    )
)
async def click_element(index: int, browser_session: BrowserSession) -> ActionResult:
    """Click the element with the given index from get_interactive_elements()."""
    try:
        page = browser_session.get_current_page()
        elements = await _dom_service.get_interactive_elements(page)

        target = None
        if 0 <= index < len(elements):
            target = elements[index]
        elif 1 <= index <= len(elements):
            target = elements[index - 1]

        if target is None:
            return ActionResult.fail(error=f"Element index {index} not found. Page has {len(elements)} interactive elements.")

        bb = target.bounding_box
        if bb:
            x = bb.x + bb.width / 2
            y = bb.y + bb.height / 2
            await page.mouse.click(x, y)
        else:
            await page.evaluate(
                "(idx) => { document.querySelectorAll('a,button,input,select,textarea')[idx]?.click(); }",
                index,
            )

        # Brief wait for page to possibly change
        await asyncio.sleep(0.5)
        summary = await _page_summary(page)
        elem_label = (target.text or "")[:60]
        logger.debug("clicked element", index=index, text=elem_label)
        return ActionResult.ok(content=f"Clicked element {index} ({elem_label}). {summary}")
    except Exception as exc:
        logger.warning("click_element failed", index=index, error=str(exc))
        return ActionResult.fail(error=str(exc))


# ---------------------------------------------------------------------------
# 4. input_text
# ---------------------------------------------------------------------------


@registry.action(
    description=(
        "Type text into a numbered input field on the page. "
        "Clears the field first, then types. "
        "Use `index` from the DOM elements list."
    )
)
async def input_text(index: int, text: str, browser_session: BrowserSession) -> ActionResult:
    """Clear and type text into the element at the given index."""
    try:
        page = browser_session.get_current_page()
        elements = await _dom_service.get_interactive_elements(page)

        target = None
        if 0 <= index < len(elements):
            target = elements[index]
        elif 1 <= index <= len(elements):
            target = elements[index - 1]

        if target is None:
            return ActionResult.fail(error=f"Element index {index} not found.")

        bb = target.bounding_box
        if bb:
            x = bb.x + bb.width / 2
            y = bb.y + bb.height / 2
            await page.mouse.click(x, y)

        await page.keyboard.press("Control+a")
        await page.keyboard.type(text)
        logger.debug("input_text", index=index, text=text[:40])
        return ActionResult.ok(content=f"Typed '{text}' into element {index}")
    except Exception as exc:
        logger.warning("input_text failed", index=index, error=str(exc))
        return ActionResult.fail(error=str(exc))


# ---------------------------------------------------------------------------
# 5. send_keys
# ---------------------------------------------------------------------------


@registry.action(
    description=(
        "Send keyboard keys or shortcuts to the browser. "
        "Use for: Enter (submit form/search), Tab (move focus), Escape (close modal), "
        "F5 (refresh), Ctrl+A (select all), ArrowDown/ArrowUp (navigate lists). "
        "Examples: 'Enter', 'Tab', 'Escape', 'Control+Enter', 'ArrowDown'."
    )
)
async def send_keys(keys: str, browser_session: BrowserSession) -> ActionResult:
    """Send key(s) to the currently focused element."""
    try:
        page = browser_session.get_current_page()
        await page.keyboard.press(keys)
        await asyncio.sleep(0.3)
        summary = await _page_summary(page)
        logger.debug("send_keys", keys=keys)
        return ActionResult.ok(content=f"Pressed '{keys}'. {summary}")
    except Exception as exc:
        logger.warning("send_keys failed", keys=keys, error=str(exc))
        return ActionResult.fail(error=str(exc))


# ---------------------------------------------------------------------------
# 6. scroll_page
# ---------------------------------------------------------------------------


@registry.action(
    description=(
        "Scroll the page up or down. "
        "`direction`: 'up' or 'down'. "
        "`amount`: pixels to scroll (default 600, use 3000+ to reach bottom of long pages)."
    )
)
async def scroll_page(
    direction: Literal["up", "down"],
    browser_session: BrowserSession,
    amount: int = 600,
) -> ActionResult:
    """Scroll the page in the given direction."""
    try:
        page = browser_session.get_current_page()
        delta = amount if direction == "down" else -amount
        await page.evaluate(f"window.scrollBy(0, {delta})")
        logger.debug("scrolled", direction=direction, amount=amount)
        return ActionResult.ok(content=f"Scrolled {direction} by {amount}px")
    except Exception as exc:
        logger.warning("scroll_page failed", error=str(exc))
        return ActionResult.fail(error=str(exc))


# ---------------------------------------------------------------------------
# 7. get_page_state
# ---------------------------------------------------------------------------


@registry.action(
    description=(
        "Get the current page state: URL, title, and a summary of visible text. "
        "Use AFTER a click or navigation to confirm what page you are on. "
        "Also use when you're unsure of the current page before deciding what to do next."
    )
)
async def get_page_state(browser_session: BrowserSession) -> ActionResult:
    """Return current URL, page title, and first 1500 chars of visible text."""
    try:
        page = browser_session.get_current_page()
        url = page.url
        title = await page.title()
        text: str = await page.evaluate(
            """() => {
                function getText(node, depth) {
                    if (depth > 8) return '';
                    if (node.nodeType === Node.TEXT_NODE) return node.textContent.trim();
                    if (['SCRIPT','STYLE','NOSCRIPT'].includes(node.tagName)) return '';
                    return Array.from(node.childNodes).map(c => getText(c, depth+1)).join(' ');
                }
                return getText(document.body, 0).replace(/\\s+/g, ' ').trim();
            }"""
        )
        snippet = text[:1500].strip()
        result = f"URL: {url}\nTitle: {title}\n\nPage text (first 1500 chars):\n{snippet}"
        return ActionResult.ok(content=result)
    except Exception as exc:
        logger.warning("get_page_state failed", error=str(exc))
        return ActionResult.fail(error=str(exc))


# ---------------------------------------------------------------------------
# 8. read_page_text
# ---------------------------------------------------------------------------


@registry.action(
    description=(
        "Read the full readable text of the current page (up to 6000 chars). "
        "Use to extract FAQ answers, article content, product descriptions, "
        "table data, or any text content from the page. "
        "Better than extract_content when you need complete structured text."
    )
)
async def read_page_text(
    browser_session: BrowserSession,
    selector: str = "",
    max_chars: int = 6000,
) -> ActionResult:
    """Extract all readable text from the page or from elements matching a CSS selector."""
    try:
        page = browser_session.get_current_page()
        title = await page.title()
        url = page.url

        if selector:
            content: str = await page.evaluate(
                """(sel) => {
                    var els = document.querySelectorAll(sel);
                    return Array.from(els).map(e => {
                        var t = e.innerText || e.textContent || '';
                        return t.replace(/[ \\t]+/g, ' ').trim();
                    }).filter(Boolean).join('\\n\\n');
                }""",
                selector,
            )
            if not content:
                content = f"No elements found matching selector '{selector}'"
        else:
            content = await page.evaluate(
                """() => document.body.innerText.replace(/[ \\t]+/g, ' ').replace(/\\n{3,}/g, '\\n\\n').trim()"""
            )

        if len(content) > max_chars:
            content = content[:max_chars] + f"\n\n[...truncated at {max_chars} chars]"

        result = f"URL: {url}\nTitle: {title}\n\n{content}"
        logger.debug("read_page_text", url=url, chars=len(content), selector=selector)
        return ActionResult.ok(content=result)
    except Exception as exc:
        logger.warning("read_page_text failed", error=str(exc))
        return ActionResult.fail(error=str(exc))


# ---------------------------------------------------------------------------
# 9. search_page_text
# ---------------------------------------------------------------------------

_SEARCH_JS = """\
(function() {
  var PATTERN = PAT_PLACEHOLDER;
  var CONTEXT = 150;
  var MAX = 20;
  try {
    var walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
    var full = ''; var offsets = [];
    while (walker.nextNode()) {
      var n = walker.currentNode, t = n.textContent;
      if (t && t.trim()) { offsets.push({o: full.length, l: t.length, n: n}); full += t; }
    }
    var flags = IS_RE_PLACEHOLDER ? 'gi' : 'gi';
    var re = IS_RE_PLACEHOLDER
      ? new RegExp(PATTERN, 'gi')
      : new RegExp(PATTERN.replace(/[.*+?^${}()|[\\]\\\\]/g, '\\\\$&'), 'gi');
    var matches = []; var m; var total = 0;
    while ((m = re.exec(full)) !== null) {
      total++;
      if (matches.length < MAX) {
        var s = Math.max(0, m.index - CONTEXT), e = Math.min(full.length, m.index + m[0].length + CONTEXT);
        matches.push({ctx: (s>0?'...':'')+full.slice(s,e)+(e<full.length?'...':'')});
      }
      if (m[0].length === 0) re.lastIndex++;
    }
    return {matches: matches, total: total};
  } catch(e) { return {matches:[], total:0, error: e.message}; }
})()
"""


@registry.action(
    description=(
        "Search page text for a word or phrase (like Ctrl+F / grep). "
        "Returns matching snippets with surrounding context. Zero LLM cost, instant. "
        "Use to find specific text, confirm a section exists, or locate data. "
        "`pattern`: text to search for. `is_regex`: set true for regex patterns."
    )
)
async def search_page_text(
    pattern: str,
    browser_session: BrowserSession,
    is_regex: bool = False,
) -> ActionResult:
    """Grep the page text for a pattern, return matches with context."""
    try:
        page = browser_session.get_current_page()
        js = _SEARCH_JS.replace("PAT_PLACEHOLDER", json.dumps(pattern)).replace(
            "IS_RE_PLACEHOLDER", "true" if is_regex else "false"
        )
        data: dict = await page.evaluate(f"() => {{ return {js} }}")
        if data.get("error"):
            return ActionResult.fail(error=f"search error: {data['error']}")

        total = data.get("total", 0)
        matches = data.get("matches", [])
        if total == 0:
            return ActionResult.ok(content=f'No matches found for "{pattern}" on this page.')

        lines = [f'Found {total} match{"es" if total != 1 else ""} for "{pattern}":']
        for i, m in enumerate(matches, 1):
            lines.append(f"  [{i}] {m['ctx']}")
        if total > len(matches):
            lines.append(f"  ... showing {len(matches)} of {total} total matches")
        return ActionResult.ok(content="\n".join(lines))
    except Exception as exc:
        logger.warning("search_page_text failed", pattern=pattern, error=str(exc))
        return ActionResult.fail(error=str(exc))


# ---------------------------------------------------------------------------
# 10. find_elements_by_selector
# ---------------------------------------------------------------------------


@registry.action(
    description=(
        "Find DOM elements matching a CSS selector and return their text and attributes. "
        "Zero LLM cost, instant. Use to: "
        "find all links ('a'), headings ('h1,h2,h3'), FAQ items ('.faq-item'), "
        "buttons ('button'), form fields ('input'), table rows ('tr'). "
        "`selector`: any valid CSS selector. "
        "`attributes`: optional list of HTML attributes to extract, e.g. ['href', 'class']."
    )
)
async def find_elements_by_selector(
    selector: str,
    browser_session: BrowserSession,
    attributes: list[str] | None = None,
    max_results: int = 30,
) -> ActionResult:
    """Query elements by CSS selector, return text + requested attributes."""
    try:
        page = browser_session.get_current_page()
        attrs = attributes or []
        result: list[dict] = await page.evaluate(
            """([sel, attrs, maxR]) => {
                var els = Array.from(document.querySelectorAll(sel)).slice(0, maxR);
                return els.map(function(el, i) {
                    var text = (el.innerText || el.textContent || '').replace(/\\s+/g,' ').trim().slice(0,300);
                    var out = {index: i, tag: el.tagName.toLowerCase(), text: text};
                    attrs.forEach(function(a) {
                        var v = el.getAttribute(a);
                        if (v !== null) out[a] = v.slice(0, 200);
                    });
                    return out;
                });
            }""",
            [selector, attrs, max_results],
        )
        total_on_page: int = await page.evaluate(
            f"() => document.querySelectorAll({json.dumps(selector)}).length"
        )
        if not result:
            return ActionResult.ok(content=f'No elements found matching "{selector}".')

        lines = [f'Found {total_on_page} element(s) matching "{selector}" (showing {len(result)}):']
        for el in result:
            tag = el.get("tag", "?")
            text = el.get("text", "")
            attr_parts = [f'{k}="{el[k]}"' for k in attrs if k in el]
            attr_str = " " + " ".join(attr_parts) if attr_parts else ""
            lines.append(f"  <{tag}{attr_str}> {text}")

        if total_on_page > len(result):
            lines.append(f"  ... {total_on_page - len(result)} more (increase max_results to see them)")
        return ActionResult.ok(content="\n".join(lines))
    except Exception as exc:
        logger.warning("find_elements_by_selector failed", selector=selector, error=str(exc))
        return ActionResult.fail(error=str(exc))


# ---------------------------------------------------------------------------
# 11. extract_content  (existing, improved)
# ---------------------------------------------------------------------------


@registry.action(
    description=(
        "Extract text content from the current page. "
        "Use 'goal' to describe what to extract (e.g. 'page title', 'FAQ answers', 'product price'). "
        "Optionally pass 'selector' (CSS selector) to restrict extraction to matching elements, "
        "e.g. selector='title' for <title>, selector='h1' for headings, "
        "selector='.faq' for FAQ sections."
    )
)
async def extract_content(
    browser_session: BrowserSession,
    goal: str = "",
    selector: str | None = None,
) -> ActionResult:
    """Extract visible text content from the page, optionally filtered by a CSS selector."""
    try:
        page = browser_session.get_current_page()
        title: str = await page.title()
        url = page.url

        if selector:
            content: str = await page.evaluate(
                """(sel) => {
                    var els = document.querySelectorAll(sel);
                    return Array.from(els)
                        .map(e => (e.innerText || e.textContent || '').trim())
                        .filter(Boolean).join('\\n');
                }""",
                selector,
            )
            if not content:
                content = f"No elements found matching selector '{selector}'"
        else:
            content = await page.evaluate(
                """() => {
                    function getText(node, depth) {
                        if (depth > 10) return '';
                        if (node.nodeType === Node.TEXT_NODE) return node.textContent.trim();
                        if (['SCRIPT','STYLE','NOSCRIPT'].includes(node.tagName)) return '';
                        return Array.from(node.childNodes).map(c => getText(c, depth+1)).join(' ');
                    }
                    return getText(document.body, 0).replace(/\\s+/g, ' ').trim();
                }"""
            )

        max_len = 4000
        if len(content) > max_len:
            content = content[:max_len] + "…"
        result_text = f"URL: {url}\nTitle: {title}\n\n{content}"
        logger.debug("extract_content", goal=goal, selector=selector, content_len=len(content))
        return ActionResult.ok(content=result_text)
    except Exception as exc:
        logger.warning("extract_content failed", error=str(exc))
        return ActionResult.fail(error=str(exc))


# ---------------------------------------------------------------------------
# 12. done
# ---------------------------------------------------------------------------


@registry.action(
    description=(
        "Signal that the current instruction or full task is complete. "
        "Set `message` to a clear summary of the result or answer. "
        "Set `success=false` only if the task genuinely failed. "
        "IMPORTANT: Always call done AFTER you have the result — "
        "never call done as a shortcut instead of extracting information."
    )
)
async def done(
    browser_session: BrowserSession,
    message: str = "",
    output: str = "",
    success: bool = True,
) -> ActionResult:
    """Mark the current task as done. 'message' and 'output' are interchangeable."""
    text = message or output
    logger.info("task done", success=success, message=text[:100])
    return ActionResult(
        success=success,
        extracted_content=text,
        data={"done": True, "success": success},
    )


# ---------------------------------------------------------------------------
# 13. wait
# ---------------------------------------------------------------------------


@registry.action(
    description=(
        "Wait for a specified number of seconds. "
        "Use when: the page is loading, a dialog is appearing, "
        "or an animation needs to finish before the next action. "
        "`seconds`: 1–30 (default 2)."
    )
)
async def wait(
    browser_session: BrowserSession,
    seconds: int = 2,
) -> ActionResult:
    """Pause execution for the given number of seconds (max 30)."""
    secs = min(max(int(seconds), 0), 30)
    await asyncio.sleep(secs)
    logger.debug("waited", seconds=secs)
    return ActionResult.ok(content=f"Waited {secs} seconds")

