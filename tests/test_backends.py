"""Tests for esim_toolman.backends (apt and winget). subprocess.run is faked."""

import subprocess
from typing import Any

import pytest

from esim_toolman.backends import AptBackend, WingetBackend, backend_for
from esim_toolman.detect import Problem
from esim_toolman.manifest import load_manifest

TOOLS = load_manifest().tools


class FakeRunner:
    """Records every call; answers with a canned result or raises."""

    def __init__(self, stdout: str = "", returncode: int = 0, raises: BaseException | None = None) -> None:
        self.stdout, self.returncode, self.raises = stdout, returncode, raises
        self.calls: list[tuple[list[str], dict[str, Any]]] = []

    def __call__(self, args: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        self.calls.append((args, kwargs))
        if self.raises is not None:
            raise self.raises
        return subprocess.CompletedProcess(args, self.returncode, self.stdout, "")


# ----------------------------------------------------------------- choosing


def test_backend_for_each_system() -> None:
    assert isinstance(backend_for("linux"), AptBackend)
    assert isinstance(backend_for("windows"), WingetBackend)
    assert backend_for("darwin") is None


# ---------------------------------------------------------------------- apt


def test_apt_command_comes_from_the_manifest() -> None:
    command = AptBackend().build(TOOLS["kicad"])
    assert command is not None
    assert command.command == (
        "sudo", "apt-get", "install", "-y",
        "kicad", "kicad-footprints", "kicad-libraries", "kicad-symbols", "kicad-templates",
    )
    assert command.packages == ("kicad", "kicad-footprints", "kicad-libraries", "kicad-symbols", "kicad-templates")
    assert command.fallback is False


def test_apt_fallback_command() -> None:
    command = AptBackend().build(TOOLS["ghdl"], use_fallback=True)
    assert command is not None
    assert command.command == ("sudo", "apt-get", "install", "-y", "ghdl-gcc")
    assert command.fallback is True


def test_apt_no_fallback_in_the_manifest() -> None:
    assert AptBackend().build(TOOLS["iverilog"], use_fallback=True) is None


def test_apt_simulation_lists_what_would_be_installed() -> None:
    runner = FakeRunner(
        stdout=(
            "NOTE: This is only a simulation!\n"
            "Inst ghdl-common (4.1.0+dfsg-0ubuntu2.1 Ubuntu:24.04/noble [amd64])\n"
            "Inst ghdl-mcode (4.1.0+dfsg-0ubuntu2.1 Ubuntu:24.04/noble [amd64])\n"
            "Conf ghdl-common (4.1.0+dfsg-0ubuntu2.1 Ubuntu:24.04/noble [amd64])\n"
        )
    )
    command = AptBackend().build(TOOLS["ghdl"])
    assert command is not None
    would_install, error = AptBackend().would_install(command, runner=runner, env={"PATH": "/usr/bin"})

    assert error is None
    assert would_install == frozenset({"ghdl-common", "ghdl-mcode"})
    args, options = runner.calls[0]
    assert args == ["apt-get", "install", "-s", "ghdl-llvm"]  # -s: simulate only
    assert options["env"]["LC_ALL"] == "C"
    assert options.get("shell", False) is False


def test_apt_simulation_failure_is_reported() -> None:
    runner = FakeRunner(stdout="E: Unable to locate package ghdl-llvm\n", returncode=100)
    command = AptBackend().build(TOOLS["ghdl"])
    assert command is not None
    would_install, error = AptBackend().would_install(command, runner=runner)
    assert would_install is None
    assert error is not None and "exited with code 100" in error and "Unable to locate" in error


def test_apt_manual_fix_text() -> None:
    # Shown when a tool has no apt section (every tool in today's manifest has one).
    assert AptBackend().manual_fix(TOOLS["kicad"]) == (
        "The manifest has no apt package for KiCad; install it by hand."
    )


# ------------------------------------------------------------------- winget


def test_winget_command_with_pinned_version() -> None:
    command = WingetBackend().build(TOOLS["kicad"])
    assert command is not None
    assert command.command == (
        "winget", "install", "--id", "KiCad.KiCad", "--exact", "--version", "9.0.3",
        "--accept-package-agreements", "--accept-source-agreements",
    )


def test_winget_command_without_a_version() -> None:
    command = WingetBackend().build(TOOLS["iverilog"])
    assert command is not None
    assert command.command == (
        "winget", "install", "--id", "Icarus.Verilog", "--exact",
        "--accept-package-agreements", "--accept-source-agreements",
    )


@pytest.mark.parametrize("key", ["ngspice", "ghdl", "verilator"])
def test_winget_has_no_command_for_unconfirmed_packages(key: str) -> None:
    # These have available = false in the manifest: no ID is ever invented.
    assert WingetBackend().build(TOOLS[key]) is None
    assert WingetBackend().manual_fix(TOOLS[key]) == TOOLS[key].winget.note  # type: ignore[union-attr]


def test_winget_has_no_fallback() -> None:
    assert WingetBackend().build(TOOLS["kicad"], use_fallback=True) is None


def test_winget_cannot_simulate() -> None:
    command = WingetBackend().build(TOOLS["kicad"])
    assert command is not None
    assert WingetBackend().would_install(command) == (None, None)


# ---------------------------------------------------------------------- run


def test_run_attaches_the_terminal() -> None:
    runner = FakeRunner()
    command = AptBackend().build(TOOLS["iverilog"])
    assert command is not None
    result = AptBackend().run(command, runner=runner)

    args, options = runner.calls[0]
    assert args == ["sudo", "apt-get", "install", "-y", "iverilog"]
    # Output is not captured (the user sees progress and the sudo prompt),
    # there is no shell and no time limit.
    assert options == {"check": False}
    assert result.exit_code == 0 and result.problem is None


def test_run_reports_a_non_zero_exit() -> None:
    command = AptBackend().build(TOOLS["iverilog"])
    assert command is not None
    result = AptBackend().run(command, runner=FakeRunner(returncode=100))
    assert result.problem is Problem.EXIT_STATUS
    assert result.exit_code == 100
    assert result.error is not None and "exited with code 100" in result.error


def test_run_reports_a_program_that_cannot_start() -> None:
    command = WingetBackend().build(TOOLS["kicad"])
    assert command is not None
    result = WingetBackend().run(command, runner=FakeRunner(raises=FileNotFoundError(2, "No such file")))
    assert result.problem is Problem.START_FAILED
    assert result.exit_code is None
