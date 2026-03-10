"""ScreenshotService — capture, resize, annotate and encode page screenshots.

Vision pipeline
---------------
The primary method for agent use is :meth:`capture_som` which implements the
Set-of-Marks (SoM) approach inspired by Skyvern's domUtils.js pipeline:

1. Inject coloured bounding-box + numeric-badge overlays into the live DOM.
2. Take a Playwright screenshot with those overlays rendered.
3. Remove the overlays (page is restored).
4. Resize to ≤ 1280 px wide and re-encode as JPEG.

This ensures element badges **exactly** align with DOM positions regardless
of device-pixel-ratio or CSS transforms.

:meth:`capture_annotated` is the PIL-only fallback (no DOM injection).
"""

from __future__ import annotations

import base64
import io
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

import structlog

logger = structlog.get_logger(__name__)

_MAX_WIDTH = 1280
_JPEG_QUALITY = 80

if TYPE_CHECKING:
    from playwright.async_api import Page

    from browser.dom_views import DOMElement


class ScreenshotService:
    """Captures viewport screenshots, annotates and encodes them.

    Parameters
    ----------
    session_id:
        Unique identifier for the current browser session; used as the
        sub-directory name when saving screenshots to disk.
    screenshots_dir:
        Base directory for saved screenshots.  Defaults to ``./screenshots``.
    privacy_mode:
        When ``"strict"`` no files are written to disk.
    """

    def __init__(
        self,
        session_id: str = "default",
        screenshots_dir: str | Path = "./screenshots",
        privacy_mode: str = "normal",
    ) -> None:
        self.session_id = session_id
        self.screenshots_dir = Path(screenshots_dir)
        self.privacy_mode = privacy_mode

    # ── Primary vision API ────────────────────────────────────────────────────

    async def capture_som(
        self,
        page: Page,
        elements: list[DOMElement],
        jpeg_quality: int = _JPEG_QUALITY,
    ) -> bytes:
        """**Main method for vision mode** — SoM (Set-of-Marks) screenshot.

        Injects coloured bounding-box + numeric-badge overlays into the live
        DOM (Skyvern-style), takes a screenshot, removes the overlays, and
        resizes the result to ≤ *_MAX_WIDTH* px.

        The numeric badges match the ``[N]`` indices in the DOM text returned
        by :meth:`~browser.dom.DomService.format_for_llm`, so the LLM can
        cross-reference visual position and text description of elements.

        Args:
            page:         Active Playwright page.
            elements:     List from DomService.get_interactive_elements().
            jpeg_quality: JPEG compression quality (1–100).

        Returns:
            JPEG bytes with element number badges visible.
        """
        from vision.annotator import capture_som_screenshot

        try:
            jpeg = await capture_som_screenshot(
                page,
                elements,
                jpeg_quality=jpeg_quality,
                max_width=_MAX_WIDTH,
            )
        except Exception as exc:
            logger.warning("som.capture_failed, falling back to plain capture", error=str(exc))
            jpeg = await self.capture(page)

        self._maybe_save(jpeg, suffix="_som")
        return jpeg

    async def capture(self, page: Page) -> bytes:
        """Capture a plain JPEG viewport screenshot (no element overlays).

        The image is resized to at most :data:`_MAX_WIDTH` pixels wide, then
        saved to disk unless *privacy_mode* is ``"strict"``.

        Returns:
            JPEG bytes.
        """
        raw: bytes = await page.screenshot(type="jpeg", quality=_JPEG_QUALITY)
        jpeg = self._resize(raw)
        self._maybe_save(jpeg)
        logger.debug("screenshot.captured", bytes=len(jpeg), session=self.session_id)
        return jpeg

    async def capture_annotated(
        self,
        page: Page,
        elements: list[Any],
    ) -> bytes:
        """PIL-only fallback: overlay index boxes without DOM injection.

        Each entry in *elements* should be a :class:`~browser.dom_views.DOMElement`
        (or a plain dict with ``index``, ``role``/``tag``, and ``bounding_box`` keys).

        Prefer :meth:`capture_som` for normal agent use.

        Returns:
            JPEG bytes with coloured box annotations.
        """
        from PIL import Image, ImageDraw, ImageFont

        raw = await page.screenshot(type="png")
        img = Image.open(io.BytesIO(raw)).convert("RGB")
        vp_w = (page.viewport_size or {}).get("width", img.size[0])
        dpr = img.size[0] / vp_w if vp_w else 1.0

        draw = ImageDraw.Draw(img, "RGBA")
        try:
            font_lbl = ImageFont.load_default(size=int(13 * dpr))
        except TypeError:
            font_lbl = ImageFont.load_default()

        _COLOURS: dict[str, str] = {
            "link": "#2563EB", "a": "#2563EB",
            "button": "#16A34A",
            "textbox": "#D97706", "input": "#D97706",
            "select": "#D97706", "textarea": "#D97706",
            "default": "#7C3AED",
        }

        for el in elements:
            if hasattr(el, "bounding_box"):
                bb = el.bounding_box
                x, y, w, h = bb.x * dpr, bb.y * dpr, bb.width * dpr, bb.height * dpr
                idx = el.index
                role = (el.role or el.tag or "").lower()
            else:
                bb = el.get("bounding_box") or el.get("rect") or {}
                x, y = bb.get("x", 0) * dpr, bb.get("y", 0) * dpr
                w, h = bb.get("width", 0) * dpr, bb.get("height", 0) * dpr
                idx = el.get("index", "?")
                role = (el.get("role") or el.get("tag") or "").lower()

            if w <= 0 or h <= 0:
                continue

            colour = _COLOURS.get(role, _COLOURS["default"])
            border = max(2, int(2 * dpr))
            draw.rectangle([x, y, x + w, y + h], outline=colour, width=border)

            label = str(idx)
            badge_w = max(18 * dpr, len(label) * 8 * dpr + 6)
            badge_h = 18 * dpr
            bx, by = max(0.0, x - 1), max(0.0, y - badge_h)
            draw.rectangle([bx, by, bx + badge_w, by + badge_h], fill=colour)
            draw.text((bx + 3, by + 1), label, fill="#FFFFFF", font=font_lbl)

        jpeg = self._to_jpeg(self._resize_img(img))
        self._maybe_save(jpeg, suffix="_annotated")
        logger.debug("screenshot.annotated", elements=len(elements), session=self.session_id)
        return jpeg

    # ── Encoding ──────────────────────────────────────────────────────────────

    @staticmethod
    def encode_for_llm(image_bytes: bytes) -> str:
        """Return a base64-encoded string suitable for sending to an LLM."""
        return base64.b64encode(image_bytes).decode("utf-8")

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _resize(self, raw: bytes) -> bytes:
        """Resize *raw* JPEG/PNG bytes so width ≤ :data:`_MAX_WIDTH`."""
        from PIL import Image

        img = Image.open(io.BytesIO(raw)).convert("RGB")
        return self._to_jpeg(self._resize_img(img))

    @staticmethod
    def _resize_img(img: Any) -> Any:  # PIL.Image → PIL.Image
        from PIL import Image

        w, h = img.size
        if w > _MAX_WIDTH:
            img = img.resize((_MAX_WIDTH, int(h * _MAX_WIDTH / w)), Image.Resampling.LANCZOS)
        return img

    @staticmethod
    def _open_image(data: bytes) -> Any:  # returns PIL.Image
        from PIL import Image

        return Image.open(io.BytesIO(data)).convert("RGB")

    @staticmethod
    def _to_jpeg(img: Any) -> bytes:  # img: PIL.Image
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=_JPEG_QUALITY, optimize=True)
        return buf.getvalue()

    def _maybe_save(self, jpeg: bytes, suffix: str = "") -> Path | None:
        """Save *jpeg* to disk; no-op in strict privacy mode."""
        if self.privacy_mode == "strict":
            return None
        ts = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        out_dir = self.screenshots_dir / self.session_id
        out_dir.mkdir(parents=True, exist_ok=True)
        path = out_dir / f"{ts}{suffix}.jpg"
        path.write_bytes(jpeg)
        return path
