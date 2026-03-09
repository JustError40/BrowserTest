"""Configuration package for coworkOS."""

from cowork_os.config.logging import get_logger, setup_logging
from cowork_os.config.settings import Settings, get_settings

__all__ = ["Settings", "get_settings", "setup_logging", "get_logger"]
