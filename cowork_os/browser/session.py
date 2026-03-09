"""BrowserSession — Playwright-based browser session with watchdog services."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Self

import structlog
from pydantic import BaseModel, Field

from cowork_os.browser.profile import BrowserProfile
from cowork_os.browser.watchdogs import WatchdogManager

if TYPE_CHECKING:
    from playwright.async_api import Browser, BrowserContext, Page, Playwright

logger = structlog.get_logger(__name__)


# ─── State model ──────────────────────────────────────────────────────────────


class TabInfo(BaseModel):
    """Lightweight info about an open tab."""

    page_id: int
    url: str
    title: str = ""


class BrowserSessionState(BaseModel):
    """Observable state of a running BrowserSession."""

    current_url: str = ""
    open_tabs: list[TabInfo] = Field(default_factory=list)
    is_running: bool = False


# ─── BrowserSession ────────────────────────────────────────────────────────────


class BrowserSession:
    """Async Playwright browser session with profile, watchdogs, and state tracking.

    Usage::

        async with BrowserSession() as session:
            page = await session.new_page()
            await page.goto("https://example.com")

    Or manually::

        session = BrowserSession(profile=BrowserProfile("work"))
        await session.launch()
        ...
        await session.close()
    """

    def __init__(
        self,
        profile: BrowserProfile | None = None,
        allowed_urls: list[str] | None = None,
    ) -> None:
        self._profile = profile or BrowserProfile()
        self._allowed_urls = allowed_urls

        self._playwright: Playwright | None = None
        self._browser: Browser | None = None
        self._context: BrowserContext | None = None
        self._current_page: Page | None = None
        self._watchdog: WatchdogManager | None = None
        self._lock = asyncio.Lock()
        self._active_ops: int = 0
        self._is_running: bool = False

    # ── Live state property ────────────────────────────────────────────────────

    @property
    def state(self) -> BrowserSessionState:
        """Return a live BrowserSessionState that reads directly from Playwright."""
        current_url = self._current_page.url if self._current_page and self._is_running else ""
        open_tabs: list[TabInfo] = []
        if self._context and self._is_running:
            for i, p in enumerate(self._context.pages):
                open_tabs.append(TabInfo(page_id=i, url=p.url))
        return BrowserSessionState(
            current_url=current_url,
            open_tabs=open_tabs,
            is_running=self._is_running,
        )

    # ── Lifecycle ──────────────────────────────────────────────────────────────

    async def launch(self) -> None:
        """Launch (or connect to) the browser.

        This method is idempotent — calling it while already running is a no-op.
        """
        if self._is_running:
            logger.debug("BrowserSession already running, skipping launch")
            return

        from playwright.async_api import async_playwright

        logger.info("Launching browser", profile=self._profile.name)
        self._playwright = await async_playwright().start()

        launch_args = self._profile.get_launch_args()
        args: list[str] = launch_args.get("args", [])
        headless: bool = launch_args.get("headless", True)
        user_data_dir: str = launch_args.get("user_data_dir", "")

        launch_kwargs: dict = {
            "headless": headless,
            "args": args,
        }
        if launch_args.get("proxy"):
            launch_kwargs["proxy"] = launch_args["proxy"]

        if user_data_dir:
            # Persistent context required for user_data_dir (profiles)
            self._context = await self._playwright.chromium.launch_persistent_context(
                user_data_dir=user_data_dir,
                **launch_kwargs,
            )
            # The persistent context doesn't expose a separate Browser object
            self._browser = None
        else:
            self._browser = await self._playwright.chromium.launch(**launch_kwargs)
            self._context = await self._browser.new_context()

        # Use existing page or open a blank one
        pages = self._context.pages
        if pages:
            self._current_page = pages[0]
        else:
            self._current_page = await self._context.new_page()

        await self._setup_page(self._current_page)
        self._is_running = True
        logger.info("Browser launched", url=self.state.current_url)

    async def _setup_page(self, page: Page) -> None:
        """Attach watchdogs to a page."""
        self._watchdog = WatchdogManager(page, self._allowed_urls)
        self._watchdog.start_all()

    async def new_page(self) -> Page:
        """Open a new tab and make it the active page."""
        if not self._context:
            raise RuntimeError("BrowserSession is not running. Call launch() first.")

        page = await self._context.new_page()
        self._current_page = page
        await self._setup_page(page)
        logger.debug("Opened new page", page_id=len(self._context.pages) - 1)
        return page

    def get_current_page(self) -> Page:
        """Return the currently active page.

        Raises:
            RuntimeError: if the session is not running.
        """
        if not self._current_page or not self._is_running:
            raise RuntimeError("BrowserSession is not running. Call launch() first.")
        return self._current_page

    async def close(self) -> None:
        """Gracefully close the browser session.

        Waits for any active operations to complete (up to 5 seconds),
        then closes the browser and releases all resources.
        """
        if not self._is_running:
            return

        # Wait for active operations (max 5s)
        deadline = asyncio.get_event_loop().time() + 5.0
        while self._active_ops > 0 and asyncio.get_event_loop().time() < deadline:
            await asyncio.sleep(0.1)

        if self._watchdog:
            await self._watchdog.close()
            self._watchdog = None

        try:
            if self._context:
                await self._context.close()
        except Exception as exc:
            logger.debug("Context close error", error=str(exc))

        try:
            if self._browser:
                await self._browser.close()
        except Exception as exc:
            logger.debug("Browser close error", error=str(exc))

        try:
            if self._playwright:
                await self._playwright.stop()
        except Exception as exc:
            logger.debug("Playwright stop error", error=str(exc))

        self._playwright = None
        self._browser = None
        self._context = None
        self._current_page = None
        self._is_running = False
        logger.info("Browser session closed")

    # ── Context manager ────────────────────────────────────────────────────────

    async def __aenter__(self) -> Self:
        await self.launch()
        return self

    async def __aexit__(self, exc_type: type | None, exc_val: Exception | None, exc_tb: object) -> None:
        await self.close()
