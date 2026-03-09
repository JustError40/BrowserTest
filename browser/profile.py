"""BrowserProfile and ProfileManager for persistent Chrome profiles."""

from __future__ import annotations

import json
import os
from pathlib import Path

import structlog
from pydantic import BaseModel, Field

logger = structlog.get_logger(__name__)

# ─── Chrome launch arguments ──────────────────────────────────────────────────

_CHROME_BASE_ARGS: list[str] = [
    "--disable-blink-features=AutomationControlled",
    "--disable-background-timer-throttling",
    "--disable-backgrounding-occluded-windows",
    "--disable-renderer-backgrounding",
    "--disable-infobars",
    "--no-first-run",
    "--no-default-browser-check",
    "--disable-translate",
    "--disable-extensions-except",  # placeholder, overridden if extensions provided
    "--disable-popup-blocking",
    "--disable-notifications",
    "--disable-save-password-bubble",
]

_CHROME_HEADLESS_ARGS: list[str] = [
    "--headless=new",
]

_CHROME_DOCKER_ARGS: list[str] = [
    "--no-sandbox",
    "--disable-setuid-sandbox",
    "--disable-dev-shm-usage",
    "--no-zygote",
    "--disable-gpu",
]


def _is_docker() -> bool:
    """Detect if running inside a Docker container."""
    return Path("/.dockerenv").exists() or os.environ.get("DOCKER_CONTAINER") == "1"


# ─── BrowserProfile ────────────────────────────────────────────────────────────


class BrowserProfile(BaseModel):
    """A named, persistent browser profile configuration."""

    name: str = "default"
    headless: bool = True
    proxy: str | None = None
    extensions: list[str] = Field(default_factory=list)
    extra_args: list[str] = Field(default_factory=list)

    def __init__(self, name: str = "default", **data: object) -> None:  # type: ignore[override]
        """Allow positional name argument: BrowserProfile('test')."""
        super().__init__(name=name, **data)

    def _get_profile_dir(self) -> Path:
        """Resolve user_data_dir for this profile from Settings."""
        try:
            from config.settings import get_settings

            base = Path(get_settings().browser_profile_dir)
        except Exception:
            base = Path("./profiles")
        return base / self.name

    def get_launch_args(self) -> dict:
        """Return Playwright launch kwargs for this profile.

        Returns:
            dict with keys: args, headless, user_data_dir (and proxy if set)
        """
        user_data_dir = self._get_profile_dir()
        user_data_dir.mkdir(parents=True, exist_ok=True)

        args = list(_CHROME_BASE_ARGS)
        # Remove placeholder if no extensions
        args = [a for a in args if a != "--disable-extensions-except"]
        if self.extensions:
            args.append(f"--load-extension={','.join(self.extensions)}")

        if self.headless:
            args.extend(_CHROME_HEADLESS_ARGS)

        if _is_docker():
            args.extend(_CHROME_DOCKER_ARGS)

        args.extend(self.extra_args)

        result: dict = {
            "args": args,
            "headless": self.headless,
            "user_data_dir": str(user_data_dir),
        }
        if self.proxy:
            result["proxy"] = {"server": self.proxy}

        return result


# ─── ProfileManager ────────────────────────────────────────────────────────────


class _ProfileRecord(BaseModel):
    """Stored metadata for a browser profile."""

    name: str
    headless: bool = True
    proxy: str | None = None
    extensions: list[str] = Field(default_factory=list)


class ProfileManager:
    """Manages the lifecycle of named browser profiles.

    Profiles metadata is persisted in ``{browser_profile_dir}/profiles.json``.
    """

    def __init__(self, base_dir: str | Path | None = None) -> None:
        if base_dir is None:
            try:
                from config.settings import get_settings

                base_dir = Path(get_settings().browser_profile_dir)
            except Exception:
                base_dir = Path("./profiles")
        self._base_dir = Path(base_dir)
        self._index_file = self._base_dir / "profiles.json"
        self._base_dir.mkdir(parents=True, exist_ok=True)

    # ── Index I/O ─────────────────────────────────────────────────────────────

    def _load_index(self) -> dict[str, _ProfileRecord]:
        if not self._index_file.exists():
            return {}
        try:
            data = json.loads(self._index_file.read_text(encoding="utf-8"))
            return {name: _ProfileRecord(**rec) for name, rec in data.items()}
        except Exception as exc:
            logger.warning("Failed to load profiles.json, starting fresh", error=str(exc))
            return {}

    def _save_index(self, records: dict[str, _ProfileRecord]) -> None:
        self._index_file.write_text(
            json.dumps({name: rec.model_dump() for name, rec in records.items()}, indent=2),
            encoding="utf-8",
        )

    # ── CRUD ──────────────────────────────────────────────────────────────────

    def create(
        self,
        name: str,
        *,
        headless: bool = True,
        proxy: str | None = None,
        extensions: list[str] | None = None,
    ) -> BrowserProfile:
        """Create a new browser profile (or return existing one idempotently).

        Args:
            name: Profile name (also used as the sub-directory name).
            headless: Whether this profile defaults to headless mode.
            proxy: Optional proxy server URL.
            extensions: Optional list of extension paths to load.

        Returns:
            BrowserProfile ready to use.
        """
        records = self._load_index()
        if name in records:
            logger.debug("Profile already exists, returning existing", profile=name)
            rec = records[name]
        else:
            rec = _ProfileRecord(name=name, headless=headless, proxy=proxy, extensions=extensions or [])
            records[name] = rec
            self._save_index(records)
            # Ensure the profile directory exists
            profile_dir = self._base_dir / name
            profile_dir.mkdir(parents=True, exist_ok=True)
            logger.info("Created browser profile", profile=name, dir=str(profile_dir))

        return BrowserProfile(
            name=rec.name,
            headless=rec.headless,
            proxy=rec.proxy,
            extensions=rec.extensions,
        )

    def list(self) -> list[str]:  # noqa: A003
        """Return the names of all registered profiles."""
        return list(self._load_index().keys())

    def delete(self, name: str) -> None:
        """Remove a profile from the registry (does NOT delete files on disk).

        Args:
            name: Name of the profile to remove.
        """
        records = self._load_index()
        if name not in records:
            logger.warning("Profile not found for deletion", profile=name)
            return
        del records[name]
        self._save_index(records)
        logger.info("Deleted browser profile from registry", profile=name)

    def get(self, name: str) -> BrowserProfile | None:
        """Return a BrowserProfile by name, or None if not registered."""
        records = self._load_index()
        rec = records.get(name)
        if rec is None:
            return None
        return BrowserProfile(name=rec.name, headless=rec.headless, proxy=rec.proxy, extensions=rec.extensions)


# ─── Module-level default manager ────────────────────────────────────────────

_default_manager: ProfileManager | None = None


def get_profile_manager() -> ProfileManager:
    """Return the default ProfileManager singleton."""
    global _default_manager  # noqa: PLW0603
    if _default_manager is None:
        _default_manager = ProfileManager()
    return _default_manager
