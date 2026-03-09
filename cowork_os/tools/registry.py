"""ToolRegistry — dynamic registry of browser automation actions."""

from __future__ import annotations

import asyncio
import inspect
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

import structlog
from pydantic import BaseModel, create_model

from cowork_os.tools.views import ActionResult, RegisteredAction

if TYPE_CHECKING:
    from cowork_os.browser.session import BrowserSession

logger = structlog.get_logger(__name__)

# sentinel to identify browser_session parameters in action signatures
_BROWSER_SESSION_PARAM = "browser_session"


def _build_param_model(func: Callable) -> tuple[type[BaseModel], bool]:
    """Build a Pydantic model from a function's non-special parameters.

    Returns:
        (ParamModel, requires_browser) tuple.
    """
    sig = inspect.signature(func)
    fields: dict[str, Any] = {}
    requires_browser = False

    for name, param in sig.parameters.items():
        if name == _BROWSER_SESSION_PARAM:
            requires_browser = True
            continue  # skip — injected by registry at runtime

        annotation = param.annotation
        if annotation is inspect.Parameter.empty:
            annotation = Any  # fallback

        if param.default is inspect.Parameter.empty:
            fields[name] = (annotation, ...)
        else:
            fields[name] = (annotation, param.default)

    # Build a model named after the function
    model_name = f"{func.__name__.title().replace('_', '')}Params"
    ParamModel = create_model(model_name, **fields)  # noqa: N806
    return ParamModel, requires_browser


def _get_json_schema(model: type[BaseModel]) -> dict[str, Any]:
    """Return simplified JSON schema for a Pydantic model."""
    schema = model.model_json_schema()
    # Remove $defs if present (simplify for LLM)
    schema.pop("$defs", None)
    return schema


class Registry:
    """Registry for browser automation actions.

    Usage::

        registry = Registry()

        @registry.action(description="Navigate to a URL")
        async def navigate(url: str, browser_session: BrowserSession) -> ActionResult:
            await browser_session.get_current_page().goto(url)
            return ActionResult.ok()

        schema = registry.get_schema()
        result = await registry.execute("navigate", {"url": "https://..."}, session)
    """

    def __init__(self) -> None:
        self._actions: dict[str, tuple[RegisteredAction, Callable, type[BaseModel]]] = {}

    def action(self, description: str) -> Callable:
        """Decorator that registers a function as an action.

        Args:
            description: Human-readable description shown to the LLM.

        Returns:
            Decorator function.
        """

        def decorator(func: Callable) -> Callable:
            name = func.__name__
            param_model, requires_browser = _build_param_model(func)
            schema = _get_json_schema(param_model)

            registered = RegisteredAction(
                name=name,
                description=description,
                param_schema=schema,
                requires_browser=requires_browser,
            )
            self._actions[name] = (registered, func, param_model)
            logger.debug("Registered action", name=name, requires_browser=requires_browser)
            return func

        return decorator

    def get_schema(self) -> dict[str, Any]:
        """Return JSON Schema of all registered actions for LLM consumption.

        Format::

            {
              "click_element": {
                "description": "Click on an element",
                "parameters": { ...json_schema... }
              },
              ...
            }
        """
        return {
            name: {
                "description": registered.description,
                "parameters": registered.param_schema,
            }
            for name, (registered, _, _) in self._actions.items()
        }

    def get_actions(self) -> dict[str, RegisteredAction]:
        """Return all registered action metadata."""
        return {name: registered for name, (registered, _, _) in self._actions.items()}

    async def execute(
        self,
        name: str,
        params: dict[str, Any],
        browser_session: BrowserSession | None = None,
    ) -> ActionResult:
        """Execute a registered action by name.

        Args:
            name: Action name as registered.
            params: Parameters dict (validated against the action's schema).
            browser_session: Active browser session (injected if action requires it).

        Returns:
            ActionResult with success/failure and optional content.
        """
        if name not in self._actions:
            logger.warning("Action not found", name=name)
            return ActionResult.fail(f"Action '{name}' not registered")

        registered, func, param_model = self._actions[name]

        # Validate params against the param model
        try:
            validated = param_model.model_validate(params)
        except Exception as exc:
            return ActionResult.fail(f"Invalid parameters for '{name}': {exc}")

        # Build kwargs for actual call
        kwargs: dict[str, Any] = validated.model_dump()
        if registered.requires_browser:
            if browser_session is None:
                return ActionResult.fail(f"Action '{name}' requires browser_session but none provided")
            kwargs[_BROWSER_SESSION_PARAM] = browser_session

        # Call the function (sync or async)
        try:
            if asyncio.iscoroutinefunction(func):
                result = await func(**kwargs)
            else:
                result = func(**kwargs)

            # Normalize return value
            if isinstance(result, ActionResult):
                return result
            if isinstance(result, str):
                return ActionResult.ok(content=result)
            if result is None:
                return ActionResult.ok()
            return ActionResult.ok(content=str(result))

        except Exception as exc:
            logger.warning("Action execution failed", name=name, error=str(exc))
            return ActionResult.fail(str(exc))

    def list_names(self) -> list[str]:
        """Return list of all registered action names."""
        return list(self._actions.keys())


# ─── Module-level default registry ────────────────────────────────────────────

registry = Registry()
"""The global default tool registry. Import and use @registry.action(...)."""
