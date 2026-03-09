"""AgentContext — shared context passed between all agents in a session."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from agent.event import EventManager
from agent.views import AgentSettings

if TYPE_CHECKING:
    from agent.llm.base import BaseLLM
    from browser.session import BrowserSession
    from config.settings import Settings
    from memory.message_manager import MessageManager


@dataclass
class AgentContext:
    """Container for all shared state passed between agents.

    Attributes:
        settings:       Agent-level settings (max_steps, vision_enabled, …).
        event_manager:  Pub/sub bus shared by all agents.
        llm:            Language-model instance (optional at context creation).
        browser_session: Playwright browser session (optional, set before run).
        message_manager: Conversation-history manager (optional).
        task_id:        Unique ID for the current top-level task.
        metadata:       Arbitrary extra data agents may attach.
    """

    settings: AgentSettings
    event_manager: EventManager
    llm: BaseLLM | None = None
    browser_session: BrowserSession | None = None
    message_manager: MessageManager | None = None
    task_id: str = ""
    metadata: dict = field(default_factory=dict)

    # ── Factory ──────────────────────────────────────────────────────────────

    @classmethod
    def create(
        cls,
        settings: AgentSettings | None = None,
        app_settings: Settings | None = None,
        **kwargs,
    ) -> AgentContext:
        """Create a fresh AgentContext.

        Args:
            settings:     Agent settings; uses defaults if not provided.
            app_settings: Application-level settings (unused directly, kept for
                          future use).
            **kwargs:     Extra fields forwarded to the dataclass constructor.
        """
        return cls(
            settings=settings or AgentSettings(),
            event_manager=EventManager(),
            **kwargs,
        )

    # ── Helpers ───────────────────────────────────────────────────────────────

    def with_llm(self, llm: BaseLLM) -> AgentContext:
        """Return *self* with llm set (mutates and returns for chaining)."""
        self.llm = llm
        return self

    def with_browser_session(self, session: BrowserSession) -> AgentContext:
        self.browser_session = session
        return self

    def with_message_manager(self, mm: MessageManager) -> AgentContext:
        self.message_manager = mm
        return self
