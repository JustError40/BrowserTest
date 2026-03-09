"""Ollama LLM provider (OpenAI-compatible local API)."""

from __future__ import annotations

import json
import logging
from typing import Any

from cowork_os.agent.llm.base import BaseLLM, Message

logger = logging.getLogger(__name__)

_DEFAULT_BASE_URL = "http://localhost:11434/v1"


class OllamaLLM(BaseLLM):
    """Ollama local model provider via OpenAI-compatible endpoint."""

    def __init__(
        self,
        model: str,
        api_key: str = "ollama",
        timeout: float = 60.0,
        base_url: str = _DEFAULT_BASE_URL,
    ) -> None:
        super().__init__(model=model, api_key=api_key or "ollama", timeout=timeout)
        self.base_url = base_url

    @property
    def provider(self) -> str:
        return "ollama"

    def _get_client(self):  # type: ignore[return]
        try:
            from openai import AsyncOpenAI
        except ImportError as e:
            raise ImportError("openai package is required: uv add openai") from e
        return AsyncOpenAI(
            api_key=self.api_key,
            base_url=self.base_url,
            timeout=self.timeout,
        )

    async def _generate(
        self,
        messages: list[Message],
        images: list[bytes] | None = None,
        **kwargs: Any,
    ) -> str:
        client = self._get_client()
        api_messages = [{"role": m.get("role", "user"), "content": m.get("content", "")} for m in messages]

        # Vision support via base64 images (Ollama supports LLaVA-style multimodal)
        if images:
            image_content: list[dict[str, Any]] = [
                {
                    "type": "image_url",
                    "image_url": {"url": f"data:image/jpeg;base64,{self._encode_image(b)}"},
                }
                for b in images
            ]
            image_content.append({"type": "text", "text": "Analyse the images."})
            api_messages.append({"role": "user", "content": image_content})

        resp = await client.chat.completions.create(
            model=self.model,
            messages=api_messages,  # type: ignore[arg-type]
            **kwargs,
        )
        return resp.choices[0].message.content or ""

    async def _generate_structured(
        self,
        schema: type | dict[str, Any],
        messages: list[Message],
        **kwargs: Any,
    ) -> dict[str, Any]:
        from pydantic import BaseModel as PydanticBase

        if isinstance(schema, type) and issubclass(schema, PydanticBase):
            schema_dict = schema.model_json_schema()
        else:
            schema_dict = schema

        schema_str = json.dumps(schema_dict, ensure_ascii=False)
        augmented = list(messages) + [
            {
                "role": "user",
                "content": f"Reply ONLY with valid JSON matching this schema:\n{schema_str}",
            }
        ]
        text = await self._generate(augmented, **kwargs)
        text = text.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
        return json.loads(text)
