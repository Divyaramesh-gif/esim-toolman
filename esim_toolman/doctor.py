"""Diagnose eSim's external tools and suggest fixes. Read-only.

run_doctor() looks at the platform and at every tool in the manifest and
returns a list of Findings. A Finding is one observation: a severity, the
problem in plain words, the eSim features it breaks, and a fix for this
platform. Nothing here installs or changes anything. The only programs it
starts are the tools themselves (version commands and the manifest's
checks, which work in a temporary folder that is deleted afterwards), and
on Linux dpkg-query and apt-cache, which only read the package lists.

Severity rules:
  ERROR    a required tool is missing, or an installed tool cannot work
           (older than eSim's minimum, a failed check, a conflicting package)
  WARNING  eSim still works, but something is missing or not ideal: an
           optional tool is missing, a version is below the recommended one
           or unreadable, a program is not on PATH, the platform is untested
  OK       checked and fine
"""

import dataclasses
import itertools
import os
import re
import shutil
import subprocess
import tempfile
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from esim_toolman.detect import (
    DEFAULT_TIMEOUT_SECONDS,
    DetectResult,
    Problem,
    Runner,
    Which,
    detect_tool,
    expand_search_path,
    run_command,
    show_command,
)
from esim_toolman.manifest import (
    Check,
    CompileTestCheck,
    Manifest,
    OutputExcludesCheck,
    OwnBuildCheck,
    Tool,
)
from esim_toolman.platform_info import PlatformInfo
from esim_toolman.versions import Status, classify, compare_versions, extract_version

COMPILE_TIMEOUT_SECONDS = 60.0
"""Time limit for each command of a compile_test check."""

PLATFORM = "Platform"
"""The group name for findings about the computer rather than one tool."""

# The package manager each system's install commands need.
_PACKAGE_MANAGER = {"linux": "apt-get", "windows": "winget"}
_PACKAGE_MANAGER_FIX = {
    "apt-get": "Install tools by hand, or use Ubuntu, where apt-get is available.",
    "winget": "Install or update 'App Installer' from the Microsoft Store; it provides winget.",
}


class Severity(StrEnum):
    """How serious a finding is. The order of _RANK is the display order."""

    ERROR = "ERROR"
    WARNING = "WARNING"
    OK = "OK"


_RANK = {Severity.ERROR: 0, Severity.WARNING: 1, Severity.OK: 2}


@dataclass(frozen=True)
class Finding:
    """One observation by the doctor."""

    tool: str | None  # tool key, or None for platform findings
    tool_name: str  # "KiCad", or PLATFORM
    severity: Severity
    check: str  # what produced it: "installed", "path", "apt_conflict", a check kind, ...
    problem: str  # plain words; for OK, what was confirmed
    breaks: tuple[str, ...] = ()  # eSim features that will not work
    fix: str | None = None  # what to do on this platform; lines separated by "\n"
    cause: str | None = None  # for a failed check: the short technical reason
    repair: str | None = None  # for a failed check: the manifest's `repair` ("fallback")

    def to_dict(self) -> dict[str, object]:
        """The finding as plain values, for JSON output."""
        return {
            "tool": self.tool,
            "tool_name": self.tool_name,
            "severity": self.severity.value,
            "check": self.check,
            "problem": self.problem,
            "breaks": list(self.breaks),
            "fix": self.fix,
            "cause": self.cause,
            "repair": self.repair,
        }


@dataclass(frozen=True)
class _Context:
    """Everything the per-tool checks need, bundled so each helper takes one
    argument instead of eight. Tests swap in fakes through run_doctor() and
    check_tool()."""

    info: PlatformInfo
    env: Mapping[str, str]
    home: str
    which: Which
    runner: Runner
    resolve: Callable[[str], str]
    timeout: float
    compile_timeout: float


# --------------------------------------------------------------------------
# Entry points
# --------------------------------------------------------------------------


def run_doctor(
    manifest: Manifest,
    info: PlatformInfo,
    *,
    env: Mapping[str, str] | None = None,
    home: str | None = None,
    which: Which | None = None,
    runner: Runner | None = None,
    resolve: Callable[[str], str] | None = None,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
    compile_timeout: float = COMPILE_TIMEOUT_SECONDS,
) -> list[Finding]:
    """Check the platform and every tool; return the findings, ordered for display.

    The keyword arguments replace the real environment, home folder,
    shutil.which, subprocess.run and os.path.realpath. Tests use them;
    normal callers leave them out.
    """
    ctx = _make_context(info, env, home, which, runner, resolve, timeout, compile_timeout)
    findings = platform_findings(manifest, info)
    for tool in manifest.tools.values():
        findings += tool_findings(tool, ctx)
    return drop_repeated_notes(order_findings(findings))


def check_tool(
    tool: Tool,
    info: PlatformInfo,
    *,
    env: Mapping[str, str] | None = None,
    home: str | None = None,
    which: Which | None = None,
    runner: Runner | None = None,
    resolve: Callable[[str], str] | None = None,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
    compile_timeout: float = COMPILE_TIMEOUT_SECONDS,
) -> list[Finding]:
    """The doctor's findings for one tool only, ordered for display. The
    installer uses this to verify an install. Same keyword arguments as
    run_doctor()."""
    ctx = _make_context(info, env, home, which, runner, resolve, timeout, compile_timeout)
    return drop_repeated_notes(order_findings(tool_findings(tool, ctx)))


def is_own_build(
    tool: Tool,
    path: str,
    *,
    env: Mapping[str, str],
    home: str,
    resolve: Callable[[str], str] = os.path.realpath,
) -> bool:
    """True if the program at ``path`` is eSim's own build, according to
    any own_build check the tool has in the manifest."""
    real_path = resolve(path)
    for check in tool.checks:
        if isinstance(check, OwnBuildCheck):
            folders = [expand_search_path(p, env, home) for p in check.paths]
            if any(_is_inside(real_path, f) for f in folders if f is not None):
                return True
    return False


def _make_context(
    info: PlatformInfo,
    env: Mapping[str, str] | None,
    home: str | None,
    which: Which | None,
    runner: Runner | None,
    resolve: Callable[[str], str] | None,
    timeout: float,
    compile_timeout: float,
) -> _Context:
    """Fill in the real environment, home, which, runner and resolver for
    whatever the caller did not replace."""
    return _Context(
        info=info,
        env=os.environ if env is None else env,
        home=str(Path.home()) if home is None else home,
        which=which or shutil.which,
        runner=runner or subprocess.run,
        resolve=resolve or os.path.realpath,
        timeout=timeout,
        compile_timeout=compile_timeout,
    )


def order_findings(findings: Sequence[Finding]) -> list[Finding]:
    """Group findings by tool, put groups with an ERROR first (then WARNING,
    then all-OK), and inside each group put ERRORs first.

    Python's sort is stable, so equal groups and equal findings keep their
    original order (platform first, then manifest order).
    """
    groups: dict[str | None, list[Finding]] = {}
    for finding in findings:
        groups.setdefault(finding.tool, []).append(finding)
    ordered = sorted(groups.values(), key=lambda group: min(_RANK[f.severity] for f in group))
    return [f for group in ordered for f in sorted(group, key=lambda f: _RANK[f.severity])]


def drop_repeated_notes(findings: Sequence[Finding]) -> list[Finding]:
    """Show each "Note: ..." line once per tool.

    The manifest's notes are added to every fix that uses its apt or winget
    section, so one tool can collect the same note several times (KiCad's
    upgrade fix and its missing-packages fix both did). Going through the
    findings in display order, a note line already shown for the same tool
    is removed from later fixes.
    """
    seen: set[tuple[str | None, str]] = set()
    result: list[Finding] = []
    for finding in findings:
        if finding.fix is not None:
            kept: list[str] = []
            for line in finding.fix.splitlines():
                if line.startswith("Note: "):
                    if (finding.tool, line) in seen:
                        continue
                    seen.add((finding.tool, line))
                kept.append(line)
            finding = dataclasses.replace(finding, fix="\n".join(kept) or None)
        result.append(finding)
    return result


def summarize(findings: Sequence[Finding]) -> dict[str, int]:
    """Count findings per severity."""
    return {
        "errors": sum(f.severity is Severity.ERROR for f in findings),
        "warnings": sum(f.severity is Severity.WARNING for f in findings),
        "ok": sum(f.severity is Severity.OK for f in findings),
    }


def summary_line(counts: Mapping[str, int]) -> str:
    """E.g. "2 errors, 1 warning, 3 OK"."""
    return (
        f"{_plural(counts['errors'], 'error')}, "
        f"{_plural(counts['warnings'], 'warning')}, {counts['ok']} OK"
    )


def grouped(findings: Sequence[Finding]) -> list[tuple[str | None, list[Finding]]]:
    """Ordered findings as (tool key, findings) pairs, for display."""
    return [(key, list(group)) for key, group in itertools.groupby(findings, key=lambda f: f.tool)]


# --------------------------------------------------------------------------
# Platform
# --------------------------------------------------------------------------


def platform_findings(manifest: Manifest, info: PlatformInfo) -> list[Finding]:
    """Warnings from platform_info, plus a missing package manager."""
    findings: list[Finding] = []
    if info.system == "linux":
        tested = ", ".join(manifest.ubuntu_supported_versions) or "none"
        warning_fix = f"For a tested setup use Ubuntu {tested}. Otherwise carry on, but expect differences."
    else:
        warning_fix = "Use Ubuntu Linux or Windows."
    for warning in info.warnings:
        findings.append(
            Finding(None, PLATFORM, Severity.WARNING, "platform", warning, fix=warning_fix)
        )

    manager = _PACKAGE_MANAGER.get(info.system)
    if manager is not None and manager not in info.package_managers:
        findings.append(
            Finding(
                None, PLATFORM, Severity.WARNING, "package_manager",
                f"{manager} was not found on PATH, so the {manager} commands in the fixes "
                "cannot be run here.",
                fix=_PACKAGE_MANAGER_FIX[manager],
            )
        )

    if not findings:
        findings.append(
            Finding(
                None, PLATFORM, Severity.OK, "platform",
                f"{info.describe()}; {manager} is available.",
            )
        )
    return findings


# --------------------------------------------------------------------------
# One tool
# --------------------------------------------------------------------------


def tool_findings(tool: Tool, ctx: _Context) -> list[Finding]:
    """Everything the doctor checks for one tool, in this order: installed
    and version, PATH, the manifest's checks, apt packages, apt conflicts."""
    detection = detect_tool(
        tool, ctx.info.system,
        env=ctx.env, home=ctx.home, which=ctx.which, runner=ctx.runner, timeout=ctx.timeout,
    )
    if not detection.found:
        return [_not_found(tool, detection, ctx.info), *_apt_conflicts(tool, ctx)]

    findings = [_version_finding(tool, detection, ctx)]
    path_finding = _path_finding(tool, detection, ctx)
    if path_finding is not None:
        findings.append(path_finding)
    # If even the version command could not run, the checks would fail the
    # same way, so they are skipped.
    if detection.problem not in (Problem.TIMEOUT, Problem.START_FAILED):
        findings += [_run_check(tool, check, detection.path, ctx) for check in tool.checks]
    findings += _apt_packages(tool, detection.path, ctx)
    findings += _apt_conflicts(tool, ctx)
    return findings


def _not_found(tool: Tool, detection: DetectResult, info: PlatformInfo) -> Finding:
    if tool.required:
        severity, problem = Severity.ERROR, f"{tool.name} was not found ({detection.error})."
    else:
        severity = Severity.WARNING
        problem = (
            f"{tool.name} was not found ({detection.error}). It is optional: only the "
            "features listed below need it."
        )
    return Finding(
        tool.key, tool.name, severity, "installed", problem, tool.needed_for,
        install_fix(tool, info),
    )


def _version_finding(tool: Tool, detection: DetectResult, ctx: _Context) -> Finding:
    """Judge the version, using the same rules as `status`."""
    spec = tool.version
    installed = extract_version(detection.output, spec.pattern)
    status = classify(True, installed, spec.minimum, spec.recommended)
    # If this is eSim's own build, updating it is done with eSim's
    # installer, never with the package manager (it would replace it).
    own_fix = _own_build_fix(tool, detection.path, ctx)

    def finding(severity: Severity, problem: str, breaks: tuple[str, ...] = (), fix: str | None = None) -> Finding:
        return Finding(tool.key, tool.name, severity, "installed", problem, breaks, fix)

    if status is Status.TOO_OLD:
        return finding(
            Severity.ERROR,
            f"{tool.name} {installed} is older than the minimum {spec.minimum} that eSim needs.",
            tool.needed_for, own_fix or _upgrade_fix(tool, spec.minimum, ctx),
        )
    if status is Status.BELOW_RECOMMENDED:
        return finding(
            Severity.WARNING,
            f"{tool.name} {installed} works, but eSim recommends {spec.recommended} or newer.",
            fix=own_fix or _upgrade_fix(tool, spec.recommended, ctx),
        )
    if status is Status.UNKNOWN:
        reason = detection.error or "its output has no version number the manifest pattern recognises"
        return finding(
            Severity.WARNING,
            f"{tool.name} was found at {detection.path}, but its version could not be read: {reason}.",
            fix=(
                f"Run `{show_command(detection.command)}` yourself to see what it prints. "
                f"If it fails, reinstall:\n{own_fix or install_fix(tool, ctx.info)}"
            ),
        )
    return finding(Severity.OK, f"{tool.name} {installed} found at {detection.path}.")


def _path_finding(tool: Tool, detection: DetectResult, ctx: _Context) -> Finding | None:
    """WARNING when the program is only in an extra search folder, not on PATH."""
    folder = detection.found_in_folder
    if folder is None or any(ctx.which(name) for name in tool.executables):
        return None
    return Finding(
        tool.key, tool.name, Severity.WARNING, "path",
        f"{tool.name} was found in {folder}, which is not on PATH, so it cannot be started "
        "from a terminal or by programs that look for it on PATH.",
        fix=path_fix(folder, ctx.info.system),
    )


# --------------------------------------------------------------------------
# The manifest's checks
# --------------------------------------------------------------------------


def _run_check(tool: Tool, check: Check, path: str, ctx: _Context) -> Finding:
    if isinstance(check, OutputExcludesCheck):
        return _output_excludes(tool, check, path, ctx)
    if isinstance(check, CompileTestCheck):
        return _compile_test(tool, check, path, ctx)
    return _own_build(tool, check, path, ctx)


def _output_excludes(tool: Tool, check: OutputExcludesCheck, path: str, ctx: _Context) -> Finding:
    """ERROR if the program's output for check.args contains check.text."""
    result = run_command((path, *check.args), runner=ctx.runner, timeout=ctx.timeout)
    shown = show_command(result.command)
    if result.problem in (Problem.TIMEOUT, Problem.START_FAILED):
        return Finding(
            tool.key, tool.name, Severity.WARNING, check.kind,
            f"Could not run the check: {result.error}.",
            fix=f"Run `{shown}` yourself and look at what it prints.",
        )
    output, text = result.output, check.text
    if check.ignore_case:
        output, text = output.lower(), text.lower()
    if text in output:
        return Finding(
            tool.key, tool.name, Severity.ERROR, check.kind,
            f"{_sentence(check.problem)} (`{shown}` mentions \"{check.text}\".)",
            tool.needed_for, check_fix(check, ctx.info),
            cause=f"`{shown}` mentions \"{check.text}\"", repair=check.repair,
        )
    return Finding(
        tool.key, tool.name, Severity.OK, check.kind,
        f"`{shown}` does not mention \"{check.text}\".",
    )


def _compile_test(tool: Tool, check: CompileTestCheck, path: str, ctx: _Context) -> Finding:
    """Write the check's files to a new temporary folder, run each command
    there, and delete the folder again when done (even after a failure)."""
    with tempfile.TemporaryDirectory(prefix="esim-toolman-", ignore_cleanup_errors=True) as folder:
        for name, contents in check.files:
            (Path(folder) / name).write_text(contents, encoding="utf-8")
        for args in check.commands:
            result = run_command(
                (path, *args), runner=ctx.runner, timeout=ctx.compile_timeout, cwd=folder
            )
            if result.problem is not None:
                excerpt = _excerpt(result.output)
                problem = f"{_sentence(check.problem)} {result.error}."
                if excerpt:
                    problem += f" Output: {excerpt}"
                first_line = next((line.strip() for line in result.output.splitlines() if line.strip()), "")
                return Finding(
                    tool.key, tool.name, Severity.ERROR, check.kind, problem,
                    tool.needed_for, check_fix(check, ctx.info),
                    cause=f"{result.error}: {first_line}" if first_line else result.error,
                    repair=check.repair,
                )
    files = ", ".join(name for name, _ in check.files)
    return Finding(
        tool.key, tool.name, Severity.OK, check.kind,
        f"{tool.name} compiled a small test file ({files}) in a temporary folder.",
    )


def _own_build(tool: Tool, check: OwnBuildCheck, path: str, ctx: _Context) -> Finding:
    """OK if the program is eSim's own build; otherwise a WARNING that says
    which features need eSim's own build."""
    is_own, real_path = _is_own_build(check, path, ctx)
    if is_own:
        return Finding(
            tool.key, tool.name, Severity.OK, check.kind,
            f"{_sentence(check.found_note)} ({real_path})",
        )
    return Finding(
        tool.key, tool.name, Severity.WARNING, check.kind,
        f"{_sentence(check.problem)} Detected: {real_path}",
        check.breaks, check_fix(check, ctx.info),
    )


def _is_own_build(check: OwnBuildCheck, path: str, ctx: _Context) -> tuple[bool, str]:
    """Follow symlinks, then test whether the real path is inside one of
    the check's folders. Returns (is it, the real path)."""
    real_path = ctx.resolve(path)
    folders = [expand_search_path(p, ctx.env, ctx.home) for p in check.paths]
    return any(_is_inside(real_path, f) for f in folders if f is not None), real_path


def own_build_fix(tool: Tool, info: PlatformInfo) -> str | None:
    """How to update eSim's own build of ``tool`` on this platform (the fix
    of its own_build check), or None if the tool has no such check."""
    for check in tool.checks:
        if isinstance(check, OwnBuildCheck):
            return check_fix(check, info)
    return None


def _own_build_fix(tool: Tool, path: str, ctx: _Context) -> str | None:
    """If the detected program is eSim's own build, the fix for updating it
    (from its own_build check); otherwise None."""
    for check in tool.checks:
        if isinstance(check, OwnBuildCheck) and _is_own_build(check, path, ctx)[0]:
            return check_fix(check, ctx.info)
    return None


# --------------------------------------------------------------------------
# apt (Linux only). dpkg-query and apt-cache only read the package lists.
# --------------------------------------------------------------------------

# "Candidate: 7.0.11+dfsg-1build4" in `apt-cache policy` output.
_CANDIDATE_RE = re.compile(r"^\s*Candidate:\s*(\S+)", re.MULTILINE)
# The upstream part of a Debian version: "1:7.0.11+dfsg-1build4" -> "7.0.11".
_UPSTREAM_RE = re.compile(r"(?:\d+:)?(\d+(?:\.\d+)*)")


def _apt_installed(package: str, dpkg_query: str, ctx: _Context) -> bool | None:
    """True if dpkg says the package is installed, False if not, None if
    dpkg-query did not answer.

    Installed means the status ends with the word "installed", as in
    "install ok installed". Not installed shows up two ways: a package dpkg
    has never seen makes it exit with 1, and one it knows but that is not
    installed (or was removed) prints "unknown ok not-installed" or
    "deinstall ok config-files" with exit code 0.
    """
    result = run_command(
        (dpkg_query, "-W", "--showformat=${Status}", package),
        runner=ctx.runner, timeout=ctx.timeout,
    )
    if result.problem in (Problem.TIMEOUT, Problem.START_FAILED):
        return None
    return result.exit_code == 0 and result.stdout.split()[-1:] == ["installed"]


def _apt_packages(tool: Tool, path: str | None, ctx: _Context) -> list[Finding]:
    """Are all of the tool's apt packages from the manifest installed?

    The program alone is not enough: on Ubuntu, `kicad` works without
    `kicad-symbols`, but then KiCad's standard libraries are missing. The
    check passes if every package in `packages` is installed, or every one
    in `fallback`. It is skipped for eSim's own build, which is not an apt
    package at all.
    """
    if ctx.info.system != "linux" or tool.apt is None or path is None:
        return []
    dpkg_query = ctx.which("dpkg-query")
    if dpkg_query is None or _own_build_fix(tool, path, ctx) is not None:
        return []

    missing = [p for p in tool.apt.packages if _apt_installed(p, dpkg_query, ctx) is False]
    if not missing:
        return [
            Finding(
                tool.key, tool.name, Severity.OK, "apt_packages",
                f"Its apt packages are installed: {', '.join(tool.apt.packages)}.",
            )
        ]
    if tool.apt.fallback and all(
        _apt_installed(p, dpkg_query, ctx) for p in tool.apt.fallback
    ):
        return [
            Finding(
                tool.key, tool.name, Severity.OK, "apt_packages",
                f"Its fallback apt packages are installed: {', '.join(tool.apt.fallback)}.",
            )
        ]
    return [
        Finding(
            tool.key, tool.name, Severity.WARNING, "apt_packages",
            f"These apt packages for {tool.name} are not installed: {', '.join(missing)}. "
            f"eSim installs them together with {tool.name}.",
            fix=_with_note(f"sudo apt-get install {' '.join(missing)}", tool.apt.note),
        )
    ]


def _apt_conflicts(tool: Tool, ctx: _Context) -> list[Finding]:
    """Is any of the tool's conflicting packages installed?"""
    if ctx.info.system != "linux" or tool.apt is None or not tool.apt.conflicts:
        return []
    packages = " ".join(tool.apt.conflicts)
    dpkg_query = ctx.which("dpkg-query")
    if dpkg_query is None:
        return [
            Finding(
                tool.key, tool.name, Severity.WARNING, "apt_conflict",
                f"dpkg-query was not found, so these conflicting packages could not be "
                f"checked: {packages}.",
                fix=f"Check by hand that these are not installed: {packages}",
            )
        ]

    findings: list[Finding] = []
    for package in tool.apt.conflicts:
        installed = _apt_installed(package, dpkg_query, ctx)
        if installed is None:
            findings.append(
                Finding(
                    tool.key, tool.name, Severity.WARNING, "apt_conflict",
                    f"Could not check whether {package} is installed (dpkg-query did not answer).",
                    fix=f"Check by hand: dpkg -l {package}",
                )
            )
        elif installed:
            findings.append(
                Finding(
                    tool.key, tool.name, Severity.ERROR, "apt_conflict",
                    f"The apt package {package} is installed, and it conflicts with {tool.name}.",
                    tool.needed_for, _with_note(f"sudo apt-get purge {package}", tool.apt.note),
                    cause=f"{package} is installed",
                )
            )
        else:
            findings.append(
                Finding(
                    tool.key, tool.name, Severity.OK, "apt_conflict",
                    f"The conflicting apt package {package} is not installed.",
                )
            )
    return findings


def _apt_candidate(package: str, ctx: _Context) -> str | None:
    """The newest version apt could install, as dotted numbers ("7.0.11"),
    from `apt-cache policy`. None if apt-cache is missing or does not say.

    LC_ALL=C keeps apt's output in English, so "Candidate:" can be found.
    """
    apt_cache = ctx.which("apt-cache")
    if apt_cache is None:
        return None
    result = run_command(
        (apt_cache, "policy", package),
        runner=ctx.runner, timeout=ctx.timeout, env={**ctx.env, "LC_ALL": "C"},
    )
    match = _CANDIDATE_RE.search(result.stdout) if result.problem is None else None
    if match is None or match.group(1) == "(none)":
        return None
    upstream = _UPSTREAM_RE.match(match.group(1))
    return upstream.group(1) if upstream else None


def _upgrade_fix(tool: Tool, target: str | None, ctx: _Context) -> str:
    """How to reach version ``target``. On Linux, first ask apt for its
    newest version: if even that is older than the target, an apt upgrade
    cannot help, so say that (with the manifest's note) instead of
    suggesting a command that would change nothing."""
    if ctx.info.system == "linux" and tool.apt is not None and target is not None:
        package = tool.apt.packages[0]
        candidate = _apt_candidate(package, ctx)
        if candidate is not None and compare_versions(candidate, target) < 0:
            return _with_note(
                f"apt cannot reach {target}: the newest {package} it offers is {candidate} "
                f"(see `apt-cache policy {package}`).",
                tool.apt.note,
            )
    return install_fix(tool, ctx.info, upgrade=True)


# --------------------------------------------------------------------------
# Fixes for this platform
# --------------------------------------------------------------------------


def install_fix(tool: Tool, info: PlatformInfo, *, upgrade: bool = False) -> str:
    """The exact install (or upgrade) command for this platform, from the
    manifest's apt or winget section, or its manual hint."""
    if info.system == "linux":
        apt = tool.apt
        if apt is None:
            return f"Install {tool.name} by hand; the manifest has no apt package for it."
        packages = " ".join(apt.packages)
        if upgrade:
            lines = [f"sudo apt-get update && sudo apt-get install --only-upgrade {packages}"]
        else:
            lines = [f"sudo apt-get install {packages}"]
        if apt.fallback:
            lines.append(f"If that fails: sudo apt-get install {' '.join(apt.fallback)}")
        return _with_note("\n".join(lines), apt.note)

    if info.system == "windows":
        winget = tool.winget
        if winget is None:
            return f"Install {tool.name} by hand; the manifest has no winget entry for it."
        if not winget.available or winget.id is None:
            return _sentence(winget.note or f"Install {tool.name} by hand.")
        if upgrade and winget.version is None:
            lines = [f"winget upgrade --id {winget.id} --exact"]
        else:
            command = f"winget install --id {winget.id} --exact"
            if winget.version:
                command += f" --version {winget.version}"
            lines = [command]
        return _with_note("\n".join(lines), winget.note)

    return f"Install {tool.name} by hand; esim-toolman has no package manager for {info.system}."


def check_fix(check: Check, info: PlatformInfo) -> str:
    """A check's fix in words, plus its exact command for this platform if
    the manifest gives one."""
    lines = [_sentence(check.fix)]
    command = {"linux": check.fix_linux, "windows": check.fix_windows}.get(info.system)
    if command:
        lines.append(command)
    return "\n".join(lines)


def path_fix(folder: str, system: str) -> str:
    """How to add ``folder`` to the user's PATH on this system."""
    if system == "windows":
        return (
            "Add the folder to your user PATH in PowerShell, then open a new terminal:\n"
            '[Environment]::SetEnvironmentVariable("Path", '
            f'[Environment]::GetEnvironmentVariable("Path", "User") + ";{folder}", "User")'
        )
    return (
        "Add the folder to PATH, then open a new terminal:\n"
        f"echo 'export PATH=\"{folder}:$PATH\"' >> ~/.bashrc"
    )


# --------------------------------------------------------------------------
# Small text helpers
# --------------------------------------------------------------------------


def _is_inside(path: str, folder: str) -> bool:
    """True if ``path`` is ``folder`` or somewhere below it. Paths are
    normalised first, so "/" vs "\\" and (on Windows) letter case do not
    matter."""
    path = os.path.normcase(os.path.normpath(path))
    folder = os.path.normcase(os.path.normpath(folder))
    return path == folder or path.startswith(folder.rstrip(os.sep) + os.sep)


def _with_note(fix: str, note: str | None) -> str:
    """A fix, followed by a "Note: ..." line when the manifest has a note.
    Every note goes through here, so drop_repeated_notes can find them."""
    return f"{fix}\nNote: {_sentence(note)}" if note else fix


def _sentence(text: str) -> str:
    """Make sure the text ends like a sentence."""
    text = text.rstrip()
    return text if text.endswith((".", "!", "?", ")")) else text + "."


def _excerpt(output: str, lines: int = 3, limit: int = 300) -> str:
    """The last few non-empty lines of a command's output, shortened."""
    kept = [line.strip() for line in output.splitlines() if line.strip()][-lines:]
    text = " | ".join(kept)
    return text if len(text) <= limit else "..." + text[-limit:]


def _plural(count: int, word: str) -> str:
    return f"{count} {word}" if count == 1 else f"{count} {word}s"
