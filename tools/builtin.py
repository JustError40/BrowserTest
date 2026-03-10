"""Built-in browser tools registered in the global ToolRegistry.

Import this module to register all standard browser actions:
    navigate_to, go_back, click_element, input_text, send_keys,
    scroll_page, extract_content, read_page_text,
    search_page_text, find_elements_by_selector,
    get_page_state, done, wait,
    select_option, hover, check_checkbox, upload_file, reload_page,
    open_navigation_menu, search_and_submit
"""

import asyncio
import json
import os
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
            return ActionResult.fail(
                error=f"Element index {index} not found. Page has {len(elements)} interactive elements."
            )

        elem_label = (target.text or "")[:60]

        # Strategy 1: XPath locator — handles scroll-into-view, overlays, shadow DOM
        if target.xpath:
            try:
                locator = page.locator(f"xpath={target.xpath}")
                await locator.scroll_into_view_if_needed(timeout=5_000)
                await locator.click(timeout=8_000)
                await asyncio.sleep(0.4)
                summary = await _page_summary(page)
                logger.debug("click_element via locator", index=index, text=elem_label)
                return ActionResult.ok(content=f"Clicked element {index} ({elem_label}). {summary}")
            except Exception:
                pass  # fall through

        # Strategy 2: coordinate mouse click
        bb = target.bounding_box
        if bb:
            try:
                x = bb.x + bb.width / 2
                y = bb.y + bb.height / 2
                await page.mouse.click(x, y)
                await asyncio.sleep(0.4)
                summary = await _page_summary(page)
                logger.debug("click_element via coords", index=index, text=elem_label)
                return ActionResult.ok(content=f"Clicked element {index} ({elem_label}). {summary}")
            except Exception:
                pass  # fall through

        # Strategy 3: JS element.click() — bypasses Playwright pointer-events
        # interception layer, works on React portals and detached overlays.
        # Modelled after browser-use's final JS fallback.
        if target.xpath:
            try:
                await page.evaluate(
                    """(xp) => {
                        const el = document.evaluate(xp, document, null,
                            XPathResult.FIRST_ORDERED_NODE_TYPE, null).singleNodeValue;
                        if (el) {
                            el.scrollIntoView({block: 'center', inline: 'center'});
                            el.click();
                        }
                    }""",
                    target.xpath,
                )
                await asyncio.sleep(0.4)
                summary = await _page_summary(page)
                logger.debug("click_element via JS el.click()", index=index, text=elem_label)
                return ActionResult.ok(content=f"Clicked element {index} ({elem_label}). {summary}")
            except Exception:
                pass  # fall through

        # Strategy 4: Playwright dispatch_event — fires a real MouseEvent that
        # bypasses actionability checks (pointer-events: none, hidden, etc.).
        if target.xpath:
            try:
                locator = page.locator(f"xpath={target.xpath}")
                await locator.dispatch_event("click", timeout=8_000)
                await asyncio.sleep(0.4)
                summary = await _page_summary(page)
                logger.debug("click_element via dispatch_event", index=index, text=elem_label)
                return ActionResult.ok(content=f"Clicked element {index} ({elem_label}). {summary}")
            except Exception:
                pass

        # All strategies failed — last-resort index-based JS click
        await page.evaluate(
            "(idx) => { document.querySelectorAll('a,button,input,select,textarea')[idx]?.click(); }",
            index,
        )
        await asyncio.sleep(0.4)
        summary = await _page_summary(page)
        logger.debug("click_element via index JS fallback", index=index, text=elem_label)
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
        "Use `index` from the DOM elements list. "
        "Works with React / SPA controlled inputs (hh.ru, etc.)."
    )
)
async def input_text(index: int, text: str, browser_session: BrowserSession) -> ActionResult:
    """Clear and type text into the element at the given index.

    Strategy priority (most reliable first):
    1. XPath locator + locator.fill() — fires correct synthetic events for React/Vue/Angular.
    2. Coordinate click + triple-click-select-all + keyboard.type() — plain HTML fallback.
    """
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

        # ── Strategy 1: XPath locator.fill() ────────────────────────────────
        # Playwright fill() dispatches focus→input→change events that React expects.
        if target.xpath:
            try:
                locator = page.locator(f"xpath={target.xpath}")
                await locator.scroll_into_view_if_needed(timeout=5_000)
                await locator.click(timeout=5_000)   # focus first
                await asyncio.sleep(0.15)
                await locator.fill(text, timeout=8_000)
                logger.debug("input_text via fill", index=index, text=text[:40])
                return ActionResult.ok(content=f"Typed '{text}' into element {index}")
            except Exception as fill_exc:
                logger.debug("locator.fill failed, trying fallback", error=str(fill_exc))

        # ── Strategy 2: coordinate click + triple-click + keyboard.type() ───
        bb = target.bounding_box
        if bb:
            x = bb.x + bb.width / 2
            y = bb.y + bb.height / 2
            await page.mouse.click(x, y)
            await asyncio.sleep(0.2)
            # Triple-click selects all text in the field (works even if Ctrl+A doesn't)
            await page.mouse.click(x, y, click_count=3)
            await asyncio.sleep(0.1)
        else:
            # No bounding box — try JS focus fallback
            if target.xpath:
                await page.evaluate(
                    "(xp) => { const el = document.evaluate(xp, document, null, "
                    "XPathResult.FIRST_ORDERED_NODE_TYPE, null).singleNodeValue; "
                    "if(el){ el.focus(); el.select && el.select(); } }",
                    target.xpath,
                )

        # Dispatch native input event so React/Vue picks up the value
        if target.xpath:
            await page.evaluate(
                """([xp, val]) => {
                    const el = document.evaluate(xp, document, null,
                        XPathResult.FIRST_ORDERED_NODE_TYPE, null).singleNodeValue;
                    if (!el) return;
                    const nativeSetter = Object.getOwnPropertyDescriptor(
                        window.HTMLInputElement.prototype, 'value')?.set
                        || Object.getOwnPropertyDescriptor(
                            window.HTMLTextAreaElement.prototype, 'value')?.set;
                    if (nativeSetter) nativeSetter.call(el, val);
                    el.dispatchEvent(new Event('input', { bubbles: true }));
                    el.dispatchEvent(new Event('change', { bubbles: true }));
                }""",
                [target.xpath, text],
            )
            logger.debug("input_text via native-setter dispatch", index=index, text=text[:40])
            return ActionResult.ok(content=f"Typed '{text}' into element {index}")

        await page.keyboard.type(text, delay=30)
        logger.debug("input_text via keyboard.type", index=index, text=text[:40])
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


# ---------------------------------------------------------------------------
# Helpers shared by the new Skyvern-inspired actions
# ---------------------------------------------------------------------------


async def _get_element_locator(index: int, page, elements):
    """Return a Playwright Locator for the element at *index*.

    Tries the element's stored XPath first.  Returns (locator_or_None, elem).
    Caller must handle `locator is None` by falling back to coordinate action.
    Raises ValueError if the index is out of range.
    """
    target = None
    if 0 <= index < len(elements):
        target = elements[index]
    elif 1 <= index <= len(elements):
        target = elements[index - 1]

    if target is None:
        raise ValueError(f"Element index {index} not found. Page has {len(elements)} interactive elements.")

    locator = page.locator(f"xpath={target.xpath}") if target.xpath else None
    return locator, target


# ---------------------------------------------------------------------------
# 14. select_option  (borrowed from Skyvern SelectOptionAction)
# ---------------------------------------------------------------------------


@registry.action(
    description=(
        "Select an option from a <select> dropdown element. "
        "Use `index` from the DOM elements list to identify the <select>. "
        "Provide at least one of: `label` (visible text), `value` (option value attr), "
        "or `option_index` (0-based position in the dropdown). "
        "Skyvern pattern: tries label → value → index → partial label match."
    )
)
async def select_option(
    index: int,
    browser_session: BrowserSession,
    label: str = "",
    value: str = "",
    option_index: int = -1,
) -> ActionResult:
    """Select a dropdown option by label, value, or index (Skyvern SelectOptionAction pattern)."""
    try:
        page = browser_session.get_current_page()
        elements = await _dom_service.get_interactive_elements(page)
        locator, target = await _get_element_locator(index, page, elements)

        if locator is None:
            return ActionResult.fail(error=f"Cannot build locator for element {index} (no XPath).")

        await locator.scroll_into_view_if_needed(timeout=5_000)

        chosen: str | None = None

        # --- Try label first, then value, then index (mirrors Skyvern priority) ---
        if label:
            try:
                await locator.select_option(label=label, timeout=8_000)
                chosen = f"label='{label}'"
            except Exception:
                pass

        if chosen is None and value:
            try:
                await locator.select_option(value=value, timeout=8_000)
                chosen = f"value='{value}'"
            except Exception:
                pass

        if chosen is None and option_index >= 0:
            try:
                await locator.select_option(index=option_index, timeout=8_000)
                chosen = f"index={option_index}"
            except Exception:
                pass

        if chosen is None and label:
            # Partial / case-insensitive fallback: evaluate JS to find best match
            matched: str | None = await page.evaluate(
                """([xpath, lbl]) => {
                    var sel = document.evaluate(xpath, document, null,
                        XPathResult.FIRST_ORDERED_NODE_TYPE, null).singleNodeValue;
                    if (!sel) return null;
                    var opts = Array.from(sel.options);
                    var lo = lbl.toLowerCase();
                    var opt = opts.find(o => o.text.toLowerCase().includes(lo));
                    if (opt) { sel.value = opt.value; sel.dispatchEvent(new Event('change', {bubbles:true})); return opt.text; }
                    return null;
                }""",
                [target.xpath, label],
            )
            if matched:
                chosen = f"partial match='{matched}'"

        if chosen is None:
            return ActionResult.fail(
                error=f"Could not select option on element {index}. "
                      f"label={label!r} value={value!r} option_index={option_index}"
            )

        logger.debug("select_option", index=index, chosen=chosen)
        return ActionResult.ok(content=f"Selected {chosen} on element {index}")
    except Exception as exc:
        logger.warning("select_option failed", index=index, error=str(exc))
        return ActionResult.fail(error=str(exc))


# ---------------------------------------------------------------------------
# 15. hover  (borrowed from Skyvern HoverAction)
# ---------------------------------------------------------------------------


@registry.action(
    description=(
        "Hover the mouse over a numbered element without clicking. "
        "Use to reveal dropdown menus, tooltips, or hidden buttons that only "
        "appear on mouse-over. After hovering, use get_page_state or "
        "find_elements_by_selector to discover newly revealed elements. "
        "`index`: element index from the DOM list. "
        "`hold_seconds`: how long to hold the hover (default 0 = brief hover)."
    )
)
async def hover(
    index: int,
    browser_session: BrowserSession,
    hold_seconds: float = 0.0,
) -> ActionResult:
    """Hover over the element at `index` (Skyvern HoverAction pattern)."""
    try:
        page = browser_session.get_current_page()
        elements = await _dom_service.get_interactive_elements(page)
        locator, target = await _get_element_locator(index, page, elements)

        if locator is not None:
            await locator.scroll_into_view_if_needed(timeout=5_000)
            await locator.hover(timeout=8_000)
        elif target.bounding_box:
            bb = target.bounding_box
            await page.mouse.move(bb.x + bb.width / 2, bb.y + bb.height / 2)
        else:
            return ActionResult.fail(error=f"Cannot hover element {index}: no locator or bounding box.")

        if hold_seconds > 0:
            await asyncio.sleep(hold_seconds)

        elem_label = (target.text or "")[:60]
        logger.debug("hover", index=index, text=elem_label)
        return ActionResult.ok(content=f"Hovered over element {index} ({elem_label})")
    except Exception as exc:
        logger.warning("hover failed", index=index, error=str(exc))
        return ActionResult.fail(error=str(exc))


# ---------------------------------------------------------------------------
# 16. check_checkbox  (borrowed from Skyvern CheckboxAction)
# ---------------------------------------------------------------------------


@registry.action(
    description=(
        "Check or uncheck a checkbox or radio button element. "
        "`index`: element index from the DOM list. "
        "`is_checked`: true to check, false to uncheck. "
        "More reliable than click_element for checkbox state because it uses "
        "Playwright's native check/uncheck which validates the element type."
    )
)
async def check_checkbox(
    index: int,
    is_checked: bool,
    browser_session: BrowserSession,
) -> ActionResult:
    """Check or uncheck a checkbox element (Skyvern CheckboxAction pattern)."""
    try:
        page = browser_session.get_current_page()
        elements = await _dom_service.get_interactive_elements(page)
        locator, target = await _get_element_locator(index, page, elements)

        if locator is None:
            return ActionResult.fail(error=f"Cannot build locator for element {index} (no XPath).")

        await locator.scroll_into_view_if_needed(timeout=5_000)

        if is_checked:
            await locator.check(timeout=8_000)
            state = "checked"
        else:
            await locator.uncheck(timeout=8_000)
            state = "unchecked"

        elem_label = (target.text or "")[:60]
        logger.debug("check_checkbox", index=index, is_checked=is_checked)
        return ActionResult.ok(content=f"Element {index} ({elem_label}) is now {state}")
    except Exception as exc:
        logger.warning("check_checkbox failed", index=index, error=str(exc))
        return ActionResult.fail(error=str(exc))


# ---------------------------------------------------------------------------
# 17. upload_file  (borrowed from Skyvern UploadFileAction)
# ---------------------------------------------------------------------------


@registry.action(
    description=(
        "Upload a local file to a file-input element (<input type='file'>). "
        "`index`: element index of the file input from the DOM list. "
        "`file_path`: absolute path to the file on the local filesystem. "
        "Use when a form has a file upload button or drop zone."
    )
)
async def upload_file(
    index: int,
    file_path: str,
    browser_session: BrowserSession,
) -> ActionResult:
    """Upload a file to the file input at `index` (Skyvern UploadFileAction pattern)."""
    try:
        if not os.path.exists(file_path):
            return ActionResult.fail(error=f"File not found: {file_path}")

        page = browser_session.get_current_page()
        elements = await _dom_service.get_interactive_elements(page)
        locator, target = await _get_element_locator(index, page, elements)

        if locator is None:
            return ActionResult.fail(error=f"Cannot build locator for element {index} (no XPath).")

        await locator.scroll_into_view_if_needed(timeout=5_000)
        await locator.set_input_files(file_path, timeout=10_000)

        fname = os.path.basename(file_path)
        logger.debug("upload_file", index=index, file=fname)
        return ActionResult.ok(content=f"Uploaded '{fname}' to element {index}")
    except Exception as exc:
        logger.warning("upload_file failed", index=index, file_path=file_path, error=str(exc))
        return ActionResult.fail(error=str(exc))


# ---------------------------------------------------------------------------
# 18. reload_page  (borrowed from Skyvern ReloadPageAction)
# ---------------------------------------------------------------------------


@registry.action(
    description=(
        "Reload / refresh the current page. "
        "Use when: the page is stuck, content failed to load, "
        "a session expired, or after completing an upload to see updated state. "
        "Equivalent to pressing F5 in the browser."
    )
)
async def reload_page(browser_session: BrowserSession) -> ActionResult:
    """Reload the current page (Skyvern ReloadPageAction pattern)."""
    try:
        page = browser_session.get_current_page()
        await page.reload(wait_until="domcontentloaded", timeout=30_000)
        summary = await _page_summary(page)
        logger.debug("reload_page", summary=summary)
        return ActionResult.ok(content=f"Page reloaded. {summary}")
    except Exception as exc:
        logger.warning("reload_page failed", error=str(exc))
        return ActionResult.fail(error=str(exc))


# ---------------------------------------------------------------------------
# 19. open_navigation_menu
# ---------------------------------------------------------------------------


@registry.action(
    description=(
        "Open collapsed/header navigation menu in one robust step. "
        "Use when profile/resume/account links are hidden. "
        "Tries common header triggers: hamburger icon, menu button, avatar/profile/account controls."
    )
)
async def open_navigation_menu(browser_session: BrowserSession) -> ActionResult:
    """Open header navigation via robust selector fallbacks.

    This avoids brittle global DOM index guessing for responsive headers.
    """
    try:
        page = browser_session.get_current_page()
        selectors = [
            # Hamburger/menu toggles
            'header button[aria-label*="menu" i]',
            'header [role="button"][aria-label*="menu" i]',
            'header button[title*="menu" i]',
            'header button[class*="menu" i]',
            # Profile/account/avatar triggers
            'header button[aria-label*="profile" i]',
            'header button[aria-label*="account" i]',
            'header button[aria-label*="кабинет" i]',
            'header button[aria-label*="профил" i]',
            'header [role="button"][aria-label*="profile" i]',
            'header [role="button"][aria-label*="account" i]',
            'header img[alt*="profile" i]',
            'header img[alt*="avatar" i]',
            # Generic icon buttons in header (last resort)
            'header button',
        ]

        for sel in selectors:
            locator = page.locator(sel)
            count = await locator.count()
            if count == 0:
                continue

            max_scan = min(count, 8)
            for i in range(max_scan):
                item = locator.nth(i)
                try:
                    if not await item.is_visible():
                        continue
                    await item.scroll_into_view_if_needed(timeout=2_000)
                    await item.click(timeout=4_000)
                    await asyncio.sleep(0.5)
                    summary = await _page_summary(page)
                    return ActionResult.ok(content=f"Opened header navigation via selector '{sel}' (candidate {i}). {summary}")
                except Exception:
                    continue

        return ActionResult.fail(
            error="Could not open header navigation: no clickable menu/profile trigger found in header selectors."
        )
    except Exception as exc:
        logger.warning("open_navigation_menu failed", error=str(exc))
        return ActionResult.fail(error=str(exc))


# ---------------------------------------------------------------------------
# 20. search_and_submit
# ---------------------------------------------------------------------------


@registry.action(
    description=(
        "Fill a search / query input field AND submit the form in one step. "
        "Use this INSTEAD of separate input_text + send_keys/click_element when "
        "performing searches (hh.ru, Google, etc.) to avoid losing focus or index "
        "shifts after autocomplete dropdowns appear. "
        "`index`: element index of the search input from the DOM list. "
        "`query`: the search text to type. "
        "After filling, presses Enter to submit."
    )
)
async def search_and_submit(
    index: int,
    query: str,
    browser_session: BrowserSession,
) -> ActionResult:
    """Fill a search field and press Enter — atomic, survives autocomplete DOM mutations."""
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

        # Step 1 — fill the field (same waterfall as input_text) ────────────
        filled = False
        if target.xpath:
            try:
                locator = page.locator(f"xpath={target.xpath}")
                await locator.scroll_into_view_if_needed(timeout=5_000)
                await locator.click(timeout=5_000)
                await asyncio.sleep(0.15)
                await locator.fill(query, timeout=8_000)
                filled = True
                logger.debug("search_and_submit fill via locator.fill", index=index, query=query[:40])
            except Exception as exc:
                logger.debug("search_and_submit fill fallback", error=str(exc))

        if not filled:
            bb = target.bounding_box
            if bb:
                x = bb.x + bb.width / 2
                y = bb.y + bb.height / 2
                await page.mouse.click(x, y)
                await asyncio.sleep(0.15)
                await page.mouse.click(x, y, click_count=3)
                await asyncio.sleep(0.1)
            if target.xpath:
                await page.evaluate(
                    """([xp, val]) => {
                        const el = document.evaluate(xp, document, null,
                            XPathResult.FIRST_ORDERED_NODE_TYPE, null).singleNodeValue;
                        if (!el) return;
                        const ns = Object.getOwnPropertyDescriptor(
                            window.HTMLInputElement.prototype, 'value')?.set
                            || Object.getOwnPropertyDescriptor(
                                window.HTMLTextAreaElement.prototype, 'value')?.set;
                        if (ns) ns.call(el, val);
                        el.dispatchEvent(new Event('input', { bubbles: true }));
                        el.dispatchEvent(new Event('change', { bubbles: true }));
                    }""",
                    [target.xpath, query],
                )
                logger.debug("search_and_submit fill via native setter", index=index)
            else:
                await page.keyboard.type(query, delay=30)

        # Step 2 — wait briefly for autocomplete, then press Enter ─────────
        await asyncio.sleep(0.4)
        await page.keyboard.press("Enter")
        await asyncio.sleep(0.8)
        summary = await _page_summary(page)
        logger.debug("search_and_submit press Enter", index=index, query=query[:40])
        return ActionResult.ok(
            content=f"Searched for '{query}' and submitted. {summary}"
        )
    except Exception as exc:
        logger.warning("search_and_submit failed", index=index, error=str(exc))
        return ActionResult.fail(error=str(exc))

