"""Tests for esim_toolman.actionlog. Every test writes to a temporary file."""

import json
from datetime import UTC, datetime
from pathlib import Path

from esim_toolman import actionlog


def fixed_time() -> datetime:
    return datetime(2026, 11, 20, 10, 0, 0, tzinfo=UTC)


def add(path: Path, tool: str = "iverilog", **changes: object) -> dict[str, object]:
    values: dict[str, object] = {
        "tool": tool,
        "action": "install",
        "backend": "apt",
        "command": ["sudo", "apt-get", "install", "-y", tool],
        "result": "ok",
        "exit_code": 0,
        "verification": "passed",
        "details": ["Icarus Verilog 12.0 found at /usr/bin/iverilog."],
    }
    values.update(changes)
    return actionlog.append(path=path, now=fixed_time, **values)  # type: ignore[arg-type]


def test_append_writes_one_json_line_with_every_field(tmp_path: Path) -> None:
    path = tmp_path / "new-folder" / "actions.log"  # the folder does not exist yet
    add(path)
    lines = path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    assert json.loads(lines[0]) == {
        "time": "2026-11-20T10:00:00+00:00",
        "tool": "iverilog",
        "action": "install",
        "backend": "apt",
        "command": ["sudo", "apt-get", "install", "-y", "iverilog"],
        "result": "ok",
        "exit_code": 0,
        "verification": "passed",
        "details": ["Icarus Verilog 12.0 found at /usr/bin/iverilog."],
    }


def test_append_adds_to_the_end(tmp_path: Path) -> None:
    path = tmp_path / "actions.log"
    add(path, "iverilog")
    add(path, "kicad")
    tools = [json.loads(line)["tool"] for line in path.read_text(encoding="utf-8").splitlines()]
    assert tools == ["iverilog", "kicad"]


def test_default_path_is_in_the_home_folder() -> None:
    assert actionlog.default_log_path() == Path.home() / ".esim-toolman" / "actions.log"


def test_read_last_returns_the_newest_entries_oldest_first(tmp_path: Path) -> None:
    path = tmp_path / "actions.log"
    for tool in ("a", "b", "c"):
        add(path, tool)
    entries, unreadable = actionlog.read_last(2, path)
    assert [entry["tool"] for entry in entries] == ["b", "c"]
    assert unreadable == 0


def test_read_last_without_a_log_file(tmp_path: Path) -> None:
    assert actionlog.read_last(5, tmp_path / "missing.log") == ([], 0)


def test_read_last_skips_damaged_lines(tmp_path: Path) -> None:
    path = tmp_path / "actions.log"
    add(path, "a")
    with path.open("a", encoding="utf-8") as handle:
        handle.write("this is not json\n\n[1, 2]\n")
    add(path, "b")
    entries, unreadable = actionlog.read_last(10, path)
    assert [entry["tool"] for entry in entries] == ["a", "b"]
    assert unreadable == 2


def test_format_entry() -> None:
    entry: dict[str, object] = {
        "time": "2026-11-20T10:00:00+00:00",
        "tool": "ghdl",
        "action": "install-fallback",
        "backend": "apt",
        "command": ["sudo", "apt-get", "install", "-y", "ghdl-gcc"],
        "result": "ok",
        "exit_code": 0,
        "verification": "passed",
        "details": ["GHDL 4.1.0 found at /usr/bin/ghdl."],
    }
    assert actionlog.format_entry(entry) == (
        "2026-11-20T10:00:00+00:00  ghdl  install-fallback (apt)  ok, exit 0, "
        "verification passed: sudo apt-get install -y ghdl-gcc\n"
        "    GHDL 4.1.0 found at /usr/bin/ghdl."
    )
    entry["exit_code"] = None
    assert "exit -" in actionlog.format_entry(entry)
