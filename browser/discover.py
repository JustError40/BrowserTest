"""Browser discovery — find a running Chrome/Chromium with remote-debugging-port.

Usage::

    from browser.discover import find_running_chrome

    url = await find_running_chrome()
    # → "http://localhost:9222"  or None if nothing found

The function probes the common CDP ports (9222, 9223, 9229) by hitting
``/json/version``.  On Linux it also reads ``/proc/*/cmdline`` to extract
the exact ``--remote-debugging-port`` value in case Chrome uses a non-standard
port.
"""

from __future__ import annotations

import os
import re
import socket

import structlog

logger = structlog.get_logger(__name__)

# Ports to probe as a first-pass heuristic
_DEFAULT_PROBE_PORTS = [9222, 9223, 9229, 9224, 4444]


# ─── Low-level probing ────────────────────────────────────────────────────────


def _port_open(host: str, port: int, timeout: float = 0.5) -> bool:
    """Return True if *host:port* accepts a TCP connection."""
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def _fetch_json_version(host: str, port: int) -> dict | None:
    """Fetch /json/version from a CDP endpoint, return parsed dict or None.

    Uses only stdlib (urllib) so this can be called from sync contexts too.
    """
    import json
    import urllib.request

    url = f"http://{host}:{port}/json/version"
    try:
        with urllib.request.urlopen(url, timeout=1.0) as resp:  # noqa: S310
            return json.loads(resp.read())
    except Exception:
        return None


# ─── Linux /proc scanning ─────────────────────────────────────────────────────


def _scan_proc_for_debug_ports() -> list[int]:
    """Read /proc/*/cmdline on Linux to find Chrome processes with --remote-debugging-port.

    Returns a list of int port numbers (possibly empty on non-Linux or if
    no matching processes are found).
    """
    if not os.path.isdir("/proc"):
        return []

    ports: list[int] = []
    try:
        for entry in os.scandir("/proc"):
            if not entry.name.isdigit():
                continue
            try:
                cmdline_path = os.path.join(entry.path, "cmdline")
                with open(cmdline_path, "rb") as fh:
                    cmdline = fh.read().decode("utf-8", errors="replace")
            except OSError:
                continue

            # Chrome/Chromium binary check
            if not any(
                name in cmdline
                for name in ("chrome", "chromium", "google-chrome", "microsoft-edge")
            ):
                continue

            # Extract --remote-debugging-port=NNNN
            match = re.search(r"--remote-debugging-port[=\x00](\d+)", cmdline)
            if match:
                port = int(match.group(1))
                if port not in ports:
                    ports.append(port)
    except Exception as exc:
        logger.debug("proc scan error", error=str(exc))

    return ports


# ─── Public API ───────────────────────────────────────────────────────────────


def probe_cdp_url(cdp_url: str) -> dict | None:
    """Probe a full CDP URL (e.g. ``http://localhost:9222``).

    Returns the /json/version dict on success, None if unreachable.
    """
    import re as _re

    m = _re.match(r"https?://([^:/]+):(\d+)", cdp_url.rstrip("/"))
    if not m:
        return None
    host, port = m.group(1), int(m.group(2))
    return _fetch_json_version(host, port)


def find_running_chrome(
    host: str = "localhost",
    extra_ports: list[int] | None = None,
) -> str | None:
    """Synchronously scan for a running Chrome with remote-debugging enabled.

    Probing order:
    1. Linux ``/proc`` scan to find exact ports used by live Chrome processes.
    2. Common well-known fallback ports (9222, 9223, …).

    Returns:
        CDP URL string like ``"http://localhost:9222"`` if found, else ``None``.
    """
    probe_ports: list[int] = []

    # 1. Try /proc scan first (Linux only) — gives the exact port
    proc_ports = _scan_proc_for_debug_ports()
    probe_ports.extend(proc_ports)

    # 2. Add well-known fallback ports (deduped)
    for p in (extra_ports or []) + _DEFAULT_PROBE_PORTS:
        if p not in probe_ports:
            probe_ports.append(p)

    for port in probe_ports:
        info = _fetch_json_version(host, port)
        if info is not None:
            url = f"http://{host}:{port}"
            logger.info(
                "Found running browser",
                url=url,
                browser=info.get("Browser", "unknown"),
            )
            return url

    return None


def require_chrome_or_raise(cdp_url: str) -> dict:
    """Verify *cdp_url* is reachable and return browser info.

    Raises:
        RuntimeError: with a helpful message if the browser is not reachable.
    """
    info = probe_cdp_url(cdp_url)
    if info is None:
        raise RuntimeError(
            f"Cannot connect to browser at {cdp_url!r}.\n"
            "Make sure Chrome/Chromium is running with remote debugging enabled:\n\n"
            "  Linux/macOS:\n"
            "    google-chrome --remote-debugging-port=9222 --no-first-run\n\n"
            "  Windows (cmd):\n"
            '    "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe" '
            "--remote-debugging-port=9222 --no-first-run\n\n"
            "  Windows (PowerShell script):\n"
            "    .\\scripts\\start_chrome_windows.ps1\n\n"
            "Then verify with:\n"
            f"  curl {cdp_url}/json/version"
        )
    return info
