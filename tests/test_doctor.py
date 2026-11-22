"""Tests for esim_toolman.doctor.

FakeMachine stands in for shutil.which, subprocess.run and os.path.realpath,
so nothing here depends on what this computer has installed. Most tests use
a small made-up manifest (tools "sim" and "vhd"); the GHDL tests use the real
GHDL entry from manifest/tools.toml, and real outputs captured on Ubuntu
24.04 (tests/fixtures/ubuntu-24.04/) wherever they exist.
"""

import subprocess
import tomllib
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from captured import UBUNTU_2404, all_runs, load

from esim_toolman.doctor import (
    Finding,
    Severity,
    drop_repeated_notes,
    grouped,
    order_findings,
    run_doctor,
    summarize,
    summary_line,
)
from esim_toolman.manifest import Manifest, load_manifest, parse_manifest
from esim_toolman.platform_info import PlatformInfo, detect_platform

TEST_TOML = r'''
schema_version = 1

[platforms.ubuntu]
supported_versions = ["24.04"]

# Like ngspice: required, has an own_build check and an extra search folder.
[tools.sim]
name = "Sim"
purpose = "simulator"
required = true
needed_for = ["simulation"]
executables = ["sim"]

[tools.sim.search_paths]
linux = ["~/own/install_dir/bin"]
windows = ["%ROOT%/own/bin"]

[tools.sim.version]
args = ["--version"]
pattern = 'sim-(\d+(?:\.\d+)*)'
minimum = "42"
recommended = "45.2"
source = "test"

[tools.sim.install.apt]
packages = ["sim"]
source = "test"

[tools.sim.install.winget]
available = false
note = "Download Sim by hand"
source = "test"

[[tools.sim.checks]]
kind = "own_build"
paths = ["~/own"]
breaks = ["co-sim"]
found_note = "This is eSim's own build"
problem = "This is not eSim's own build"
fix = "Install eSim's own build"
fix_linux = "run the own installer"
source = "test"

# Like GHDL: optional, apt fallback and conflict, winget pinned.
[tools.vhd]
name = "Vhd"
purpose = "VHDL compiler"
required = false
needed_for = ["vhdl-cosim"]
executables = ["vhd"]

[tools.vhd.version]
args = ["--version"]
pattern = 'VHD (\d+(?:\.\d+)+)'
source = "test"

[tools.vhd.install.apt]
packages = ["vhd-llvm"]
fallback = ["vhd-gcc"]
conflicts = ["vhd-mcode"]
source = "test"

[tools.vhd.install.winget]
available = true
id = "Vhd.Vhd"
version = "1.0.0"
source = "test"
'''

MANIFEST = parse_manifest(tomllib.loads(TEST_TOML))
GHDL_ONLY = Manifest(schema_version=1, tools={"ghdl": load_manifest().tools["ghdl"]})

UBUNTU = PlatformInfo(
    system="linux", distro_id="ubuntu", distro_version="24.04",
    distro_name="Ubuntu 24.04 LTS", package_managers=("apt-get",),
)
WINDOWS = PlatformInfo(system="windows", package_managers=("winget",))

def dpkg(package: str) -> tuple[str, ...]:
    """The key of a `dpkg-query` status call in FakeMachine.responses."""
    return ("dpkg-query", "-W", "--showformat=${Status}", package)


def apt_cache(package: str) -> tuple[str, ...]:
    """The key of an `apt-cache policy` call in FakeMachine.responses."""
    return ("apt-cache", "policy", package)


DPKG_GHDL_MCODE = dpkg("ghdl-mcode")
DPKG_VHD_MCODE = dpkg("vhd-mcode")

# Real `ghdl --version` output from Ubuntu 24.04 (llvm backend).
GHDL_LLVM = load("ghdl-version")["stdout"]
# No machine with the mcode backend was available, so this one is made up,
# following GHDL-BACKEND-26.04.md: the backend is named on a later line.
GHDL_MCODE = (
    "GHDL 5.0.1 (Ubuntu 5.0.1+dfsg-1ubuntu1) [Dunoon edition]\n"
    " Compiled with GNAT Version: 14.2.0\n"
    " mcode JIT code generator\n"
)

# A response is (stdout, exit code), a captured real run (a dict from
# tests/fixtures), an exception to raise, or a function that gets the working
# folder and returns one of the first two.
Answer = tuple[str, int] | dict[str, Any]
Response = Answer | BaseException | Callable[[str | None], Answer]


class FakeMachine:
    """A pretend computer: which programs exist and what they print."""

    def __init__(self) -> None:
        self.on_path: dict[str, str] = {}  # name -> path found on PATH
        self.in_dirs: dict[tuple[str, str], str] = {}  # (folder, name) -> path
        self.responses: dict[tuple[str, ...], Response] = {}  # (program, *args) -> response
        self.real_paths: dict[str, str] = {}  # where symlinks really point
        self.calls: list[tuple[tuple[str, ...], dict[str, Any]]] = []

    def install(self, name: str, version_output: str) -> None:
        """Put `name` on PATH, printing version_output for --version."""
        self.on_path[name] = f"/usr/bin/{name}"
        self.responses[(name, "--version")] = (version_output, 0)

    def which(self, name: str, path: str | None = None) -> str | None:
        if path is None:
            return self.on_path.get(name)
        return self.in_dirs.get((path, name))

    def run(self, args: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        key = (program_name(args[0]), *args[1:])
        self.calls.append((key, kwargs))
        response = self.responses[key]
        if isinstance(response, BaseException):
            raise response
        if callable(response):
            response = response(kwargs.get("cwd"))
        if isinstance(response, dict):  # a captured real run
            return subprocess.CompletedProcess(
                args, response["exit_code"], response["stdout"], response["stderr"]
            )
        stdout, returncode = response
        return subprocess.CompletedProcess(args, returncode, stdout, "")

    def resolve(self, path: str) -> str:
        return self.real_paths.get(path, path)

    def ran(self, *key: str) -> bool:
        return any(call_key == key for call_key, _ in self.calls)


def program_name(path: str) -> str:
    return path.replace("\\", "/").rsplit("/", 1)[-1]


def doctor(
    machine: FakeMachine,
    info: PlatformInfo = UBUNTU,
    *,
    manifest: Manifest = MANIFEST,
    home: str = "/home/me",
    env: dict[str, str] | None = None,
    **kwargs: Any,
) -> list[Finding]:
    return run_doctor(
        manifest, info,
        env=env or {}, home=home,
        which=machine.which, runner=machine.run, resolve=machine.resolve,
        **kwargs,
    )


def for_tool(findings: list[Finding], key: str | None, check: str | None = None) -> list[Finding]:
    return [f for f in findings if f.tool == key and (check is None or f.check == check)]


def one(findings: list[Finding], key: str | None, check: str) -> Finding:
    (finding,) = for_tool(findings, key, check)
    return finding


def ghdl_machine(version_output: str) -> FakeMachine:
    """GHDL installed and compiling fine; ghdl-llvm installed and
    ghdl-mcode not (both dpkg answers are real, from Ubuntu 24.04)."""
    machine = FakeMachine()
    machine.install("ghdl", version_output)
    machine.on_path["dpkg-query"] = "/usr/bin/dpkg-query"
    machine.responses[DPKG_GHDL_MCODE] = load("dpkg-query-ghdl-mcode")  # exit 1: unknown
    machine.responses[dpkg("ghdl-llvm")] = load("dpkg-query-ghdl-llvm")  # installed
    machine.responses[("ghdl", "-a", "t.vhdl")] = ("", 0)
    machine.responses[("ghdl", "-e", "t")] = ("", 0)
    return machine


def ubuntu_2404_machine() -> FakeMachine:
    """The WSL Ubuntu 24.04 machine, replayed from its captured outputs:
    every captured program is on PATH and answers exactly as it did there."""
    machine = FakeMachine()
    for run in all_runs():
        program = run["command"][0]
        name = program_name(program)
        machine.on_path[name] = program
        machine.responses[(name, *run["command"][1:])] = run
    return machine


# ------------------------------------------------------------------ platform


def test_platform_ok() -> None:
    finding = one(doctor(FakeMachine()), None, "platform")
    assert finding.severity is Severity.OK
    assert finding.tool_name == "Platform"
    assert "apt-get is available" in finding.problem


def test_untested_ubuntu_is_a_warning() -> None:
    info = PlatformInfo(
        system="linux", distro_id="ubuntu", distro_version="25.10",
        package_managers=("apt-get",),
        warnings=("Ubuntu 25.10 is not in the manifest's tested list (24.04).",),
    )
    finding = one(doctor(FakeMachine(), info), None, "platform")
    assert finding.severity is Severity.WARNING
    assert "25.10" in finding.problem
    assert finding.fix is not None and "Ubuntu 24.04" in finding.fix


def test_linux_without_apt_get_is_a_warning() -> None:
    info = PlatformInfo(system="linux", distro_id="ubuntu", distro_version="24.04")
    finding = one(doctor(FakeMachine(), info), None, "package_manager")
    assert finding.severity is Severity.WARNING
    assert "apt-get was not found" in finding.problem


def test_windows_without_winget_is_a_warning() -> None:
    finding = one(doctor(FakeMachine(), PlatformInfo(system="windows")), None, "package_manager")
    assert finding.severity is Severity.WARNING
    assert finding.fix is not None and "App Installer" in finding.fix


# ---------------------------------------------------------- missing tools


def test_required_tool_missing_is_an_error_with_the_apt_command() -> None:
    finding = one(doctor(FakeMachine()), "sim", "installed")
    assert finding.severity is Severity.ERROR
    assert finding.breaks == ("simulation",)
    assert finding.fix == "sudo apt-get install sim"


def test_optional_tool_missing_is_a_warning_with_the_fallback() -> None:
    finding = one(doctor(FakeMachine()), "vhd", "installed")
    assert finding.severity is Severity.WARNING
    assert "optional" in finding.problem
    assert finding.breaks == ("vhdl-cosim",)
    assert finding.fix is not None
    assert finding.fix.splitlines() == [
        "sudo apt-get install vhd-llvm",
        "If that fails: sudo apt-get install vhd-gcc",
    ]


def test_windows_fix_is_the_winget_command_or_the_manual_note() -> None:
    findings = doctor(FakeMachine(), WINDOWS)
    assert one(findings, "sim", "installed").fix == "Download Sim by hand."
    assert one(findings, "vhd", "installed").fix == "winget install --id Vhd.Vhd --exact --version 1.0.0"


# ----------------------------------------------------------------- versions


@pytest.mark.parametrize(
    ("output", "severity", "text"),
    [
        ("sim-41\n", Severity.ERROR, "older than the minimum 42"),
        ("sim-44\n", Severity.WARNING, "eSim recommends 45.2 or newer"),
        ("sim-45.2\n", Severity.OK, "Sim 45.2 found at /usr/bin/sim"),
        ("Usage: sim [options]\n", Severity.WARNING, "its version could not be read"),
    ],
)
def test_version_findings(output: str, severity: Severity, text: str) -> None:
    machine = FakeMachine()
    machine.install("sim", output)
    finding = one(doctor(machine), "sim", "installed")
    assert finding.severity is severity
    assert text in finding.problem


def test_too_old_fix_upgrades_with_apt() -> None:
    machine = FakeMachine()
    machine.install("sim", "sim-41\n")
    finding = one(doctor(machine), "sim", "installed")
    assert finding.breaks == ("simulation",)
    assert finding.fix == "sudo apt-get update && sudo apt-get install --only-upgrade sim"


def test_own_build_is_updated_with_its_own_installer_never_apt() -> None:
    machine = FakeMachine()
    machine.install("sim", "sim-41\n")
    machine.real_paths["/usr/bin/sim"] = "/home/me/own/install_dir/bin/sim"
    finding = one(doctor(machine), "sim", "installed")
    assert finding.severity is Severity.ERROR
    assert finding.fix is not None and "apt-get" not in finding.fix
    assert "run the own installer" in finding.fix


def test_version_timeout_is_a_warning_and_skips_the_checks() -> None:
    machine = ghdl_machine(GHDL_LLVM)
    machine.responses[("ghdl", "--version")] = subprocess.TimeoutExpired(["ghdl", "--version"], 15)
    findings = doctor(machine, manifest=GHDL_ONLY)

    finding = one(findings, "ghdl", "installed")
    assert finding.severity is Severity.WARNING
    assert "did not finish within 15 seconds" in finding.problem
    assert for_tool(findings, "ghdl", "output_excludes") == []
    assert for_tool(findings, "ghdl", "compile_test") == []
    assert not machine.ran("ghdl", "-a", "t.vhdl")


# ---------------------------------------------------------------- own build


def test_own_build_found_through_a_symlink_is_ok() -> None:
    machine = FakeMachine()
    machine.install("sim", "sim-45.2\n")
    machine.real_paths["/usr/bin/sim"] = "/home/me/own/install_dir/bin/sim"
    finding = one(doctor(machine), "sim", "own_build")
    assert finding.severity is Severity.OK
    assert "eSim's own build" in finding.problem
    assert "/home/me/own/install_dir/bin/sim" in finding.problem


def test_other_build_is_a_warning_naming_what_it_breaks() -> None:
    machine = FakeMachine()
    machine.install("sim", "sim-45.2\n")
    finding = one(doctor(machine), "sim", "own_build")
    assert finding.severity is Severity.WARNING
    assert "Detected: /usr/bin/sim" in finding.problem
    assert finding.breaks == ("co-sim",)
    assert finding.fix is not None
    assert finding.fix.splitlines() == ["Install eSim's own build.", "run the own installer"]


# --------------------------------------------------------------------- PATH


def test_found_only_in_an_extra_folder_warns_about_path(tmp_path: Path) -> None:
    (tmp_path / "own" / "install_dir" / "bin").mkdir(parents=True)
    folder = f"{tmp_path}/own/install_dir/bin"  # the text glob returns for "~/own/install_dir/bin"
    machine = FakeMachine()
    machine.in_dirs[(folder, "sim")] = f"{folder}/sim"
    machine.responses[("sim", "--version")] = ("sim-45.2\n", 0)

    finding = one(doctor(machine, home=str(tmp_path)), "sim", "path")

    assert finding.severity is Severity.WARNING
    assert folder in finding.problem and "not on PATH" in finding.problem
    assert finding.fix is not None
    assert f"export PATH=\"{folder}:$PATH\"" in finding.fix


def test_found_in_an_extra_folder_and_on_path_is_fine(tmp_path: Path) -> None:
    (tmp_path / "own" / "install_dir" / "bin").mkdir(parents=True)
    folder = f"{tmp_path}/own/install_dir/bin"
    machine = FakeMachine()
    machine.in_dirs[(folder, "sim")] = f"{folder}/sim"
    machine.on_path["sim"] = "/usr/bin/sim"
    machine.responses[("sim", "--version")] = ("sim-45.2\n", 0)

    assert for_tool(doctor(machine, home=str(tmp_path)), "sim", "path") == []


def test_windows_path_fix_uses_powershell(tmp_path: Path) -> None:
    (tmp_path / "own" / "bin").mkdir(parents=True)
    folder = f"{tmp_path}/own/bin"
    machine = FakeMachine()
    machine.in_dirs[(folder, "sim")] = f"{folder}/sim.exe"
    machine.responses[("sim.exe", "--version")] = ("sim-45.2\n", 0)

    finding = one(doctor(machine, WINDOWS, env={"ROOT": str(tmp_path)}), "sim", "path")

    assert finding.fix is not None
    assert "SetEnvironmentVariable" in finding.fix and folder in finding.fix


# ------------------------------------------------- GHDL: output_excludes


def test_ghdl_mcode_backend_is_an_error() -> None:
    findings = doctor(ghdl_machine(GHDL_MCODE), manifest=GHDL_ONLY)
    finding = one(findings, "ghdl", "output_excludes")
    assert finding.severity is Severity.ERROR
    assert "mcode backend" in finding.problem
    assert finding.breaks == ("vhdl-cosim",)
    assert finding.fix is not None
    assert finding.fix.splitlines() == [
        "Install ghdl-llvm or ghdl-gcc and remove ghdl-mcode.",
        "sudo apt-get purge ghdl-mcode && sudo apt-get install ghdl-llvm",
    ]


def test_ghdl_mcode_fix_on_windows_points_to_esims_hdl_toolchain() -> None:
    findings = doctor(ghdl_machine(GHDL_MCODE), WINDOWS, manifest=GHDL_ONLY)
    finding = one(findings, "ghdl", "output_excludes")
    assert finding.fix is not None and "HDL toolchain" in finding.fix


def test_ghdl_with_a_working_llvm_backend_passes_every_check() -> None:
    findings = doctor(ghdl_machine(GHDL_LLVM), manifest=GHDL_ONLY)
    assert one(findings, "ghdl", "installed").severity is Severity.OK
    assert one(findings, "ghdl", "output_excludes").severity is Severity.OK
    assert one(findings, "ghdl", "compile_test").severity is Severity.OK
    assert one(findings, "ghdl", "apt_conflict").severity is Severity.OK


# ---------------------------------------------------- GHDL: compile_test


def test_ghdl_installs_but_cannot_compile() -> None:
    # Ubuntu 24.04's ghdl-llvm: --version works, compiling fails. The failure
    # is the real one captured there (exit code 3, message on stderr).
    machine = ghdl_machine(GHDL_LLVM)
    seen: dict[str, str] = {}

    def broken_backend(cwd: str | None) -> dict[str, Any]:
        assert cwd is not None
        seen["cwd"] = cwd
        seen["file"] = (Path(cwd) / "t.vhdl").read_text(encoding="utf-8")
        return load("ghdl-analyze")

    machine.responses[("ghdl", "-a", "t.vhdl")] = broken_backend
    findings = doctor(machine, manifest=GHDL_ONLY, compile_timeout=9)

    finding = one(findings, "ghdl", "compile_test")
    assert finding.severity is Severity.ERROR
    assert "cannot compile VHDL" in finding.problem
    assert "`ghdl -a t.vhdl` exited with code 3" in finding.problem
    assert "libLLVM-18.so.18.1" in finding.problem  # an excerpt of stderr
    assert finding.breaks == ("vhdl-cosim",)
    assert finding.fix is not None
    assert finding.fix.splitlines() == [
        "Switch to the gcc backend: install ghdl-gcc. On Ubuntu, also unset GHDL_BACKEND if it is set to llvm.",
        "sudo apt-get install ghdl-gcc",
    ]
    # The short reason `install` shows in its plan: the command and the
    # first line of its output.
    assert finding.cause is not None
    assert finding.cause.startswith("`ghdl -a t.vhdl` exited with code 3: ")
    assert "libLLVM-18.so.18.1: cannot open shared object file" in finding.cause
    assert finding.repair == "fallback"
    # The test file was written into the temporary folder ...
    assert seen["file"] == "entity t is end t;\narchitecture a of t is begin end a;\n"
    # ... which is gone afterwards.
    assert not Path(seen["cwd"]).exists()
    # It stops at the first failing command, with the compile time limit.
    assert not machine.ran("ghdl", "-e", "t")
    compile_options = [options for key, options in machine.calls if key == ("ghdl", "-a", "t.vhdl")]
    assert compile_options[0]["timeout"] == 9


def test_compile_test_runs_every_command_in_one_temp_folder() -> None:
    machine = ghdl_machine(GHDL_LLVM)
    folders: list[str | None] = []

    def record(cwd: str | None) -> tuple[str, int]:
        folders.append(cwd)
        return ("", 0)

    machine.responses[("ghdl", "-a", "t.vhdl")] = record
    machine.responses[("ghdl", "-e", "t")] = record
    doctor(machine, manifest=GHDL_ONLY)

    assert len(folders) == 2 and folders[0] == folders[1]
    assert folders[0] is not None and not Path(folders[0]).exists()


def test_compile_test_timeout_is_an_error() -> None:
    machine = ghdl_machine(GHDL_LLVM)
    machine.responses[("ghdl", "-a", "t.vhdl")] = subprocess.TimeoutExpired(["ghdl"], 60)
    finding = one(doctor(machine, manifest=GHDL_ONLY), "ghdl", "compile_test")
    assert finding.severity is Severity.ERROR
    assert "did not finish within 60 seconds" in finding.problem


# ------------------------------------------------------------ apt conflicts


def test_conflicting_package_installed_is_an_error() -> None:
    machine = ghdl_machine(GHDL_LLVM)
    machine.responses[DPKG_GHDL_MCODE] = ("install ok installed", 0)
    finding = one(doctor(machine, manifest=GHDL_ONLY), "ghdl", "apt_conflict")
    assert finding.severity is Severity.ERROR
    assert "ghdl-mcode is installed" in finding.problem
    assert finding.fix is not None
    command, note = finding.fix.splitlines()
    assert command == "sudo apt-get purge ghdl-mcode"
    assert note.startswith("Note: Never install the 'ghdl' meta-package")


@pytest.mark.parametrize(
    ("stdout", "exit_code"),
    [
        ("deinstall ok config-files", 0),  # removed, only settings left
        ("", 1),  # dpkg-query has never heard of it
    ],
)
def test_conflicting_package_not_installed_is_ok(stdout: str, exit_code: int) -> None:
    machine = ghdl_machine(GHDL_LLVM)
    machine.responses[DPKG_GHDL_MCODE] = (stdout, exit_code)
    finding = one(doctor(machine, manifest=GHDL_ONLY), "ghdl", "apt_conflict")
    assert finding.severity is Severity.OK


def test_conflicts_are_checked_even_when_the_tool_is_missing() -> None:
    machine = FakeMachine()
    machine.on_path["dpkg-query"] = "/usr/bin/dpkg-query"
    machine.responses[DPKG_VHD_MCODE] = ("install ok installed", 0)
    finding = one(doctor(machine), "vhd", "apt_conflict")
    assert finding.severity is Severity.ERROR


def test_without_dpkg_query_conflicts_are_a_warning() -> None:
    finding = one(doctor(FakeMachine()), "vhd", "apt_conflict")
    assert finding.severity is Severity.WARNING
    assert "dpkg-query was not found" in finding.problem


def test_no_apt_checks_on_windows() -> None:
    machine = ghdl_machine(GHDL_LLVM)
    findings = doctor(machine, WINDOWS, manifest=GHDL_ONLY)
    assert for_tool(findings, "ghdl", "apt_conflict") == []
    assert for_tool(findings, "ghdl", "apt_packages") == []
    assert not any(key[0] == "dpkg-query" for key, _ in machine.calls)


# ------------------------------------------------------------- apt packages


def vhd_machine(installed: set[str]) -> FakeMachine:
    """vhd 1.0.0 on PATH; dpkg says exactly the packages in ``installed`` are
    installed (the others answer "unknown ok not-installed", as on Ubuntu)."""
    machine = FakeMachine()
    machine.install("vhd", "VHD 1.0.0\n")
    machine.on_path["dpkg-query"] = "/usr/bin/dpkg-query"
    for package in ("vhd-llvm", "vhd-gcc", "vhd-mcode"):
        status = "install ok installed" if package in installed else "unknown ok not-installed"
        machine.responses[dpkg(package)] = (status, 0)
    return machine


@pytest.mark.parametrize(
    ("installed", "severity"),
    [
        ({"vhd-llvm"}, Severity.OK),  # the main packages
        ({"vhd-gcc"}, Severity.OK),  # the fallback instead
        (set(), Severity.WARNING),  # neither
    ],
)
def test_apt_packages(installed: set[str], severity: Severity) -> None:
    finding = one(doctor(vhd_machine(installed)), "vhd", "apt_packages")
    assert finding.severity is severity
    if severity is Severity.WARNING:
        assert "not installed: vhd-llvm" in finding.problem
        assert finding.fix == "sudo apt-get install vhd-llvm"


def test_apt_packages_skipped_for_esims_own_build() -> None:
    # eSim's own build is not an apt package, so its absence from dpkg is fine.
    machine = FakeMachine()
    machine.install("sim", "sim-45.2\n")
    machine.on_path["dpkg-query"] = "/usr/bin/dpkg-query"
    machine.responses[DPKG_VHD_MCODE] = ("", 1)  # for the (missing) vhd tool's conflict check
    machine.real_paths["/usr/bin/sim"] = "/home/me/own/install_dir/bin/sim"
    findings = doctor(machine)
    assert for_tool(findings, "sim", "apt_packages") == []
    assert not machine.ran(*dpkg("sim"))


# ------------------------------------------------------ apt upgrade advice


def sim_machine_below_recommended(policy: str) -> FakeMachine:
    """sim 44 installed (eSim recommends 45.2); apt-cache policy says ``policy``."""
    machine = FakeMachine()
    machine.install("sim", "sim-44\n")
    machine.on_path["apt-cache"] = "/usr/bin/apt-cache"
    machine.responses[apt_cache("sim")] = (policy, 0)
    return machine


def test_upgrade_fix_says_when_apt_cannot_reach_the_version() -> None:
    machine = sim_machine_below_recommended("sim:\n  Installed: 44-1\n  Candidate: 44-1\n")
    finding = one(doctor(machine), "sim", "installed")
    assert finding.fix == "apt cannot reach 45.2: the newest sim it offers is 44 (see `apt-cache policy sim`)."
    # apt-cache ran in English, so "Candidate:" could be found.
    options = next(options for key, options in machine.calls if key == apt_cache("sim"))
    assert options["env"]["LC_ALL"] == "C"


def test_upgrade_fix_uses_apt_when_apt_has_a_new_enough_version() -> None:
    # "1:" is a Debian epoch; it is not part of the upstream version.
    machine = sim_machine_below_recommended("sim:\n  Installed: 44-1\n  Candidate: 1:46.0-1\n")
    finding = one(doctor(machine), "sim", "installed")
    assert finding.fix == "sudo apt-get update && sudo apt-get install --only-upgrade sim"


def test_upgrade_fix_falls_back_to_apt_when_apt_cache_does_not_say() -> None:
    machine = sim_machine_below_recommended("sim:\n  Installed: 44-1\n  Candidate: (none)\n")
    finding = one(doctor(machine), "sim", "installed")
    assert finding.fix == "sudo apt-get update && sudo apt-get install --only-upgrade sim"


# ------------------------------------- the real Ubuntu 24.04 machine, replayed


def test_replay_of_the_real_ubuntu_2404_machine() -> None:
    """Every command the doctor runs answers exactly as it did on the WSL
    Ubuntu 24.04 machine. The bugs this real run exposed stay fixed."""
    manifest = load_manifest()
    info = detect_platform(
        manifest.ubuntu_supported_versions,
        system="Linux",
        os_release_paths=[UBUNTU_2404 / "os-release"],
        which=lambda name: "/usr/bin/apt-get" if name == "apt-get" else None,
    )
    findings = doctor(ubuntu_2404_machine(), info, manifest=manifest)

    # Right the first time: GHDL's llvm backend cannot compile on 24.04.
    compile_test = one(findings, "ghdl", "compile_test")
    assert compile_test.severity is Severity.ERROR
    assert "exited with code 3" in compile_test.problem
    assert "libLLVM-18.so.18.1" in compile_test.problem
    assert one(findings, "ghdl", "output_excludes").severity is Severity.OK

    # Bug 1: apt's iverilog has no libvvp, so d_cosim cannot work.
    icarus = one(findings, "iverilog", "own_build")
    assert icarus.severity is Severity.WARNING
    assert icarus.breaks == ("verilog-cosim",)

    # Bug 2: apt's ngspice has no ivlng either, so d_cosim is broken too.
    ngspice = one(findings, "ngspice", "own_build")
    assert ngspice.breaks == ("vhdl-cosim", "verilog-models", "verilog-cosim")

    # Bug 3: apt has nothing newer, so no useless upgrade command.
    kicad = one(findings, "kicad", "installed")
    assert kicad.severity is Severity.WARNING
    assert kicad.fix is not None
    assert kicad.fix.startswith("apt cannot reach 9: the newest kicad it offers is 7.0.11")
    assert "--only-upgrade" not in kicad.fix
    ngspice_version = one(findings, "ngspice", "installed")
    assert ngspice_version.fix is not None
    assert ngspice_version.fix.startswith("apt cannot reach 45.2: the newest ngspice it offers is 42")

    # Bug 4: no note for developers in what users read.
    assert not any("not written" in f.problem + (f.fix or "") for f in findings)

    # Bug 5: KiCad's library packages were not installed.
    libraries = one(findings, "kicad", "apt_packages")
    assert libraries.severity is Severity.WARNING
    assert "kicad-footprints, kicad-libraries, kicad-symbols, kicad-templates" in libraries.problem
    assert libraries.fix == "sudo apt-get install kicad-footprints kicad-libraries kicad-symbols kicad-templates"

    # Follow-up: KiCad's note was printed twice (once per warning). Now once.
    kicad_text = "\n".join(f.problem + "\n" + (f.fix or "") for f in for_tool(findings, "kicad"))
    assert kicad_text.count("Newer KiCad for Ubuntu comes from the KiCad PPA") == 1

    # Everything else that was fine there.
    for key in ("ngspice", "ghdl", "verilator", "iverilog"):
        assert one(findings, key, "apt_packages").severity is Severity.OK
    assert one(findings, "verilator", "installed").severity is Severity.OK
    assert one(findings, "iverilog", "installed").severity is Severity.OK
    assert one(findings, None, "platform").severity is Severity.OK
    assert summarize(findings)["errors"] == 1
    assert summarize(findings)["warnings"] == 5


# --------------------------------------------------------------- read-only


def test_doctor_never_runs_a_package_manager() -> None:
    machine = ghdl_machine(GHDL_MCODE)
    machine.responses[DPKG_GHDL_MCODE] = ("install ok installed", 0)
    doctor(machine, manifest=GHDL_ONLY)
    programs = {key[0] for key, _ in machine.calls}
    assert programs <= {"ghdl", "dpkg-query"}


# ------------------------------------------------------ ordering and summary


def test_groups_with_errors_come_first() -> None:
    # sim (required) missing: ERROR. vhd (optional) missing: WARNINGs. Platform: OK.
    findings = doctor(FakeMachine())
    assert [key for key, _ in grouped(findings)] == ["sim", "vhd", None]


def test_errors_come_first_inside_a_group() -> None:
    findings = [
        Finding("a", "A", Severity.OK, "x", "fine"),
        Finding("a", "A", Severity.ERROR, "y", "broken"),
        Finding(None, "Platform", Severity.WARNING, "p", "untested"),
        Finding("b", "B", Severity.WARNING, "z", "not ideal"),
    ]
    ordered = order_findings(findings)
    assert [(f.tool, f.severity) for f in ordered] == [
        ("a", Severity.ERROR),
        ("a", Severity.OK),
        (None, Severity.WARNING),  # stable: Platform stays before b
        ("b", Severity.WARNING),
    ]


def test_each_note_is_shown_once_per_tool() -> None:
    findings = [
        Finding("a", "A", Severity.WARNING, "x", "old", fix="upgrade a\nNote: about a."),
        Finding("a", "A", Severity.WARNING, "y", "missing", fix="install a-libs\nNote: about a."),
        Finding("a", "A", Severity.WARNING, "z", "only a note", fix="Note: about a."),
        Finding("b", "B", Severity.WARNING, "x", "old", fix="upgrade b\nNote: about a."),
        Finding("a", "A", Severity.OK, "w", "fine"),
    ]
    fixes = [f.fix for f in drop_repeated_notes(findings)]
    assert fixes == [
        "upgrade a\nNote: about a.",  # first time for tool a: kept
        "install a-libs",  # repeated for a: dropped
        None,  # nothing left
        "upgrade b\nNote: about a.",  # another tool: kept
        None,  # had no fix
    ]


def test_summary_line_counts_and_plurals() -> None:
    findings = [
        Finding("a", "A", Severity.ERROR, "x", "1"),
        Finding("a", "A", Severity.ERROR, "x", "2"),
        Finding("b", "B", Severity.WARNING, "x", "3"),
    ]
    counts = summarize(findings)
    assert counts == {"errors": 2, "warnings": 1, "ok": 0}
    assert summary_line(counts) == "2 errors, 1 warning, 0 OK"


def test_finding_to_dict() -> None:
    finding = Finding("ghdl", "GHDL", Severity.ERROR, "compile_test", "broken", ("vhdl-cosim",), "fix it")
    assert finding.to_dict() == {
        "tool": "ghdl",
        "tool_name": "GHDL",
        "severity": "ERROR",
        "check": "compile_test",
        "problem": "broken",
        "breaks": ["vhdl-cosim"],
        "fix": "fix it",
        "cause": None,
        "repair": None,
    }
