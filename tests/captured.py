"""Real command outputs captured on real machines, for tests.

- tests/fixtures/ubuntu-24.04/: Ubuntu 24.04.5 (WSL2), 2026-11-20.
- tests/fixtures/windows-11/: Windows 11, 2026-11-21, after a real
  `winget install --id Icarus.Verilog`.

Each JSON file is one command exactly as it ran there: "command" (the full
argument list), "exit_code", "stdout" and "stderr". `os-release` is the
Ubuntu machine's /etc/os-release. Each folder's README says how they were
captured.
"""

import json
from pathlib import Path
from typing import Any

UBUNTU_2404 = Path(__file__).parent / "fixtures" / "ubuntu-24.04"
WINDOWS_11 = Path(__file__).parent / "fixtures" / "windows-11"


def load(name: str, folder: Path = UBUNTU_2404) -> dict[str, Any]:
    """One captured command, e.g. load("ghdl-version") or
    load("iverilog-version", WINDOWS_11)."""
    return json.loads((folder / f"{name}.json").read_text(encoding="utf-8"))


def output(name: str, folder: Path = UBUNTU_2404) -> str:
    """stdout and stderr together, the way DetectResult.output joins them."""
    run = load(name, folder)
    return "\n".join(part for part in (run["stdout"], run["stderr"]) if part)


def all_runs() -> list[dict[str, Any]]:
    """Every captured command."""
    return [load(path.stem) for path in sorted(UBUNTU_2404.glob("*.json"))]
