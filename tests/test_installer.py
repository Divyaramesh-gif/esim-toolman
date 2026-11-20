"""Tests for esim_toolman.installer.

FakeComputer stands in for shutil.which, subprocess.run, os.path.realpath
and the user's keyboard. Install commands are fake too: a test decides what
"running" one changes (usually: the program now exists). Nothing real is
ever installed. The tests use the real manifest and, where they exist, real
outputs captured on Ubuntu 24.04 (tests/fixtures/ubuntu-24.04/).
"""

import json
import os
import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from captured import load

from esim_toolman.backends import AptBackend, WingetBackend
from esim_toolman.installer import Action, Machine, build_plan, format_plan, install, make_machine
from esim_toolman.manifest import load_manifest
from esim_toolman.platform_info import PlatformInfo
from esim_toolman.versions import Status

MANIFEST = load_manifest()
UBUNTU = PlatformInfo(
    system="linux", distro_id="ubuntu", distro_version="24.04",
    distro_name="Ubuntu 24.04.5 LTS", package_managers=("apt-get",),
)
WINDOWS = PlatformInfo(system="windows", package_managers=("winget",))

# A response is (stdout, exit code), a captured real run (dict), an
# exception to raise, or a function with no arguments returning one of
# those (used for install commands that change the fake computer).
Response = Any


def program_name(path: str) -> str:
    return path.replace("\\", "/").rsplit("/", 1)[-1]


class FakeComputer:
    def __init__(self, *on_path: str) -> None:
        self.on_path: dict[str, str] = {name: f"/usr/bin/{name}" for name in on_path}
        self.in_dirs: dict[tuple[str, str], str] = {}  # (folder, name) -> path
        self.responses: dict[tuple[str, ...], Response] = {}
        self.real_paths: dict[str, str] = {}
        self.answers: list[str] = []  # what the user will type, in order
        self.questions: list[str] = []
        self.calls: list[tuple[tuple[str, ...], dict[str, Any]]] = []

    def program(self, name: str, args: tuple[str, ...], answer: Response) -> None:
        """Put `name` on PATH; its version command `args` answers `answer`."""
        self.on_path[name] = f"/usr/bin/{name}"
        self.responses[(name, *args)] = answer

    def which(self, name: str, path: str | None = None) -> str | None:
        if path is None:
            return self.on_path.get(name)
        for folder in path.split(os.pathsep):
            if (folder, name) in self.in_dirs:
                return self.in_dirs[(folder, name)]
        return None

    def run(self, args: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        key = (program_name(args[0]), *args[1:])
        self.calls.append((key, kwargs))
        response = self.responses[key]
        if callable(response):
            response = response()
        if isinstance(response, BaseException):
            raise response
        if isinstance(response, dict):
            return subprocess.CompletedProcess(args, response["exit_code"], response["stdout"], response["stderr"])
        stdout, returncode = response
        return subprocess.CompletedProcess(args, returncode, stdout, "")

    def ask(self, question: str) -> str:
        self.questions.append(question)
        if not self.answers:
            raise EOFError
        return self.answers.pop(0)

    def installs_run(self) -> list[tuple[str, ...]]:
        """Every install command that ran (sudo apt-get install / winget install)."""
        return [key for key, _ in self.calls if key[0] == "sudo" or key[:2] == ("winget", "install")]


def machine(computer: FakeComputer, tmp_path: Path, info: PlatformInfo = UBUNTU,
            read_windows_path: Callable[[], str] = lambda: "") -> Machine:
    return make_machine(
        info,
        env={"PATH": "/usr/bin"},
        home="/home/me",
        which=computer.which,
        runner=computer.run,
        resolve=lambda path: computer.real_paths.get(path, path),
        ask=computer.ask,
        log_path=tmp_path / "actions.log",
        read_windows_path=read_windows_path,
    )


def log_entries(tmp_path: Path) -> list[dict[str, Any]]:
    path = tmp_path / "actions.log"
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def apt_install(*packages: str) -> tuple[str, ...]:
    return ("sudo", "apt-get", "install", "-y", *packages)


def apt_simulate(*packages: str) -> tuple[str, ...]:
    return ("apt-get", "install", "-s", *packages)


# ---------------------------------------------------------------- the plan


def test_missing_plans_required_tools_not_found_or_too_old(tmp_path: Path) -> None:
    computer = FakeComputer("apt-get")
    computer.program("kicad-cli", ("version",), ("6.0.11\n", 0))  # minimum is 7
    plan = build_plan(MANIFEST, machine(computer, tmp_path), AptBackend())

    # Only required tools; optional ones (ghdl, verilator, iverilog) are left out.
    assert [(item.tool.key, item.status, item.action) for item in plan] == [
        ("ngspice", Status.NOT_FOUND, Action.INSTALL),
        ("kicad", Status.TOO_OLD, Action.INSTALL),
    ]
    assert plan[0].command is not None and plan[0].command.command == apt_install("ngspice")


def test_missing_skips_required_tools_that_work(tmp_path: Path) -> None:
    # The real Ubuntu 24.04 machine: ngspice 42 and KiCad 7.0.11 are below the
    # recommended versions but not below the minimum.
    computer = FakeComputer("apt-get")
    computer.program("ngspice", ("--version",), load("ngspice-version"))
    computer.program("kicad-cli", ("version",), load("kicad-cli-version"))
    plan = build_plan(MANIFEST, machine(computer, tmp_path), AptBackend())
    assert [(item.tool.key, item.status, item.action) for item in plan] == [
        ("ngspice", Status.BELOW_RECOMMENDED, Action.SKIP),
        ("kicad", Status.BELOW_RECOMMENDED, Action.SKIP),
    ]


def test_named_tool_that_is_ok_is_skipped(tmp_path: Path) -> None:
    computer = FakeComputer("apt-get")
    computer.program("iverilog", ("-V",), load("iverilog-version"))
    (item,) = build_plan(MANIFEST, machine(computer, tmp_path), AptBackend(), tool_key="iverilog")
    assert item.action is Action.SKIP
    assert item.reason == "already installed: 12.0 at /usr/bin/iverilog"


def test_nothing_to_install_says_so(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    # Found on the README walk-through: with only "skip" lines, the output
    # used to end at the plan, so it was unclear whether anything happened.
    computer = FakeComputer("apt-get")
    computer.program("iverilog", ("-V",), load("iverilog-version"))
    assert install(MANIFEST, machine(computer, tmp_path), tool_key="iverilog") == 0
    assert capsys.readouterr().out.rstrip().endswith("Nothing to install.")
    assert computer.questions == []


def test_named_optional_tool_that_is_missing_is_planned(tmp_path: Path) -> None:
    computer = FakeComputer("apt-get")
    (item,) = build_plan(MANIFEST, machine(computer, tmp_path), AptBackend(), tool_key="iverilog")
    assert item.action is Action.INSTALL
    assert item.command is not None and item.command.command == apt_install("iverilog")


def test_winget_plan_on_windows(tmp_path: Path) -> None:
    computer = FakeComputer("winget")
    (item,) = build_plan(MANIFEST, machine(computer, tmp_path, WINDOWS), WingetBackend(), tool_key="kicad")
    assert item.action is Action.INSTALL
    assert item.command is not None and item.command.shown() == (
        "winget install --id KiCad.KiCad --exact --version 9.0.3 "
        "--accept-package-agreements --accept-source-agreements"
    )


def test_format_plan_shows_tool_backend_and_exact_command(tmp_path: Path) -> None:
    computer = FakeComputer("apt-get")
    computer.program("kicad-cli", ("version",), load("kicad-cli-version"))
    plan = build_plan(MANIFEST, machine(computer, tmp_path), AptBackend())
    text = format_plan(UBUNTU, AptBackend(), plan)
    assert "Package manager: apt (apt-get)" in text
    assert "Ngspice *  NOT_FOUND          install  apt      sudo apt-get install -y ngspice" in text
    assert "KiCad *    BELOW_RECOMMENDED  skip     -        installed (7.0.11)" in text
    assert "1 to install, 0 manual, 0 refused, 1 skipped." in text


# ----------------------------------------------------------------- refusals


def test_no_package_for_this_platform_shows_the_manual_fix(tmp_path: Path) -> None:
    computer = FakeComputer("winget")
    (item,) = build_plan(MANIFEST, machine(computer, tmp_path, WINDOWS), WingetBackend(), tool_key="ngspice")
    assert item.action is Action.MANUAL
    assert MANIFEST.tools["ngspice"].winget is not None
    assert item.reason == MANIFEST.tools["ngspice"].winget.note


def test_missing_package_manager_is_refused(tmp_path: Path) -> None:
    computer = FakeComputer()  # no apt-get on PATH
    (item,) = build_plan(MANIFEST, machine(computer, tmp_path), AptBackend(), tool_key="iverilog")
    assert item.action is Action.REFUSE
    assert item.reason == "apt-get was not found on PATH"


def test_unsupported_system_needs_a_manual_install(tmp_path: Path) -> None:
    computer = FakeComputer()
    darwin = PlatformInfo(system="darwin")
    (item,) = build_plan(MANIFEST, machine(computer, tmp_path, darwin), None, tool_key="iverilog")
    assert item.action is Action.MANUAL
    assert "no package manager for darwin" in item.reason


def test_refuses_apt_over_esims_own_ngspice(tmp_path: Path) -> None:
    computer = FakeComputer("apt-get")
    computer.program("ngspice", ("--version",), ("** ngspice-41 : Circuit level simulation program\n", 0))
    computer.real_paths["/usr/bin/ngspice"] = "/home/me/nghdl-simulator/install_dir/bin/ngspice"
    (item,) = build_plan(MANIFEST, machine(computer, tmp_path), AptBackend(), tool_key="ngspice")
    assert item.status is Status.TOO_OLD
    assert item.action is Action.REFUSE
    assert "eSim's own build of Ngspice is installed" in item.reason
    assert "would replace it" in item.reason
    assert "Run eSim's Ubuntu installer" in item.reason


def test_refuses_when_apt_would_pull_in_a_conflict(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    computer = FakeComputer("apt-get")
    computer.responses[apt_simulate("ghdl-llvm")] = (
        "Inst ghdl-common (4.1.0+dfsg-0ubuntu2.1 Ubuntu:24.04/noble [amd64])\n"
        "Inst ghdl-mcode (4.1.0+dfsg-0ubuntu2.1 Ubuntu:24.04/noble [amd64])\n"
        "Inst ghdl-llvm (4.1.0+dfsg-0ubuntu2.1 Ubuntu:24.04/noble [amd64])\n",
        0,
    )
    exit_code = install(MANIFEST, machine(computer, tmp_path), tool_key="ghdl", yes=True)

    assert exit_code == 1
    assert computer.installs_run() == []  # even with --yes
    assert "it would also install ghdl-mcode, which conflicts with GHDL" in capsys.readouterr().out


def test_refuses_when_the_apt_simulation_fails(tmp_path: Path) -> None:
    computer = FakeComputer("apt-get")
    computer.responses[apt_simulate("ghdl-llvm")] = ("E: Unable to locate package ghdl-llvm\n", 100)
    (item,) = build_plan(MANIFEST, machine(computer, tmp_path), AptBackend(), tool_key="ghdl")
    assert item.action is Action.REFUSE
    assert "could not check what apt would install" in item.reason
    assert "Unable to locate package" in item.reason


def test_unknown_tool_is_an_error(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert install(MANIFEST, machine(FakeComputer(), tmp_path), tool_key="nope") == 2
    assert "unknown tool 'nope'" in capsys.readouterr().err


# ------------------------------------------------- dry run and confirmation


def iverilog_installs(computer: FakeComputer) -> None:
    """Running `sudo apt-get install -y iverilog` makes iverilog appear."""

    def run_install() -> tuple[str, int]:
        computer.program("iverilog", ("-V",), load("iverilog-version"))
        return ("", 0)

    computer.responses[apt_install("iverilog")] = run_install


def test_dry_run_runs_nothing_and_logs_nothing(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    computer = FakeComputer("apt-get")
    iverilog_installs(computer)
    exit_code = install(MANIFEST, machine(computer, tmp_path), tool_key="iverilog", dry_run=True)

    assert exit_code == 0
    assert computer.installs_run() == []
    assert computer.questions == []
    assert not (tmp_path / "actions.log").exists()
    out = capsys.readouterr().out
    assert "sudo apt-get install -y iverilog" in out
    assert "Dry run: nothing was installed." in out


def test_answering_no_runs_nothing(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    computer = FakeComputer("apt-get")
    iverilog_installs(computer)
    computer.answers = ["n"]
    exit_code = install(MANIFEST, machine(computer, tmp_path), tool_key="iverilog")

    assert exit_code == 1
    assert computer.questions == ["\nRun the 1 install command above? [y/N] "]
    assert computer.installs_run() == []
    assert "Cancelled: nothing was installed." in capsys.readouterr().out
    assert log_entries(tmp_path) == []


@pytest.mark.parametrize("answer", ["", "maybe", "no"])
def test_anything_but_yes_means_no(tmp_path: Path, answer: str) -> None:
    computer = FakeComputer("apt-get")
    iverilog_installs(computer)
    computer.answers = [answer]
    assert install(MANIFEST, machine(computer, tmp_path), tool_key="iverilog") == 1
    assert computer.installs_run() == []


def test_no_answer_at_all_means_no(tmp_path: Path) -> None:
    computer = FakeComputer("apt-get")
    iverilog_installs(computer)  # computer.answers is empty: input() hits end of input
    assert install(MANIFEST, machine(computer, tmp_path), tool_key="iverilog") == 1
    assert computer.installs_run() == []


def test_yes_flag_skips_the_question(tmp_path: Path) -> None:
    computer = FakeComputer("apt-get")
    iverilog_installs(computer)
    assert install(MANIFEST, machine(computer, tmp_path), tool_key="iverilog", yes=True) == 0
    assert computer.questions == []
    assert computer.installs_run() == [apt_install("iverilog")]


# ------------------------------------------------------ running and verifying


def test_successful_install_is_verified_and_logged(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    computer = FakeComputer("apt-get")
    iverilog_installs(computer)
    computer.answers = ["y"]
    exit_code = install(MANIFEST, machine(computer, tmp_path), tool_key="iverilog")

    assert exit_code == 0
    (entry,) = log_entries(tmp_path)
    assert entry["tool"] == "iverilog"
    assert entry["action"] == "install"
    assert entry["backend"] == "apt"
    assert entry["command"] == ["sudo", "apt-get", "install", "-y", "iverilog"]
    assert entry["result"] == "ok"
    assert entry["exit_code"] == 0
    assert entry["verification"] == "passed"
    assert entry["details"][0] == "Icarus Verilog 12.0 found at /usr/bin/iverilog."
    # The doctor's warning (apt's iverilog has no libvvp) is kept in the log.
    assert any("not eSim's own build" in line for line in entry["details"][1:])
    assert "Verification passed" in capsys.readouterr().out


def test_failed_install_command_is_logged_and_not_verified(tmp_path: Path) -> None:
    computer = FakeComputer("apt-get")
    computer.responses[apt_install("iverilog")] = ("", 100)
    assert install(MANIFEST, machine(computer, tmp_path), tool_key="iverilog", yes=True) == 1
    (entry,) = log_entries(tmp_path)
    assert (entry["result"], entry["exit_code"], entry["verification"]) == ("failed", 100, "skipped")


def test_install_that_leaves_the_tool_missing_fails_verification(tmp_path: Path) -> None:
    computer = FakeComputer("apt-get")
    computer.responses[apt_install("iverilog")] = ("", 0)  # "succeeds", but nothing appears
    assert install(MANIFEST, machine(computer, tmp_path), tool_key="iverilog", yes=True) == 1
    (entry,) = log_entries(tmp_path)
    assert entry["result"] == "ok" and entry["verification"] == "failed"
    assert "still not found" in entry["details"][0]


def test_interrupted_install_is_logged(tmp_path: Path) -> None:
    computer = FakeComputer("apt-get")
    computer.responses[apt_install("iverilog")] = KeyboardInterrupt()
    with pytest.raises(KeyboardInterrupt):
        install(MANIFEST, machine(computer, tmp_path), tool_key="iverilog", yes=True)
    (entry,) = log_entries(tmp_path)
    assert entry["result"] == "interrupted"


# --------------------------------------------- GHDL: verification + fallback


def ghdl_computer() -> FakeComputer:
    """GHDL not installed. apt's simulations bring in no conflicting package.
    Installing ghdl-llvm gives the real Ubuntu 24.04 result: ghdl runs, but
    cannot compile. Installing ghdl-gcc makes compiling work."""
    computer = FakeComputer("apt-get")
    computer.responses[apt_simulate("ghdl-llvm")] = ("Inst ghdl-common (4.1.0)\nInst ghdl-llvm (4.1.0)\n", 0)
    computer.responses[apt_simulate("ghdl-gcc")] = ("Inst ghdl-gcc (4.1.0)\n", 0)

    def install_llvm() -> tuple[str, int]:
        computer.program("ghdl", ("--version",), load("ghdl-version"))
        computer.responses[("ghdl", "-a", "t.vhdl")] = load("ghdl-analyze")  # exit 3, libLLVM error
        return ("", 0)

    def install_gcc() -> tuple[str, int]:
        computer.responses[("ghdl", "-a", "t.vhdl")] = ("", 0)
        computer.responses[("ghdl", "-e", "t")] = ("", 0)
        return ("", 0)

    computer.responses[apt_install("ghdl-llvm")] = install_llvm
    computer.responses[apt_install("ghdl-gcc")] = install_gcc
    return computer


def test_failed_verification_offers_the_fallback(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    computer = ghdl_computer()
    computer.answers = ["y", "y"]  # run the plan; then run the fallback
    exit_code = install(MANIFEST, machine(computer, tmp_path), tool_key="ghdl")

    assert exit_code == 0
    assert computer.installs_run() == [apt_install("ghdl-llvm"), apt_install("ghdl-gcc")]
    assert computer.questions[1] == "Run the fallback? [y/N] "
    first, second = log_entries(tmp_path)
    assert (first["action"], first["verification"]) == ("install", "failed")
    assert any("cannot compile VHDL" in line for line in first["details"])
    assert (second["action"], second["verification"]) == ("install-fallback", "passed")
    assert second["command"] == ["sudo", "apt-get", "install", "-y", "ghdl-gcc"]
    out = capsys.readouterr().out
    assert "The manifest has a fallback for GHDL: sudo apt-get install -y ghdl-gcc" in out


def test_declined_fallback_is_not_run(tmp_path: Path) -> None:
    computer = ghdl_computer()
    computer.answers = ["y", "n"]
    assert install(MANIFEST, machine(computer, tmp_path), tool_key="ghdl") == 1
    assert computer.installs_run() == [apt_install("ghdl-llvm")]
    assert len(log_entries(tmp_path)) == 1


def test_fallback_that_would_pull_in_a_conflict_is_refused(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    computer = ghdl_computer()
    computer.responses[apt_simulate("ghdl-gcc")] = ("Inst ghdl-mcode (4.1.0)\nInst ghdl-gcc (4.1.0)\n", 0)
    assert install(MANIFEST, machine(computer, tmp_path), tool_key="ghdl", yes=True) == 1
    assert computer.installs_run() == [apt_install("ghdl-llvm")]
    assert "Not running the fallback: it would also install ghdl-mcode" in capsys.readouterr().out


def test_no_fallback_in_the_manifest_means_no_second_question(tmp_path: Path) -> None:
    computer = FakeComputer("apt-get")
    computer.responses[apt_install("iverilog")] = ("", 0)  # nothing appears: verification fails
    computer.answers = ["y"]
    assert install(MANIFEST, machine(computer, tmp_path), tool_key="iverilog") == 1
    assert len(computer.questions) == 1


# ------------------------------------------------- Windows: PATH from registry


def windows_iverilog_computer() -> FakeComputer:
    """winget installs Icarus into a folder that only the PATH stored in the
    registry knows about, not the PATH of the running process."""
    computer = FakeComputer("winget")
    winget = ("winget", "install", "--id", "Icarus.Verilog", "--exact",
              "--accept-package-agreements", "--accept-source-agreements")

    def run_install() -> tuple[str, int]:
        computer.in_dirs[("fresh-bin", "iverilog")] = "fresh-bin/iverilog.exe"
        computer.responses[("iverilog.exe", "-V")] = load("iverilog-version")
        return ("", 0)

    computer.responses[winget] = run_install
    return computer


def test_windows_verification_reads_the_path_stored_in_the_registry(tmp_path: Path) -> None:
    computer = windows_iverilog_computer()
    found = machine(computer, tmp_path, WINDOWS, read_windows_path=lambda: "fresh-bin")
    assert install(MANIFEST, found, tool_key="iverilog", yes=True) == 0
    (entry,) = log_entries(tmp_path)
    assert entry["verification"] == "passed"
    assert entry["details"][0] == "Icarus Verilog 12.0 found at fresh-bin/iverilog.exe."


def test_without_the_stored_path_the_new_program_is_not_seen(tmp_path: Path) -> None:
    # Shows why the registry is read: this process's PATH does not change.
    computer = windows_iverilog_computer()
    stale = machine(computer, tmp_path, WINDOWS, read_windows_path=lambda: "")
    assert install(MANIFEST, stale, tool_key="iverilog", yes=True) == 1
