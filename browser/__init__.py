"""Browser management package for coworkOS."""

from browser.dom import DomService
from browser.dom_views import BoundingBox, DOMElement
from browser.profile import BrowserProfile, ProfileManager, get_profile_manager
from browser.session import BrowserSession, BrowserSessionState, TabInfo
from browser.watchdogs import DialogWatchdog, SecurityWatchdog, WatchdogManager

__all__ = [
    "BrowserProfile",
    "ProfileManager",
    "get_profile_manager",
    "BrowserSession",
    "BrowserSessionState",
    "TabInfo",
    "DialogWatchdog",
    "SecurityWatchdog",
    "WatchdogManager",
    "DomService",
    "DOMElement",
    "BoundingBox",
]
