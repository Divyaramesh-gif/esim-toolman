"""Real command outputs captured on Ubuntu 24.04.5 (WSL2) on 2026-11-20.

Each JSON file in tests/fixtures/ubuntu-24.04/ is one command exactly as it
ran there: "command" (the full argument list), "exit_code", "stdout" and
"stderr". `os-release` is that machine's /etc/os-release. See the README in
that folder for how they were captured.
"""

import json
from pathlib import Path
from typing import Any

UBUNTU_2404 = Path(__file__).parent / "fixtures" / "ubuntu-24.04"


def load(name: str) -> dict[str, Any]:
    """One captured command, e.g. load("ghdl-version")."""
    return json.loads((UBUNTU_2404 / f"{name}.json").read_text(encoding="utf-8"))


def output(name: str) -> str:
    """stdout and stderr together, the way DetectResult.output joins them."""
    run = load(name)
    return "\n".join(part for part in (run["stdout"], run["stderr"]) if part)


def all_runs() -> list[dict[str, Any]]:
    """Every captured command."""
    return [load(path.stem) for path in sorted(UBUNTU_2404.glob("*.json"))]
