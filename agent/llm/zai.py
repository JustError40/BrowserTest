"""Z.AI LLM provider — OpenAI-compatible API at https://api.z.ai/api/paas/v4/"""

from __future__ import annotations

import asyncio
from typing import Any

from agent.llm.base import Message
from agent.llm.openai import OpenAILLM

_ZAI_BASE_URL = "https://api.z.ai/api/paas/v4/"
_MAX_CONCURRENT = 1  # glm-4.6v-flash allows 1 simultaneous request


class ZaiLLM(OpenAILLM):
    """Z.AI chat completion via OpenAI-compatible API.

    Uses the standard ``openai`` Python SDK pointed at the Z.AI endpoint.
    Limits concurrent in-flight requests to ``_MAX_CONCURRENT`` (1) via a
    class-level semaphore shared across all instances.
    """

    # Lazily created on first use so it inherits the running event loop.
    _semaphore: asyncio.Semaphore | None = None

    @classmethod
    def _get_semaphore(cls) -> asyncio.Semaphore:
        if cls._semaphore is None:
            cls._semaphore = asyncio.Semaphore(_MAX_CONCURRENT)
        return cls._semaphore

    @property
    def provider(self) -> str:
        return "zai"

    def _get_client(self):  # type: ignore[return]
        try:
            from openai import AsyncOpenAI
        except ImportError as e:
            raise ImportError("openai package is required: uv add openai") from e
        return AsyncOpenAI(
            api_key=self.api_key,
            base_url=_ZAI_BASE_URL,
            timeout=self.timeout,
        )

    async def _generate(
        self,
        messages: list[Message],
        images: list[bytes] | None = None,
        **kwargs: Any,
    ) -> str:
        async with self._get_semaphore():
            return await super()._generate(messages, images=images, **kwargs)

    async def _generate_structured(
        self,
        schema: type | dict[str, Any],
        messages: list[Message],
        **kwargs: Any,
    ) -> dict[str, Any]:
        async with self._get_semaphore():
            return await super()._generate_structured(schema, messages, **kwargs)
