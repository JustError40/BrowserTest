"""ScreenshotService — capture, resize, annotate and encode page screenshots."""

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


class ScreenshotService:
    """Captures viewport screenshots, resizes them and optionally saves to disk.

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

    # ── Public API ────────────────────────────────────────────────────────────

    async def capture(self, page: Page) -> bytes:
        """Capture a JPEG viewport screenshot.

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
        self, page: Page, elements: list[dict[str, Any]]
    ) -> bytes:
        """Capture a screenshot with element index labels overlaid.

        Each entry in *elements* must contain at least:
        ``{"index": int, "rect": {"x": float, "y": float, "width": float, "height": float}}``

        Returns:
            JPEG bytes with annotations.
        """
        try:
            from PIL import ImageDraw, ImageFont
        except ImportError as exc:
            raise ImportError("Pillow is required: uv add pillow") from exc

        raw = await page.screenshot(type="png")
        img = self._open_image(raw)

        draw = ImageDraw.Draw(img)
        try:
            font = ImageFont.load_default(size=14)
        except TypeError:
            font = ImageFont.load_default()

        for el in elements:
            rect = el.get("rect") or {}
            x = rect.get("x", 0)
            y = rect.get("y", 0)
            w = rect.get("width", 0)
            h = rect.get("height", 0)
            idx = el.get("index", "?")
            # Draw bounding box
            draw.rectangle([x, y, x + w, y + h], outline="red", width=2)
            # Draw index label
            draw.text((x + 2, y + 2), str(idx), fill="red", font=font)

        jpeg = self._to_jpeg(img)
        self._maybe_save(jpeg, suffix="_annotated")
        logger.debug(
            "screenshot.annotated",
            elements=len(elements),
            session=self.session_id,
        )
        return jpeg

    @staticmethod
    def encode_for_llm(image_bytes: bytes) -> str:
        """Return a base64-encoded string suitable for sending to an LLM."""
        return base64.b64encode(image_bytes).decode("utf-8")

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _resize(self, raw: bytes) -> bytes:
        """Resize *raw* JPEG/PNG bytes so width ≤ ``_MAX_WIDTH``."""
        img = self._open_image(raw)
        w, h = img.size
        if w > _MAX_WIDTH:
            new_h = int(h * _MAX_WIDTH / w)
            img = img.resize((_MAX_WIDTH, new_h))
            logger.debug("screenshot.resized", from_w=w, to_w=_MAX_WIDTH)
        return self._to_jpeg(img)

    @staticmethod
    def _open_image(data: bytes):  # returns PIL.Image
        try:
            from PIL import Image
        except ImportError as exc:
            raise ImportError("Pillow is required: uv add pillow") from exc
        return Image.open(io.BytesIO(data)).convert("RGB")

    @staticmethod
    def _to_jpeg(img) -> bytes:  # img: PIL.Image
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=_JPEG_QUALITY)
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
