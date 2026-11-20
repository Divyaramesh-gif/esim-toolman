"""Plan and run installs: `install <tool>` and `install --missing`.

The order of work:

1. Detect the selected tools, with the same rules as `status`.
   `install --missing` selects the required tools that are NOT_FOUND or
   TOO_OLD; `install <tool>` selects that one tool.
2. Build a plan: one action per tool.
     install  an exact command, built only from the manifest (apt or winget)
     skip     already installed and good enough
     manual   no package for this platform; the manifest's manual hint is shown
     refuse   the package manager is missing, eSim's own build is installed,
              or apt would also install a package that conflicts with the tool
3. Print the plan. --dry-run stops here.
4. Ask for confirmation, unless --yes was given.
5. Run each install command, write it to the action log, and verify it by
   re-running the tool's doctor checks. If verification fails and the
   manifest has a fallback (GHDL: ghdl-llvm -> ghdl-gcc), show the
   fallback, ask again, then run and verify it.

Before step 5 only read-only commands run: the tools' version commands and
apt's install simulation (apt-get install -s).
"""

import os
import shutil
import subprocess
import sys
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from esim_toolman import actionlog
from esim_toolman.backends import Backend, InstallCommand, backend_for
from esim_toolman.detect import DetectResult, Runner, Which, detect_tool
from esim_toolman.doctor import Severity, check_tool, is_own_build, own_build_fix
from esim_toolman.manifest import Manifest, Tool
from esim_toolman.platform_info import PlatformInfo
from esim_toolman.versions import Status, classify, extract_version

EXIT_OK = 0  # everything planned was installed and verified, or nothing was needed
EXIT_NOT_OK = 1  # something was refused, manual, declined, failed or not verified
EXIT_ERROR = 2  # could not run: an unknown tool name


class Action(StrEnum):
    """What the plan does for one tool."""

    INSTALL = "install"
    SKIP = "skip"
    MANUAL = "manual"
    REFUSE = "refuse"


@dataclass(frozen=True)
class PlanItem:
    """One line of the plan."""

    tool: Tool
    status: Status  # before installing
    installed: str | None  # version before installing, if found
    action: Action
    command: InstallCommand | None = None  # set for INSTALL (and REFUSE, for reference)
    reason: str = ""  # for SKIP, MANUAL and REFUSE: why, or what to do by hand


@dataclass(frozen=True)
class Machine:
    """The computer the installer works on and how it talks to the user.
    make_machine() fills in the real parts; tests swap in fakes."""

    info: PlatformInfo
    env: Mapping[str, str]
    home: str
    which: Which
    runner: Runner
    resolve: Callable[[str], str]
    ask: Callable[[str], str]  # shows a question, returns the typed answer
    log_path: Path
    read_windows_path: Callable[[], str]  # the PATH stored in the registry


def make_machine(
    info: PlatformInfo,
    *,
    env: Mapping[str, str] | None = None,
    home: str | None = None,
    which: Which | None = None,
    runner: Runner | None = None,
    resolve: Callable[[str], str] | None = None,
    ask: Callable[[str], str] | None = None,
    log_path: Path | None = None,
    read_windows_path: Callable[[], str] | None = None,
) -> Machine:
    """The real machine, with any part replaced by the caller."""
    return Machine(
        info=info,
        env=os.environ if env is None else env,
        home=str(Path.home()) if home is None else home,
        which=which or shutil.which,
        runner=runner or subprocess.run,
        resolve=resolve or os.path.realpath,
        ask=ask or input,
        log_path=log_path or actionlog.default_log_path(),
        read_windows_path=read_windows_path or _read_windows_path,
    )


# --------------------------------------------------------------------------
# The whole command
# --------------------------------------------------------------------------


def install(
    manifest: Manifest,
    machine: Machine,
    *,
    tool_key: str | None = None,
    missing: bool = False,
    dry_run: bool = False,
    yes: bool = False,
) -> int:
    """`install <tool>` (tool_key) or `install --missing`. Returns the exit code."""
    if tool_key is None and not missing:
        print("esim-toolman: name a tool or use --missing", file=sys.stderr)
        return EXIT_ERROR
    if tool_key is not None and tool_key not in manifest.tools:
        known = ", ".join(manifest.tools)
        print(f"esim-toolman: unknown tool {tool_key!r} (known tools: {known})", file=sys.stderr)
        return EXIT_ERROR

    backend = backend_for(machine.info.system)
    plan = build_plan(manifest, machine, backend, tool_key=tool_key)
    print(format_plan(machine.info, backend, plan))

    blocked = any(item.action in (Action.MANUAL, Action.REFUSE) for item in plan)
    to_install = [item for item in plan if item.action is Action.INSTALL]
    if dry_run:
        print("\nDry run: nothing was installed.")
        return EXIT_NOT_OK if blocked else EXIT_OK
    if not to_install or backend is None:  # INSTALL items only exist with a backend
        return EXIT_NOT_OK if blocked else EXIT_OK

    count = len(to_install)
    question = f"\nRun the {count} install command{'s' if count != 1 else ''} above?"
    if not confirm(question, machine, yes):
        print("Cancelled: nothing was installed.")
        return EXIT_NOT_OK

    all_verified = True
    for item in to_install:
        if not install_one(item, backend, machine, yes=yes):
            all_verified = False
    return EXIT_OK if all_verified and not blocked else EXIT_NOT_OK


# --------------------------------------------------------------------------
# Building and printing the plan
# --------------------------------------------------------------------------


def build_plan(
    manifest: Manifest,
    machine: Machine,
    backend: Backend | None,
    *,
    tool_key: str | None = None,
) -> list[PlanItem]:
    """One PlanItem per selected tool: the named tool, or (with no name)
    every required tool, where only NOT_FOUND and TOO_OLD get installed."""
    plan: list[PlanItem] = []
    for tool in manifest.tools.values():
        if tool_key is not None and tool.key != tool_key:
            continue
        if tool_key is None and not tool.required:
            continue
        detection, installed, status = tool_status(tool, machine)
        if tool_key is None and status not in (Status.NOT_FOUND, Status.TOO_OLD):
            reason = f"installed ({installed or 'version unknown'}); --missing only installs NOT_FOUND or TOO_OLD"
            plan.append(PlanItem(tool, status, installed, Action.SKIP, reason=reason))
            continue
        plan.append(plan_item(tool, detection, installed, status, backend, machine))
    return plan


def plan_item(
    tool: Tool,
    detection: DetectResult,
    installed: str | None,
    status: Status,
    backend: Backend | None,
    machine: Machine,
) -> PlanItem:
    """Decide what to do for one tool. The checks run in this order; the
    first one that applies decides."""

    def item(action: Action, command: InstallCommand | None = None, reason: str = "") -> PlanItem:
        return PlanItem(tool, status, installed, action, command, reason)

    if status is Status.OK:
        return item(Action.SKIP, reason=f"already installed: {installed} at {detection.path}")
    if backend is None:
        return item(
            Action.MANUAL,
            reason=f"esim-toolman has no package manager for {machine.info.system}; "
            f"install {tool.name} by hand",
        )
    if not machine.which(backend.program):
        return item(Action.REFUSE, reason=f"{backend.program} was not found on PATH")
    command = backend.build(tool)
    if command is None:
        return item(Action.MANUAL, reason=backend.manual_fix(tool))
    refusal = refusal_reason(tool, detection, command, backend, machine)
    if refusal is not None:
        return item(Action.REFUSE, command, refusal)
    return item(Action.INSTALL, command)


def refusal_reason(
    tool: Tool,
    detection: DetectResult,
    command: InstallCommand,
    backend: Backend,
    machine: Machine,
) -> str | None:
    """Why this command must not run, or None if it may.

    1. eSim's own build is installed: the package would replace it (for
       ngspice, apt would take over /usr/bin/ngspice).
    2. The tool has conflicting packages, and the package manager's
       simulation says the command would install one of them, or the
       simulation itself failed, so that cannot be ruled out.
    """
    if detection.found and detection.path is not None and is_own_build(
        tool, detection.path, env=machine.env, home=machine.home, resolve=machine.resolve
    ):
        how = own_build_fix(tool, machine.info)
        update = f" To update it instead: {' '.join(how.splitlines())}" if how else ""
        return (
            f"eSim's own build of {tool.name} is installed ({detection.path}); installing "
            f"the {backend.name} package would replace it.{update}"
        )

    conflicts = frozenset(tool.apt.conflicts) if tool.apt is not None else frozenset()
    if conflicts:
        would_install, error = backend.would_install(command, runner=machine.runner, env=machine.env)
        if error is not None:
            return (
                f"could not check what {backend.name} would install ({error}), so a "
                f"conflicting package ({', '.join(sorted(conflicts))}) cannot be ruled out"
            )
        pulled_in = conflicts & (would_install or frozenset())
        if pulled_in:
            return (
                f"it would also install {', '.join(sorted(pulled_in))}, which conflicts "
                f"with {tool.name}"
            )
    return None


def tool_status(tool: Tool, machine: Machine, which: Which | None = None) -> tuple[DetectResult, str | None, Status]:
    """Detect the tool and judge its version, with the same rules as `status`."""
    detection = detect_tool(
        tool, machine.info.system,
        env=machine.env, home=machine.home, which=which or machine.which, runner=machine.runner,
    )
    installed = extract_version(detection.output, tool.version.pattern) if detection.found else None
    status = classify(detection.found, installed, tool.version.minimum, tool.version.recommended)
    return detection, installed, status


def format_plan(info: PlatformInfo, backend: Backend | None, plan: list[PlanItem]) -> str:
    """The plan as a table: tool, status, action, backend, and the exact
    command (for install) or the reason (for skip, manual and refuse)."""
    manager = f"{backend.name} ({backend.program})" if backend else "none"
    lines = [f"Platform: {info.describe()}", f"Package manager: {manager}", "", "Install plan:"]
    header = ("TOOL", "STATUS", "ACTION", "BACKEND", "COMMAND OR REASON")
    rows = [
        (
            item.tool.name + (" *" if item.tool.required else ""),
            item.status.value,
            item.action.value,
            item.command.backend if item.action is Action.INSTALL and item.command else "-",
            item.command.shown() if item.action is Action.INSTALL and item.command else item.reason,
        )
        for item in plan
    ]
    widths = [max(len(row[column]) for row in (header, *rows)) for column in range(len(header) - 1)]
    for row in (header, *rows):
        padded = [cell.ljust(width) for cell, width in zip(row, widths)]
        lines.append("  " + "  ".join([*padded, row[-1]]).rstrip())
    counts = {action: sum(item.action is action for item in plan) for action in Action}
    lines.append("")
    lines.append(
        f"{counts[Action.INSTALL]} to install, {counts[Action.MANUAL]} manual, "
        f"{counts[Action.REFUSE]} refused, {counts[Action.SKIP]} skipped. (* required)"
    )
    return "\n".join(lines)


# --------------------------------------------------------------------------
# Running, verifying, falling back
# --------------------------------------------------------------------------


def install_one(item: PlanItem, backend: Backend, machine: Machine, *, yes: bool) -> bool:
    """Run one planned install and verify it. If verification fails and the
    manifest has a fallback, offer it. True if the tool ends up verified."""
    if item.command is None:  # only INSTALL items get here, and they have a command
        return False
    if run_and_verify(item.tool, item.command, backend, machine):
        return True

    fallback = backend.build(item.tool, use_fallback=True)
    if fallback is None or item.command.fallback:
        return False
    print(f"\nThe manifest has a fallback for {item.tool.name}: {fallback.shown()}")
    detection, _, _ = tool_status(item.tool, machine)
    refusal = refusal_reason(item.tool, detection, fallback, backend, machine)
    if refusal is not None:
        print(f"Not running the fallback: {refusal}.")
        return False
    if not confirm("Run the fallback?", machine, yes):
        print("Fallback not run.")
        return False
    return run_and_verify(item.tool, fallback, backend, machine)


def run_and_verify(tool: Tool, command: InstallCommand, backend: Backend, machine: Machine) -> bool:
    """Run one install command, verify the result, and log both."""
    print(f"\nRunning: {command.shown()}")
    try:
        result = backend.run(command, runner=machine.runner)
    except KeyboardInterrupt:
        _log(tool, command, machine, result="interrupted", exit_code=None,
             verification="skipped", details=["stopped with Ctrl+C"])
        raise
    if result.problem is not None:
        print(f"  Failed: {result.error}.")
        _log(tool, command, machine, result="error" if result.exit_code is None else "failed",
             exit_code=result.exit_code, verification="skipped", details=[result.error or ""])
        return False

    print(f"Verifying {tool.name} ...")
    passed, details = verify(tool, machine)
    print(f"  Verification {'passed' if passed else 'FAILED'}:")
    for line in details:
        print(f"    {line}")
    _log(tool, command, machine, result="ok", exit_code=result.exit_code,
         verification="passed" if passed else "failed", details=details)
    return passed


def verify(tool: Tool, machine: Machine) -> tuple[bool, list[str]]:
    """Re-run the tool's doctor checks. It passes when the tool is found and
    no finding is an ERROR. Returns (passed, what was found)."""
    which = _which_with_fresh_path(machine)
    detection, installed, _ = tool_status(tool, machine, which)
    if not detection.found:
        return False, [f"{tool.name} is still not found: {detection.error}."]
    findings = check_tool(
        tool, machine.info,
        env=machine.env, home=machine.home, which=which,
        runner=machine.runner, resolve=machine.resolve,
    )
    errors = [f.problem for f in findings if f.severity is Severity.ERROR]
    if errors:
        return False, errors
    warnings = [f"warning: {f.problem}" for f in findings if f.severity is Severity.WARNING]
    return True, [f"{tool.name} {installed or '(version unknown)'} found at {detection.path}.", *warnings]


def confirm(question: str, machine: Machine, yes: bool) -> bool:
    """Ask a yes/no question; only "y" or "yes" counts as yes. --yes answers
    for the user. No answer (end of input) means no."""
    if yes:
        print(f"{question} yes (--yes)")
        return True
    try:
        answer = machine.ask(f"{question} [y/N] ")
    except EOFError:
        print()
        return False
    return answer.strip().lower() in ("y", "yes")


def _log(
    tool: Tool,
    command: InstallCommand,
    machine: Machine,
    *,
    result: str,
    exit_code: int | None,
    verification: str,
    details: list[str],
) -> None:
    actionlog.append(
        tool=tool.key,
        action="install-fallback" if command.fallback else "install",
        backend=command.backend,
        command=command.command,
        result=result,
        exit_code=exit_code,
        verification=verification,
        details=details,
        path=machine.log_path,
    )


# --------------------------------------------------------------------------
# Windows: PATH changes made by an installer
# --------------------------------------------------------------------------


def _which_with_fresh_path(machine: Machine) -> Which:
    """On Windows an installer that adds its folder to PATH changes the
    registry, not this running process. So for verification, search the
    PATH stored in the registry as well as this process's PATH."""
    if machine.info.system != "windows":
        return machine.which
    stored = machine.read_windows_path()
    if not stored:
        return machine.which
    search = os.pathsep.join(part for part in (stored, machine.env.get("PATH", "")) if part)

    def which(name: str, path: str | None = None) -> str | None:
        return machine.which(name, path=search if path is None else path)

    return which


def _read_windows_path() -> str:
    """The machine and user PATH from the Windows registry ("" elsewhere)."""
    try:
        import winreg
    except ImportError:  # not Windows
        return ""
    keys = (
        (winreg.HKEY_LOCAL_MACHINE, r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment"),
        (winreg.HKEY_CURRENT_USER, "Environment"),
    )
    parts: list[str] = []
    for root, subkey in keys:
        try:
            with winreg.OpenKey(root, subkey) as key:
                value, _ = winreg.QueryValueEx(key, "Path")
        except OSError:
            continue
        parts.append(os.path.expandvars(value))
    return os.pathsep.join(parts)
