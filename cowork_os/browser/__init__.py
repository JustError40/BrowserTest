"""Browser management package for coworkOS."""

from cowork_os.browser.dom import DomService
from cowork_os.browser.dom_views import BoundingBox, DOMElement
from cowork_os.browser.profile import BrowserProfile, ProfileManager, get_profile_manager
from cowork_os.browser.session import BrowserSession, BrowserSessionState, TabInfo
from cowork_os.browser.watchdogs import DialogWatchdog, SecurityWatchdog, WatchdogManager

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
