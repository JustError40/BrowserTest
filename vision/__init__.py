"""Vision package for coworkOS."""

from vision.annotator import capture_som_screenshot, element_labels
from vision.screenshot import ScreenshotService

__all__ = ["ScreenshotService", "capture_som_screenshot", "element_labels"]
