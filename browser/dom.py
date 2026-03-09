"""DOM service: extract and number interactive elements via Playwright."""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING, Any

from playwright.async_api import Page

from browser.dom_views import BoundingBox, DOMElement

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)

# Tags and roles treated as interactive
_INTERACTIVE_TAGS = frozenset(
    {"a", "button", "input", "select", "textarea", "label", "details", "summary"}
)
_INTERACTIVE_ROLES = frozenset(
    {
        "button",
        "link",
        "textbox",
        "combobox",
        "listbox",
        "checkbox",
        "radio",
        "menuitem",
        "tab",
        "option",
        "searchbox",
        "spinbutton",
        "slider",
        "switch",
    }
)

# Max characters for LLM-formatted output (~4000 tokens ≈ 16 000 chars)
_MAX_FORMAT_CHARS = 16_000


class DomService:
    """Extract, filter and format interactive DOM elements from a Playwright page."""

    def __init__(self, viewport_expansion: int = 0) -> None:
        """
        Args:
            viewport_expansion: Extra pixels beyond the visible viewport to include.
                                Set to -1 to include all elements regardless of position.
        """
        self.viewport_expansion = viewport_expansion

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def get_interactive_elements(self, page: Page) -> list[DOMElement]:
        """Return numbered interactive elements visible within the current viewport.

        Uses Playwright's evaluate to query DOM attributes for reliability — avoids
        the complexity of cdp-based Accessibility tree snapshots.
        """
        viewport = page.viewport_size or {"width": 1280, "height": 720}
        height: float = viewport["height"]
        if self.viewport_expansion < 0:
            lower_bound = float("-inf")
            upper_bound = float("inf")
        else:
            lower_bound = -self.viewport_expansion
            upper_bound = height + self.viewport_expansion

        raw_elements: list[dict[str, Any]] = await page.evaluate(
            """(bounds) => {
  const { lower, upper } = bounds;
  const INTERACTIVE = new Set(['a','button','input','select','textarea','label','details','summary']);
  const ROLES = new Set(['button','link','textbox','combobox','listbox','checkbox',
    'radio','menuitem','tab','option','searchbox','spinbutton','slider','switch']);

  function getXPath(el) {
    const parts = [];
    let cur = el;
    while (cur && cur.nodeType === Node.ELEMENT_NODE) {
      let idx = 1;
      let sib = cur.previousElementSibling;
      while (sib) { if (sib.tagName === cur.tagName) idx++; sib = sib.previousElementSibling; }
      parts.unshift(cur.tagName.toLowerCase() + (idx > 1 ? '['+idx+']' : ''));
      cur = cur.parentElement;
    }
    return '/' + parts.join('/');
  }

  function getText(el) {
    const aria = el.getAttribute('aria-label') || el.getAttribute('title') || '';
    if (aria) return aria.trim().slice(0, 200);
    return (el.innerText || el.textContent || '').trim().slice(0, 200);
  }

  const candidates = document.querySelectorAll(Array.from(INTERACTIVE).join(','));
  const results = [];
  for (const el of candidates) {
    const style = window.getComputedStyle(el);
    if (style.display === 'none' || style.visibility === 'hidden' || style.opacity === '0') continue;
    const rect = el.getBoundingClientRect();
    if (rect.width === 0 || rect.height === 0) continue;
    if (rect.bottom < lower || rect.top > upper) continue;
    const role = el.getAttribute('role') || el.tagName.toLowerCase();
    results.push({
      tag: el.tagName.toLowerCase(),
      text: getText(el),
      role: role,
      href: el.href || '',
      value: el.value || '',
      placeholder: el.placeholder || '',
      xpath: getXPath(el),
      x: rect.x,
      y: rect.y,
      width: rect.width,
      height: rect.height,
    });
  }
  // Also pick up elements with interactive roles that aren't standard tags
  const roleEls = document.querySelectorAll('[role]');
  const seen = new Set(Array.from(candidates));
  for (const el of roleEls) {
    if (seen.has(el)) continue;
    const role = (el.getAttribute('role') || '').toLowerCase();
    if (!ROLES.has(role)) continue;
    const style = window.getComputedStyle(el);
    if (style.display === 'none' || style.visibility === 'hidden' || style.opacity === '0') continue;
    const rect = el.getBoundingClientRect();
    if (rect.width === 0 || rect.height === 0) continue;
    if (rect.bottom < lower || rect.top > upper) continue;
    results.push({
      tag: el.tagName.toLowerCase(),
      text: (el.getAttribute('aria-label') || el.textContent || '').trim().slice(0, 200),
      role: role,
      href: el.href || '',
      value: el.value || '',
      placeholder: el.placeholder || '',
      xpath: getXPath(el),
      x: rect.x,
      y: rect.y,
      width: rect.width,
      height: rect.height,
    });
  }
  return results;
}""",
            {"lower": lower_bound, "upper": upper_bound},
        )

        elements: list[DOMElement] = []
        for idx, raw in enumerate(raw_elements, start=1):
            tag = raw.get("tag", "")
            role = raw.get("role", "") or tag
            # Normalise role names
            if tag == "a" and role == "a":
                role = "link"
            elif tag == "button" and role == "button":
                role = "button"
            elif tag in ("input", "textarea") and role in ("input", "textarea"):
                role = "textbox"
            elements.append(
                DOMElement(
                    index=idx,
                    tag=tag,
                    text=raw.get("text", ""),
                    role=role,
                    href=raw.get("href", ""),
                    value=raw.get("value", ""),
                    placeholder=raw.get("placeholder", ""),
                    xpath=raw.get("xpath", ""),
                    bounding_box=BoundingBox(
                        x=raw.get("x", 0.0),
                        y=raw.get("y", 0.0),
                        width=raw.get("width", 0.0),
                        height=raw.get("height", 0.0),
                    ),
                )
            )

        return elements

    async def highlight(self, page: Page, index: int, elements: list[DOMElement]) -> bool:
        """Highlight the element with the given index using an orange border.

        Returns True if the element was found and highlighted.
        """
        matching = [e for e in elements if e.index == index]
        if not matching:
            return False
        el = matching[0]
        highlighted = await page.evaluate(
            """(xpath) => {
  const result = document.evaluate(xpath, document, null,
    XPathResult.FIRST_ORDERED_NODE_TYPE, null);
  const el = result.singleNodeValue;
  if (!el) return false;
  const prev = el.style.outline;
  el.style.outline = '3px solid orange';
  el.style.outlineOffset = '2px';
  el.__prev_outline = prev;
  return true;
}""",
            el.xpath,
        )
        return bool(highlighted)

    @staticmethod
    def format_for_llm(elements: list[DOMElement]) -> str:
        """Format elements as a compact string for LLM consumption.

        Example output line: '[1] link: More information... (https://www.iana.org/...)'
        Truncates at _MAX_FORMAT_CHARS.
        """
        lines: list[str] = []
        total = 0
        for el in elements:
            line = str(el)
            if total + len(line) + 1 > _MAX_FORMAT_CHARS:
                lines.append("... (truncated)")
                break
            lines.append(line)
            total += len(line) + 1
        return "\n".join(lines)

    @staticmethod
    def to_json(elements: list[DOMElement]) -> str:
        """Serialise elements to a JSON string."""
        return json.dumps([e.model_dump() for e in elements], ensure_ascii=False, indent=2)
