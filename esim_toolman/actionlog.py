"""The action log: one JSON line per command esim-toolman ran that changes
the system, appended to ~/.esim-toolman/actions.log.

Only commands that actually ran are logged. A dry run, a refused plan item
or a declined prompt changes nothing, so nothing is written for them.

Each line holds: time (UTC, ISO 8601), tool, action ("install" or
"install-fallback"), backend, command (argument list), result ("ok",
"failed" or "error"), exit_code, verification ("passed", "failed" or
"skipped") and details (what verification found, or why it failed).
"""

import json
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


def default_log_path() -> Path:
    """~/.esim-toolman/actions.log for the current user."""
    return Path.home() / ".esim-toolman" / "actions.log"


def append(
    *,
    tool: str,
    action: str,
    backend: str,
    command: Sequence[str],
    result: str,
    exit_code: int | None,
    verification: str,
    details: Sequence[str] = (),
    path: Path | None = None,
    now: Callable[[], datetime] | None = None,
) -> dict[str, Any]:
    """Add one entry to the log (creating the folder if needed) and return it."""
    entry: dict[str, Any] = {
        "time": (now or _utc_now)().isoformat(timespec="seconds"),
        "tool": tool,
        "action": action,
        "backend": backend,
        "command": list(command),
        "result": result,
        "exit_code": exit_code,
        "verification": verification,
        "details": list(details),
    }
    path = path or default_log_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(entry) + "\n")
    return entry


def read_last(count: int, path: Path | None = None) -> tuple[list[dict[str, Any]], int]:
    """The last ``count`` entries, oldest first, and how many lines could
    not be read (a damaged line is skipped, not fatal). A missing log file
    means no entries."""
    path = path or default_log_path()
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        return [], 0
    entries: list[dict[str, Any]] = []
    unreadable = 0
    for line in lines:
        if not line.strip():
            continue
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            unreadable += 1
            continue
        if isinstance(entry, dict):
            entries.append(entry)
        else:
            unreadable += 1
    return (entries[-count:] if count > 0 else []), unreadable


def format_entry(entry: dict[str, Any]) -> str:
    """One entry as a line of text, e.g.
    2026-11-20T10:00:00+00:00  iverilog  install (apt)  ok, exit 0, verification passed: sudo apt-get install -y iverilog
    """
    command = " ".join(str(part) for part in entry.get("command", []))
    exit_code = entry.get("exit_code")
    outcome = f"{entry.get('result', '?')}, exit {'-' if exit_code is None else exit_code}"
    text = (
        f"{entry.get('time', '?')}  {entry.get('tool', '?')}  "
        f"{entry.get('action', '?')} ({entry.get('backend', '?')})  "
        f"{outcome}, verification {entry.get('verification', '?')}: {command}"
    )
    details = entry.get("details") or []
    return "\n".join([text, *(f"    {detail}" for detail in details)])


def _utc_now() -> datetime:
    return datetime.now(UTC)
