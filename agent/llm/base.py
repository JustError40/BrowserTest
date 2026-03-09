"""Abstract base class and message types for the LLM layer."""

from __future__ import annotations

import asyncio
import logging
from abc import ABC, abstractmethod
from typing import Any

logger = logging.getLogger(__name__)

# Message dict type: {"role": "user"|"assistant"|"system", "content": str}
Message = dict[str, Any]

# Exponential backoff delays in seconds
_RETRY_DELAYS = (1.0, 2.0, 4.0)
_RATE_LIMIT_EXCEPTIONS = ("rate_limit", "RateLimitError", "429", "too many requests")


def _is_rate_limit(exc: Exception) -> bool:
    """Detect rate-limit errors from any SDK."""
    msg = str(exc).lower()
    cls = type(exc).__name__
    return any(
        kw in msg or kw.lower() in cls.lower()
        for kw in _RATE_LIMIT_EXCEPTIONS
    )


class BaseLLM(ABC):
    """Abstract base class for all LLM providers."""

    def __init__(self, model: str, api_key: str = "", timeout: float = 120.0) -> None:
        self.model = model
        self.api_key = api_key
        self.timeout = timeout

    @property
    @abstractmethod
    def provider(self) -> str:
        """Provider name: 'openai', 'anthropic', 'ollama'."""

    async def generate(
        self,
        messages: list[Message],
        images: list[bytes] | None = None,
        **kwargs: Any,
    ) -> str:
        """Generate a text response with optional images (vision).

        Retries up to 3 times on rate-limit errors with exponential backoff.
        """
        last_exc: Exception | None = None
        for attempt, delay in enumerate((*_RETRY_DELAYS, None), start=1):
            try:
                return await asyncio.wait_for(
                    self._generate(messages, images=images, **kwargs),
                    timeout=self.timeout,
                )
            except Exception as exc:
                if _is_rate_limit(exc) and delay is not None:
                    logger.warning(
                        "Rate limit hit (attempt %d/3), retrying in %.0fs…",
                        attempt,
                        delay,
                    )
                    await asyncio.sleep(delay)
                    last_exc = exc
                else:
                    if delay is None and last_exc is not None:
                        raise last_exc from exc
                    raise
        assert last_exc is not None
        raise last_exc

    async def generate_structured(
        self,
        schema: type | dict[str, Any],
        messages: list[Message],
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Generate a structured (JSON) response matching *schema*.

        Retries up to 3 times on rate-limit errors with exponential backoff.
        """
        last_exc: Exception | None = None
        for attempt, delay in enumerate((*_RETRY_DELAYS, None), start=1):
            try:
                return await asyncio.wait_for(
                    self._generate_structured(schema, messages, **kwargs),
                    timeout=self.timeout,
                )
            except Exception as exc:
                if _is_rate_limit(exc) and delay is not None:
                    logger.warning(
                        "Rate limit hit on structured (attempt %d/3), retrying in %.0fs…",
                        attempt,
                        delay,
                    )
                    await asyncio.sleep(delay)
                    last_exc = exc
                else:
                    if delay is None and last_exc is not None:
                        raise last_exc from exc
                    raise
        assert last_exc is not None
        raise last_exc

    # ------------------------------------------------------------------
    # Subclass implementation hooks
    # ------------------------------------------------------------------

    @abstractmethod
    async def _generate(
        self,
        messages: list[Message],
        images: list[bytes] | None = None,
        **kwargs: Any,
    ) -> str: ...

    @abstractmethod
    async def _generate_structured(
        self,
        schema: type | dict[str, Any],
        messages: list[Message],
        **kwargs: Any,
    ) -> dict[str, Any]: ...

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _encode_image(image_bytes: bytes) -> str:
        """Base64-encode image bytes for API transmission."""
        import base64
        return base64.b64encode(image_bytes).decode()

    def __repr__(self) -> str:
        return f"{type(self).__name__}(model={self.model!r})"
