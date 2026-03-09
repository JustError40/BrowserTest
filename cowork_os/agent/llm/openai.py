"""OpenAI LLM provider."""

from __future__ import annotations

import json
import logging
from typing import Any

from cowork_os.agent.llm.base import BaseLLM, Message

logger = logging.getLogger(__name__)


class OpenAILLM(BaseLLM):
    """OpenAI chat completion provider."""

    @property
    def provider(self) -> str:
        return "openai"

    def _get_client(self):  # type: ignore[return]
        try:
            from openai import AsyncOpenAI
        except ImportError as e:
            raise ImportError("openai package is required: uv add openai") from e
        return AsyncOpenAI(api_key=self.api_key, timeout=self.timeout)

    async def _generate(
        self,
        messages: list[Message],
        images: list[bytes] | None = None,
        **kwargs: Any,
    ) -> str:
        client = self._get_client()
        api_messages: list[dict[str, Any]] = []
        for msg in messages:
            role = msg.get("role", "user")
            content = msg.get("content", "")
            api_messages.append({"role": role, "content": content})

        # Append images as a final user message if provided
        if images:
            image_content: list[dict[str, Any]] = []
            for img_bytes in images:
                b64 = self._encode_image(img_bytes)
                image_content.append(
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:image/jpeg;base64,{b64}"},
                    }
                )
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
        client = self._get_client()
        api_messages = [{"role": m.get("role", "user"), "content": m.get("content", "")} for m in messages]

        from pydantic import BaseModel as PydanticBase

        if isinstance(schema, type) and issubclass(schema, PydanticBase):
            # Use structured-output beta
            resp = await client.beta.chat.completions.parse(
                model=self.model,
                messages=api_messages,  # type: ignore[arg-type]
                response_format=schema,
                **kwargs,
            )
            parsed = resp.choices[0].message.parsed
            return parsed.model_dump() if parsed else {}
        else:
            # schema is a JSON schema dict — ask the model to return JSON
            schema_str = json.dumps(schema, ensure_ascii=False)
            api_messages.append(
                {
                    "role": "system",
                    "content": f"Reply ONLY with valid JSON matching this schema: {schema_str}",
                }
            )
            text = await self._generate(messages=api_messages, **kwargs)
            # Strip markdown code fences if present
            text = text.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
            return json.loads(text)
