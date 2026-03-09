"""LLM factory — create provider instances by name."""

from __future__ import annotations

from agent.llm.base import BaseLLM

_PROVIDERS: dict[str, str] = {
    "openai": "agent.llm.openai.OpenAILLM",
    "anthropic": "agent.llm.anthropic.AnthropicLLM",
    "ollama": "agent.llm.ollama.OllamaLLM",
}


class LLMFactory:
    """Factory for creating LLM provider instances."""

    @staticmethod
    def create(
        provider: str,
        model: str,
        api_key: str = "",
        timeout: float = 60.0,
        **kwargs,
    ) -> BaseLLM:
        """Create and return an LLM instance for the given provider.

        Args:
            provider: One of 'openai', 'anthropic', 'ollama'.
            model: Model name (e.g. 'gpt-4o-mini', 'claude-haiku-4-5-20251001', 'llama3').
            api_key: API key (empty string is fine for Ollama).
            timeout: Request timeout in seconds (default 60).
            **kwargs: Extra kwargs forwarded to the provider constructor.

        Returns:
            A BaseLLM instance for the selected provider.

        Raises:
            ValueError: If the provider is not recognised.
        """
        provider = provider.lower().strip()
        if provider not in _PROVIDERS:
            supported = ", ".join(sorted(_PROVIDERS))
            raise ValueError(
                f"Unknown LLM provider {provider!r}. Supported: {supported}"
            )

        # Lazy import to avoid SDK imports unless actually needed
        module_path, cls_name = _PROVIDERS[provider].rsplit(".", 1)
        import importlib

        mod = importlib.import_module(module_path)
        cls = getattr(mod, cls_name)
        return cls(model=model, api_key=api_key, timeout=timeout, **kwargs)

    @staticmethod
    def list_providers() -> list[str]:
        """Return list of supported provider names."""
        return sorted(_PROVIDERS)
