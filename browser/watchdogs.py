"""Browser watchdog services for coworkOS."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

import structlog

if TYPE_CHECKING:
    from playwright.async_api import Page

logger = structlog.get_logger(__name__)


class DialogWatchdog:
    """Automatically dismisses JavaScript dialogs (alert, confirm, prompt).

    Registers a handler on the Playwright Page that accepts all dialogs
    to prevent the browser from freezing when a page triggers an alert.
    """

    def __init__(self, page: Page) -> None:
        self._page = page
        self._dismissed_count = 0

    def start(self) -> None:
        """Register dialog handler on the page."""
        self._page.on("dialog", self._handle_dialog)
        logger.debug("DialogWatchdog started")

    def stop(self) -> None:
        """Remove dialog handler from the page."""
        try:
            self._page.remove_listener("dialog", self._handle_dialog)
        except Exception:
            pass

    async def _handle_dialog(self, dialog: object) -> None:  # type: ignore[override]
        """Accept the dialog and log it."""
        try:
            message = getattr(dialog, "message", "")
            dialog_type = getattr(dialog, "type", "unknown")
            await dialog.accept()  # type: ignore[union-attr]
            self._dismissed_count += 1
            logger.debug("DialogWatchdog dismissed dialog", type=dialog_type, message=message[:100])
        except Exception as exc:
            logger.warning("DialogWatchdog failed to dismiss dialog", error=str(exc))

    @property
    def dismissed_count(self) -> int:
        return self._dismissed_count


class SecurityWatchdog:
    """URL allowlist watchdog — blocks navigation to disallowed URLs.

    If ``allowed_urls`` is empty, all URLs are allowed.
    """

    def __init__(self, page: Page, allowed_urls: list[str] | None = None) -> None:
        self._page = page
        self._allowed_urls: list[str] = allowed_urls or []
        self._blocked_count = 0

    def start(self) -> None:
        """Register navigation event handler."""
        if not self._allowed_urls:
            return  # No restrictions
        self._page.on("framenavigated", self._handle_navigation)
        logger.debug("SecurityWatchdog started", allowed_urls=self._allowed_urls)

    def stop(self) -> None:
        """Remove navigation handler."""
        if not self._allowed_urls:
            return
        try:
            self._page.remove_listener("framenavigated", self._handle_navigation)
        except Exception:
            pass

    async def _handle_navigation(self, frame: object) -> None:  # type: ignore[override]
        """Check navigated URL against allowlist."""
        if not self._allowed_urls:
            return
        try:
            # Only check main frame
            if getattr(frame, "parent_frame", None) is not None:
                url = getattr(frame, "url", "")
                if url and not any(url.startswith(allowed) for allowed in self._allowed_urls):
                    logger.warning("SecurityWatchdog blocked navigation", url=url)
                    self._blocked_count += 1
                    try:
                        await self._page.go_back()
                    except Exception:
                        pass
        except Exception as exc:
            logger.debug("SecurityWatchdog navigation check error", error=str(exc))

    def is_allowed(self, url: str) -> bool:
        """Return True if the URL is permitted."""
        if not self._allowed_urls:
            return True
        return any(url.startswith(allowed) for allowed in self._allowed_urls)

    @property
    def blocked_count(self) -> int:
        return self._blocked_count


class WatchdogManager:
    """Manages a set of watchdogs for a browser page."""

    def __init__(self, page: Page, allowed_urls: list[str] | None = None) -> None:
        self._page = page
        self.dialog = DialogWatchdog(page)
        self.security = SecurityWatchdog(page, allowed_urls)
        self._running = False

    def start_all(self) -> None:
        """Start all registered watchdogs."""
        if self._running:
            return
        self.dialog.start()
        self.security.start()
        self._running = True

    def stop_all(self) -> None:
        """Stop all registered watchdogs."""
        self.dialog.stop()
        self.security.stop()
        self._running = False

    async def close(self) -> None:
        """Stop watchdogs (async alias)."""
        self.stop_all()
        await asyncio.sleep(0)
