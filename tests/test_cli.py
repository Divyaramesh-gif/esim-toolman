"""Tests for the `status` command (esim_toolman.cli).

A small test manifest lists one required tool (alpha) and one optional tool
(beta). shutil.which, subprocess.run and the platform check are faked, so
the result never depends on what this machine has installed.
"""

import json
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest

from esim_toolman import actionlog, cli
from esim_toolman.platform_info import PlatformInfo

TEST_MANIFEST = r'''
schema_version = 1

[platforms.ubuntu]
supported_versions = ["24.04"]

[tools.alpha]
name = "Alpha"
purpose = "required test tool"
required = true
executables = ["alpha"]

[tools.alpha.version]
args = ["--version"]
pattern = 'alpha (\d+(?:\.\d+)*)'
minimum = "1.0"
recommended = "2.0"
source = "test"

[tools.beta]
name = "Beta"
purpose = "optional test tool"
required = false
executables = ["beta"]

[tools.beta.version]
args = ["-V"]
pattern = 'beta (\d+(?:\.\d+)*)'
source = "test"
'''

UBUNTU = PlatformInfo(
    system="linux",
    distro_id="ubuntu",
    distro_version="24.04",
    distro_name="Ubuntu 24.04 LTS",
    package_managers=("apt-get",),
)


class FakeSystem:
    """The pretend machine: ``programs`` maps a program name to the text its
    version command prints. Programs not in the map are not installed."""

    def __init__(self, manifest: Path) -> None:
        self.manifest = manifest
        self.programs: dict[str, str] = {}
        self.platform = UBUNTU

    def which(self, name: str, path: str | None = None) -> str | None:
        if path is None and name in self.programs:
            return f"/fake/bin/{name}"
        return None

    def run(self, args: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        name = args[0].rsplit("/", 1)[-1]
        return subprocess.CompletedProcess(args, 0, stdout=self.programs[name], stderr="")

    def status(self, *extra: str) -> int:
        return cli.main(["--manifest", str(self.manifest), "status", *extra])

    def doctor(self, *extra: str) -> int:
        return cli.main(["--manifest", str(self.manifest), "doctor", *extra])

    def cli(self, *args: str) -> int:
        return cli.main(["--manifest", str(self.manifest), *args])


@pytest.fixture
def system(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> FakeSystem:
    manifest = tmp_path / "tools.toml"
    manifest.write_text(TEST_MANIFEST, encoding="utf-8")
    fake = FakeSystem(manifest)
    monkeypatch.setattr(shutil, "which", fake.which)
    monkeypatch.setattr(subprocess, "run", fake.run)
    monkeypatch.setattr(cli, "detect_platform", lambda supported: fake.platform)
    return fake


def test_required_ok_optional_missing_exits_0(system: FakeSystem, capsys: pytest.CaptureFixture[str]) -> None:
    system.programs["alpha"] = "alpha 2.1\n"

    assert system.status() == 0

    out = capsys.readouterr().out
    assert "Platform: Linux (Ubuntu 24.04 LTS)" in out
    assert "Package managers: apt-get" in out
    lines = out.splitlines()
    header = next(line for line in lines if line.startswith("TOOL"))
    assert header.split() == ["TOOL", "STATUS", "INSTALLED", "RECOMMENDED", "PATH"]
    alpha = next(line for line in lines if line.startswith("Alpha *"))
    assert alpha.split() == ["Alpha", "*", "OK", "2.1", "2.0", "/fake/bin/alpha"]
    beta = next(line for line in lines if line.startswith("Beta"))
    assert beta.split() == ["Beta", "NOT_FOUND", "-", "-", "-"]
    assert "beta: beta not found on PATH" in out


def test_required_below_recommended_exits_1(system: FakeSystem, capsys: pytest.CaptureFixture[str]) -> None:
    system.programs["alpha"] = "alpha 1.5\n"
    assert system.status() == 1
    out = capsys.readouterr().out
    assert "BELOW_RECOMMENDED" in out
    assert "alpha: version 1.5 works but is older than the recommended 2.0" in out


def test_required_too_old_exits_1(system: FakeSystem, capsys: pytest.CaptureFixture[str]) -> None:
    system.programs["alpha"] = "alpha 0.9\n"
    assert system.status() == 1
    assert "older than the minimum 1.0" in capsys.readouterr().out


def test_required_missing_exits_1(system: FakeSystem) -> None:
    system.programs["beta"] = "beta 1.0\n"
    assert system.status() == 1


def test_unreadable_version_is_unknown(system: FakeSystem, capsys: pytest.CaptureFixture[str]) -> None:
    system.programs["alpha"] = "Usage: alpha [options]\n"
    assert system.status() == 1
    out = capsys.readouterr().out
    assert "UNKNOWN" in out
    assert "the version could not be read" in out


def test_optional_tools_do_not_change_the_exit_code(system: FakeSystem) -> None:
    system.programs["alpha"] = "alpha 2.0\n"
    system.programs["beta"] = "garbage\n"  # UNKNOWN, but beta is optional
    assert system.status() == 0


def test_json_output(system: FakeSystem, capsys: pytest.CaptureFixture[str]) -> None:
    system.programs["alpha"] = "alpha 2.1\n"

    assert system.status("--json") == 0

    report = json.loads(capsys.readouterr().out)
    assert report["platform"]["system"] == "linux"
    assert report["platform"]["package_managers"] == ["apt-get"]
    assert report["warnings"] == []
    assert report["required_tools_ok"] is True
    alpha, beta = report["tools"]
    assert alpha["key"] == "alpha"
    assert alpha["status"] == "OK"
    assert alpha["installed_version"] == "2.1"
    assert alpha["recommended_version"] == "2.0"
    assert alpha["path"] == "/fake/bin/alpha"
    assert alpha["problem"] is None
    assert beta["status"] == "NOT_FOUND"
    assert beta["problem"] == "not_found"


def test_platform_warnings_are_shown(system: FakeSystem, capsys: pytest.CaptureFixture[str]) -> None:
    system.platform = PlatformInfo(
        system="linux",
        distro_id="ubuntu",
        distro_version="25.10",
        warnings=("Ubuntu 25.10 is not in the manifest's tested list (24.04).",),
    )
    system.programs["alpha"] = "alpha 2.0\n"

    assert system.status() == 0  # a warning never changes the exit code
    assert "Warning: Ubuntu 25.10 is not in the manifest's tested list" in capsys.readouterr().out

    system.status("--json")
    report = json.loads(capsys.readouterr().out)
    assert report["warnings"] == ["Ubuntu 25.10 is not in the manifest's tested list (24.04)."]


def test_broken_manifest_exits_2(system: FakeSystem, capsys: pytest.CaptureFixture[str]) -> None:
    system.manifest.write_text("schema_version = 2\n[tools]\n", encoding="utf-8")
    assert system.status() == 2
    err = capsys.readouterr().err
    assert "esim-toolman:" in err and "schema_version" in err


# ------------------------------------------------------------------- doctor


def test_doctor_puts_error_groups_first_and_ends_with_a_summary(
    system: FakeSystem, capsys: pytest.CaptureFixture[str]
) -> None:
    system.programs["beta"] = "beta 1.0\n"  # required alpha is missing

    assert system.doctor() == 1

    lines = capsys.readouterr().out.splitlines()
    assert lines[0] == "Platform: Linux (Ubuntu 24.04 LTS)"
    assert lines[1] == "Read-only: the doctor changed nothing."
    alpha = lines.index("Alpha (required)")
    assert alpha < lines.index("Platform") and alpha < lines.index("Beta")
    assert lines[alpha + 1].startswith("  ERROR    Alpha was not found")
    assert lines[alpha + 2].startswith("           Fix: Install Alpha by hand")
    assert lines[-1] == "1 error, 0 warnings, 2 OK"


def test_doctor_exits_0_when_there_are_only_warnings(system: FakeSystem) -> None:
    system.programs["alpha"] = "alpha 1.5\n"  # below recommended: a WARNING
    assert system.doctor() == 0


def test_doctor_json(system: FakeSystem, capsys: pytest.CaptureFixture[str]) -> None:
    system.programs["alpha"] = "alpha 0.9\n"  # too old: an ERROR

    assert system.doctor("--json") == 1

    report = json.loads(capsys.readouterr().out)
    assert report["platform"]["system"] == "linux"
    assert report["summary"] == {"errors": 1, "warnings": 1, "ok": 1}
    first = report["findings"][0]
    assert first["tool"] == "alpha"
    assert first["severity"] == "ERROR"
    assert first["check"] == "installed"
    assert "older than the minimum 1.0" in first["problem"]


def test_doctor_with_a_broken_manifest_exits_2(system: FakeSystem) -> None:
    system.manifest.write_text("schema_version = 2\n[tools]\n", encoding="utf-8")
    assert system.doctor() == 2


# ------------------------------------------------------------ install and log


def test_install_dry_run_missing(system: FakeSystem, capsys: pytest.CaptureFixture[str]) -> None:
    system.programs["apt-get"] = ""  # apt-get is on PATH (it is never run in a dry run)

    # alpha is required and missing, but the test manifest has no apt package
    # for it: a manual step, so the exit code is 1.
    assert system.cli("install", "--dry-run", "--missing") == 1

    out = capsys.readouterr().out
    assert "Install plan:" in out
    alpha = next(line for line in out.splitlines() if line.startswith("  Alpha"))
    assert alpha.split()[:5] == ["Alpha", "*", "NOT_FOUND", "manual", "-"]
    assert "The manifest has no apt package for Alpha" in alpha
    assert "Beta" not in out  # optional tools are not part of --missing
    assert "Dry run: nothing was installed." in out


@pytest.mark.parametrize("args", [["install"], ["install", "alpha", "--missing"]])
def test_install_needs_exactly_one_target(args: list[str]) -> None:
    with pytest.raises(SystemExit) as exit_info:
        cli.main(args)
    assert exit_info.value.code == 2


def test_install_unknown_tool_exits_2(system: FakeSystem, capsys: pytest.CaptureFixture[str]) -> None:
    assert system.cli("install", "nope", "--dry-run") == 2
    assert "unknown tool 'nope'" in capsys.readouterr().err


def test_log_shows_the_last_actions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    path = tmp_path / "actions.log"
    monkeypatch.setattr(actionlog, "default_log_path", lambda: path)
    for tool in ("a", "b", "c"):
        actionlog.append(
            tool=tool, action="install", backend="apt",
            command=["sudo", "apt-get", "install", "-y", tool],
            result="ok", exit_code=0, verification="passed", path=path,
        )

    assert cli.main(["log", "-n", "2"]) == 0

    out = capsys.readouterr().out
    assert "  b  install (apt)  ok, exit 0, verification passed: sudo apt-get install -y b" in out
    assert "  c  install (apt)" in out
    assert "  a  install" not in out


def test_log_without_any_actions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(actionlog, "default_log_path", lambda: tmp_path / "missing.log")
    assert cli.main(["log"]) == 0
    assert "No actions logged yet" in capsys.readouterr().out


@pytest.mark.parametrize("count", ["0", "-3", "many"])
def test_log_count_must_be_a_positive_number(count: str) -> None:
    with pytest.raises(SystemExit) as exit_info:
        cli.main(["log", "-n", count])
    assert exit_info.value.code == 2


def test_no_command_is_a_usage_error(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exit_info:
        cli.main([])
    assert exit_info.value.code == 2
    assert "COMMAND" in capsys.readouterr().err
