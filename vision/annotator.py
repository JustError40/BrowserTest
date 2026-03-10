"""Set-of-Marks (SoM) visual annotator for browser screenshots.

Approach (borrowed from Skyvern's domUtils.js bounding-box pipeline):
  1. Inject a fixed-position transparent overlay layer into the live DOM.
  2. For each interactive element, add a coloured bounding box + numeric badge.
  3. Take a Playwright screenshot — the overlays are captured in the image.
  4. Remove all overlays (cleanup), leaving the page intact.

By injecting into the real DOM rather than post-processing pixels with PIL we
get pixel-perfect element alignment at any device-pixel-ratio, and the labels
stay valid even if the page has CSS transforms.  After the screenshot the page
is fully restored.

Colour scheme (mirrors semantic role):
  - link      → blue  (#2563EB)
  - button    → green (#16A34A)
  - textbox / input / textarea / select → orange (#D97706)
  - other     → purple (#7C3AED)

Usage::

    from vision.annotator import capture_som_screenshot
    from browser.dom import DomService

    elements = await DomService().get_interactive_elements(page)
    screenshot_bytes = await capture_som_screenshot(page, elements)
    # → JPEG bytes with numbered element badges visible
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING

import structlog

if TYPE_CHECKING:
    from playwright.async_api import Page

    from browser.dom_views import DOMElement

logger = structlog.get_logger(__name__)

_CONTAINER_ID = "coworkos-som-layer"

# ─── Colour per semantic role ─────────────────────────────────────────────────

_ROLE_COLOURS: dict[str, tuple[str, str]] = {
    # role keyword → (border_hex, badge_hex)
    "link":       ("#2563EB", "#2563EB"),   # blue
    "a":          ("#2563EB", "#2563EB"),
    "button":     ("#16A34A", "#16A34A"),   # green
    "submit":     ("#16A34A", "#16A34A"),
    "textbox":    ("#D97706", "#D97706"),   # amber
    "input":      ("#D97706", "#D97706"),
    "textarea":   ("#D97706", "#D97706"),
    "select":     ("#D97706", "#D97706"),
    "combobox":   ("#D97706", "#D97706"),
    "searchbox":  ("#D97706", "#D97706"),
    "checkbox":   ("#EA580C", "#EA580C"),   # orange
    "radio":      ("#EA580C", "#EA580C"),
}
_DEFAULT_COLOUR = ("#7C3AED", "#7C3AED")  # purple for everything else


def _colour_for(role: str) -> tuple[str, str]:
    role_lc = (role or "").lower()
    return _ROLE_COLOURS.get(role_lc, _DEFAULT_COLOUR)


# ─── JavaScript that builds the overlay ──────────────────────────────────────

# The JS receives a list of {index, x, y, width, height, role} objects and
# paints them onto a fixed-position container using only vanilla DOM APIs.
_INJECT_SCRIPT = """
(elements) => {
  // Remove any stale container from a previous run
  const old = document.getElementById("{container_id}");
  if (old) old.remove();

  const container = document.createElement("div");
  container.id = "{container_id}";
  container.style.cssText = [
    "position:fixed",
    "top:0",
    "left:0",
    "width:100%",
    "height:100%",
    "pointer-events:none",
    "z-index:2147483647",
    "overflow:hidden",
  ].join(";");

  for (const el of elements) {
    const borderColor = el.borderColor || "#7C3AED";
    const badgeColor  = el.badgeColor  || "#7C3AED";

    // ── Bounding box ──────────────────────────────────────────────────
    const box = document.createElement("div");
    box.style.cssText = [
      "position:absolute",
      "pointer-events:none",
      "box-sizing:border-box",
      `left:${el.x}px`,
      `top:${el.y}px`,
      `width:${el.width}px`,
      `height:${el.height}px`,
      `border:2px solid ${borderColor}`,
      "border-radius:3px",
    ].join(";");
    container.appendChild(box);

    // ── Numeric badge ─────────────────────────────────────────────────
    const badge = document.createElement("div");
    const BADGE_W = Math.max(18, String(el.index).length * 8 + 6);
    const BADGE_H = 18;
    // Position badge at top-left corner, slightly outside the box
    const bx = Math.max(0, el.x - 1);
    const by = Math.max(0, el.y - BADGE_H);
    badge.style.cssText = [
      "position:absolute",
      "pointer-events:none",
      "box-sizing:border-box",
      `left:${bx}px`,
      `top:${by}px`,
      `width:${BADGE_W}px`,
      `height:${BADGE_H}px`,
      `background:${badgeColor}`,
      "color:#fff",
      "font-size:11px",
      "font-family:monospace",
      "font-weight:bold",
      "text-align:center",
      "line-height:18px",
      "border-radius:3px 3px 0 0",
      "user-select:none",
    ].join(";");
    badge.textContent = String(el.index);
    container.appendChild(badge);
  }

  document.documentElement.appendChild(container);
  return true;
}
""".replace(
    "{container_id}", _CONTAINER_ID
)

_REMOVE_SCRIPT = f"""
() => {{
  const el = document.getElementById("{_CONTAINER_ID}");
  if (el) el.remove();
  return true;
}}
"""


# ─── Low-level helpers ────────────────────────────────────────────────────────


async def _inject_element_labels(page: Page, elements: list[DOMElement]) -> None:
    """Paint numbered bounding-box overlays on *page* for each element."""
    payload = []
    for el in elements:
        bb = el.bounding_box
        if not bb.is_visible:
            continue
        border_c, badge_c = _colour_for(el.role)
        payload.append(
            {
                "index": el.index,
                "x": bb.x,
                "y": bb.y,
                "width": bb.width,
                "height": bb.height,
                "borderColor": border_c,
                "badgeColor": badge_c,
            }
        )
    if not payload:
        return
    try:
        await page.evaluate(_INJECT_SCRIPT, payload)
    except Exception as exc:
        logger.debug("som.inject_failed", error=str(exc))


async def _remove_element_labels(page: Page) -> None:
    """Remove the SoM overlay layer from *page*."""
    try:
        await page.evaluate(_REMOVE_SCRIPT)
    except Exception as exc:
        logger.debug("som.remove_failed", error=str(exc))


# ─── Public API ───────────────────────────────────────────────────────────────


@asynccontextmanager
async def element_labels(
    page: Page,
    elements: list[DOMElement],
) -> AsyncIterator[None]:
    """Context manager: inject SoM labels, yield, then clean up.

    Usage::

        async with element_labels(page, elements):
            screenshot = await page.screenshot()
    """
    await _inject_element_labels(page, elements)
    try:
        yield
    finally:
        # Brief wait to ensure compositing is complete before cleanup
        await asyncio.sleep(0.05)
        await _remove_element_labels(page)


async def capture_som_screenshot(
    page: Page,
    elements: list[DOMElement],
    jpeg_quality: int = 80,
    max_width: int = 1280,
) -> bytes:
    """Capture a screenshot with SoM (Set-of-Marks) element annotations.

    Injects numbered bounding boxes into the live DOM, takes a JPEG screenshot,
    removes the overlays, and resizes the image to *max_width* pixels.

    Args:
        page:         Playwright Page to screenshot.
        elements:     Interactive elements returned by DomService.
        jpeg_quality: JPEG compression quality (1–100).
        max_width:    Maximum output image width in pixels.

    Returns:
        JPEG bytes with coloured element-index badges visible.
    """
    from io import BytesIO

    from PIL import Image

    async with element_labels(page, elements):
        # Small settle delay so the new DOM nodes are rendered
        await asyncio.sleep(0.08)
        raw = await page.screenshot(type="png")

    # Post-process: convert to JPEG and resize
    img = Image.open(BytesIO(raw)).convert("RGB")
    w, h = img.size
    if w > max_width:
        img = img.resize((max_width, int(h * max_width / w)), Image.Resampling.LANCZOS)
    buf = BytesIO()
    img.save(buf, format="JPEG", quality=jpeg_quality, optimize=True)
    result = buf.getvalue()
    logger.debug(
        "som.screenshot",
        elements=len([e for e in elements if e.bounding_box.is_visible]),
        bytes=len(result),
        width=img.width,
    )
    return result
