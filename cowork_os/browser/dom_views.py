"""DOM data structures for CoworkOS."""

from __future__ import annotations

from pydantic import BaseModel, Field


class BoundingBox(BaseModel):
    """Element bounding box coordinates."""

    x: float = 0.0
    y: float = 0.0
    width: float = 0.0
    height: float = 0.0

    @property
    def is_visible(self) -> bool:
        """Return True if the element has non-zero dimensions."""
        return self.width > 0 and self.height > 0


class DOMElement(BaseModel):
    """Represents a single interactive DOM element."""

    index: int
    tag: str  # e.g. 'a', 'button', 'input', 'select', 'textarea'
    text: str = ""  # visible text or aria-label
    role: str = ""  # ARIA role: 'link', 'button', 'textbox', etc.
    href: str = ""  # for links
    value: str = ""  # current input value
    placeholder: str = ""
    xpath: str = ""
    bounding_box: BoundingBox = Field(default_factory=BoundingBox)

    def __str__(self) -> str:
        parts = [f"[{self.index}] {self.role or self.tag}"]
        if self.text:
            label = self.text[:80]
            parts.append(f": {label}")
        if self.href:
            parts.append(f" ({self.href[:100]})")
        if self.placeholder:
            parts.append(f" [{self.placeholder}]")
        return "".join(parts)
