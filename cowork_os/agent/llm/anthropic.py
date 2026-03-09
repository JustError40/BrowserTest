"""Anthropic LLM provider."""

from __future__ import annotations

import json
import logging
from typing import Any

from cowork_os.agent.llm.base import BaseLLM, Message

logger = logging.getLogger(__name__)

_MAX_TOKENS_DEFAULT = 4096


class AnthropicLLM(BaseLLM):
    """Anthropic Claude provider."""

    @property
    def provider(self) -> str:
        return "anthropic"

    def _get_client(self):  # type: ignore[return]
        try:
            from anthropic import AsyncAnthropic
        except ImportError as e:
            raise ImportError("anthropic package is required: uv add anthropic") from e
        return AsyncAnthropic(api_key=self.api_key, timeout=self.timeout)

    @staticmethod
    def _split_system(messages: list[Message]) -> tuple[str, list[dict[str, Any]]]:
        """Extract system message (Anthropic passes it separately)."""
        system = ""
        api_msgs: list[dict[str, Any]] = []
        for msg in messages:
            role = msg.get("role", "user")
            content = msg.get("content", "")
            if role == "system":
                system = content
            else:
                api_msgs.append({"role": role, "content": content})
        return system, api_msgs

    async def _generate(
        self,
        messages: list[Message],
        images: list[bytes] | None = None,
        **kwargs: Any,
    ) -> str:
        client = self._get_client()
        system, api_msgs = self._split_system(messages)

        if images:
            content_parts: list[dict[str, Any]] = []
            for img_bytes in images:
                b64 = self._encode_image(img_bytes)
                content_parts.append(
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": "image/jpeg",
                            "data": b64,
                        },
                    }
                )
            content_parts.append({"type": "text", "text": "Analyse the images."})
            api_msgs.append({"role": "user", "content": content_parts})

        max_tokens = kwargs.pop("max_tokens", _MAX_TOKENS_DEFAULT)
        create_kwargs: dict[str, Any] = dict(
            model=self.model,
            max_tokens=max_tokens,
            messages=api_msgs,
            **kwargs,
        )
        if system:
            create_kwargs["system"] = system

        resp = await client.messages.create(**create_kwargs)
        block = resp.content[0] if resp.content else None
        return block.text if block and hasattr(block, "text") else ""

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
                "content": f"Reply ONLY with valid JSON that matches this schema:\n{schema_str}",
            }
        ]
        text = await self._generate(augmented, **kwargs)
        text = text.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
        return json.loads(text)
