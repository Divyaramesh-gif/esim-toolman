"""Tests for esim_toolman.detect.

shutil.which and subprocess.run are replaced by small fakes, and search
folders are created inside pytest's temporary folder, so nothing here
depends on which tools this machine has installed.
"""

import re
import subprocess
from pathlib import Path
from typing import Any

import pytest

from esim_toolman.detect import (
    Problem,
    candidate_directories,
    detect_tool,
    expand_search_path,
    find_executable,
)
from esim_toolman.manifest import SearchPaths, Tool, VersionSpec


def make_tool(
    executables: tuple[str, ...] = ("demo",),
    linux: tuple[str, ...] = (),
    windows: tuple[str, ...] = (),
) -> Tool:
    return Tool(
        key="demo",
        name="Demo",
        purpose="test tool",
        required=True,
        executables=executables,
        version=VersionSpec(
            args=("--version",),
            pattern=re.compile(r"demo (\d+(?:\.\d+)*)"),
            source="test",
        ),
        search_paths=SearchPaths(linux=linux, windows=windows),
    )


class FakeWhich:
    """Stands in for shutil.which. ``on_path`` maps a name to the path found
    on PATH; ``in_dirs`` maps (folder, name) to the path found in a folder."""

    def __init__(
        self,
        on_path: dict[str, str] | None = None,
        in_dirs: dict[tuple[str, str], str] | None = None,
    ) -> None:
        self.on_path = on_path or {}
        self.in_dirs = in_dirs or {}

    def __call__(self, name: str, path: str | None = None) -> str | None:
        if path is None:
            return self.on_path.get(name)
        return self.in_dirs.get((path, name))


class FakeRunner:
    """Stands in for subprocess.run: records each call, then returns a
    canned result or raises a canned exception."""

    def __init__(
        self,
        stdout: str = "",
        stderr: str = "",
        returncode: int = 0,
        raises: BaseException | None = None,
    ) -> None:
        self.stdout, self.stderr, self.returncode = stdout, stderr, returncode
        self.raises = raises
        self.calls: list[tuple[list[str], dict[str, Any]]] = []

    def __call__(self, args: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        self.calls.append((args, kwargs))
        if self.raises is not None:
            raise self.raises
        return subprocess.CompletedProcess(args, self.returncode, self.stdout, self.stderr)


def detect(tool: Tool, which: FakeWhich, runner: FakeRunner, **kwargs: Any):
    """detect_tool on a pretend Linux machine with an empty environment."""
    return detect_tool(tool, "linux", env={}, home="/nonexistent", which=which, runner=runner, **kwargs)


# ---------------------------------------------------------- expanding paths


def test_expand_home() -> None:
    assert expand_search_path("~/x/bin", {}, "/home/me") == "/home/me/x/bin"


def test_expand_percent_variables_ignore_case() -> None:
    env = {"PROGRAMFILES": r"C:\Program Files"}
    assert expand_search_path(r"%ProgramFiles%\KiCad", env, "") == r"C:\Program Files\KiCad"


def test_expand_percent_variable_with_brackets() -> None:
    env = {"ProgramFiles(x86)": r"C:\Program Files (x86)"}
    assert expand_search_path(r"%ProgramFiles(x86)%\X", env, "") == r"C:\Program Files (x86)\X"


def test_expand_brace_variables() -> None:
    assert expand_search_path("${ROOT}/bin", {"ROOT": "/opt/x"}, "") == "/opt/x/bin"


def test_unset_variable_means_no_path() -> None:
    assert expand_search_path("%NOT_SET%/bin", {}, "") is None


def test_text_without_variables_is_unchanged() -> None:
    assert expand_search_path("/usr/local/bin", {}, "/home/me") == "/usr/local/bin"


# ------------------------------------------------------ candidate folders


def test_glob_matches_are_sorted_highest_version_first(tmp_path: Path) -> None:
    for version in ("8.0", "9.0", "10.0"):
        (tmp_path / "KiCad" / version / "bin").mkdir(parents=True)
    tool = make_tool(windows=("%ROOT%/KiCad/*/bin",))

    found = candidate_directories(tool, "windows", env={"ROOT": str(tmp_path)}, home="")

    expected = [tmp_path / "KiCad" / version / "bin" for version in ("10.0", "9.0", "8.0")]
    assert [Path(directory) for directory in found] == expected


def test_only_existing_folders_for_this_system_are_kept(tmp_path: Path) -> None:
    (tmp_path / "linux-bin").mkdir()
    (tmp_path / "windows-bin").mkdir()
    tool = make_tool(
        linux=("~/linux-bin", "~/missing"),
        windows=("%ROOT%/windows-bin",),
    )
    env = {"ROOT": str(tmp_path)}

    linux = candidate_directories(tool, "linux", env=env, home=str(tmp_path))
    windows = candidate_directories(tool, "windows", env=env, home=str(tmp_path))
    other = candidate_directories(tool, "darwin", env=env, home=str(tmp_path))

    assert [Path(d) for d in linux] == [tmp_path / "linux-bin"]
    assert [Path(d) for d in windows] == [tmp_path / "windows-bin"]
    assert other == ()


# ------------------------------------------------------- finding programs


def test_folder_order_beats_name_order() -> None:
    # eSim's console ngspice.exe in the first folder must win over the
    # official ngspice_con.exe in the second folder.
    which = FakeWhich(
        on_path={"ngspice_con": "/path/ngspice_con"},
        in_dirs={
            ("first", "ngspice"): "first/ngspice.exe",
            ("second", "ngspice_con"): "second/ngspice_con.exe",
        },
    )
    assert find_executable(("ngspice_con", "ngspice"), ("first", "second"), which) == "first/ngspice.exe"


def test_name_order_within_one_folder() -> None:
    which = FakeWhich(
        in_dirs={("bin", "ngspice_con"): "bin/ngspice_con.exe", ("bin", "ngspice"): "bin/ngspice.exe"}
    )
    assert find_executable(("ngspice_con", "ngspice"), ("bin",), which) == "bin/ngspice_con.exe"


def test_path_is_searched_after_the_extra_folders() -> None:
    which = FakeWhich(on_path={"demo": "/usr/bin/demo"})
    assert find_executable(("demo",), ("empty-folder",), which) == "/usr/bin/demo"


def test_extra_folder_is_used_before_path(tmp_path: Path) -> None:
    (tmp_path / "bin").mkdir()
    folder = f"{tmp_path}/bin"  # the exact text glob returns for this pattern
    which = FakeWhich(
        on_path={"demo": "/usr/bin/demo"},
        in_dirs={(folder, "demo"): f"{folder}/demo"},
    )
    runner = FakeRunner(stdout="demo 1.0\n")
    result = detect_tool(
        make_tool(linux=("~/bin",)), "linux", env={}, home=str(tmp_path), which=which, runner=runner
    )
    assert result.path == f"{folder}/demo"
    assert result.searched == (folder,)


# ------------------------------------------------- running the version command


def test_not_found_runs_nothing() -> None:
    runner = FakeRunner()
    result = detect(make_tool(executables=("demo", "demo2")), FakeWhich(), runner)

    assert result.found is False
    assert result.problem is Problem.NOT_FOUND
    assert result.error is not None and "demo / demo2 not found on PATH" in result.error
    assert runner.calls == []


def test_version_command_is_run_safely() -> None:
    runner = FakeRunner(stdout="demo 1.2.3\n")
    result = detect(make_tool(), FakeWhich(on_path={"demo": "/usr/bin/demo"}), runner, timeout=7)

    (args, kwargs) = runner.calls[0]
    assert args == ["/usr/bin/demo", "--version"]  # a list of arguments, no shell string
    assert kwargs.get("shell", False) is False
    assert kwargs["timeout"] == 7
    assert kwargs["stdin"] is subprocess.DEVNULL
    assert kwargs["capture_output"] is True
    assert result.found is True
    assert result.problem is None and result.error is None
    assert result.exit_code == 0
    assert result.command == ("/usr/bin/demo", "--version")
    assert "demo 1.2.3" in result.output


def test_stderr_is_part_of_the_output() -> None:
    runner = FakeRunner(stdout="", stderr="demo 2.0\n")
    result = detect(make_tool(), FakeWhich(on_path={"demo": "/usr/bin/demo"}), runner)
    assert result.stderr == "demo 2.0\n"
    assert "demo 2.0" in result.output


def test_timeout_is_reported_and_partial_output_kept() -> None:
    timeout = subprocess.TimeoutExpired(["/usr/bin/demo", "--version"], 5, output=b"demo 1.")
    runner = FakeRunner(raises=timeout)
    result = detect(make_tool(), FakeWhich(on_path={"demo": "/usr/bin/demo"}), runner, timeout=5)

    assert result.found is True
    assert result.problem is Problem.TIMEOUT
    assert result.error is not None and "5 seconds" in result.error
    assert result.stdout == "demo 1."  # bytes from TimeoutExpired, decoded


def test_program_that_cannot_start() -> None:
    runner = FakeRunner(raises=PermissionError(13, "Permission denied"))
    result = detect(make_tool(), FakeWhich(on_path={"demo": "/usr/bin/demo"}), runner)

    assert result.found is True
    assert result.problem is Problem.START_FAILED
    assert result.error is not None and "Permission denied" in result.error


def test_non_zero_exit_is_reported_but_output_kept() -> None:
    runner = FakeRunner(stdout="demo 3.1\n", stderr="warning: odd\n", returncode=2)
    result = detect(make_tool(), FakeWhich(on_path={"demo": "/usr/bin/demo"}), runner)

    assert result.problem is Problem.EXIT_STATUS
    assert result.exit_code == 2
    assert result.error is not None and "code 2" in result.error
    assert "demo 3.1" in result.output


@pytest.mark.parametrize("returncode", [-11, 139])
def test_crash_exit_codes_are_reported(returncode: int) -> None:
    runner = FakeRunner(returncode=returncode)
    result = detect(make_tool(), FakeWhich(on_path={"demo": "/usr/bin/demo"}), runner)
    assert result.problem is Problem.EXIT_STATUS
    assert result.output == ""
