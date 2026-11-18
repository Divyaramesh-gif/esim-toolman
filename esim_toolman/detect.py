"""Find a tool's program and ask it for its version.

Read-only. For one tool from the manifest, detect_tool():

1. turns the manifest's search_paths for this system into real folders:
   "~", %NAME% and ${NAME} are expanded, "*" is matched with glob, and
   folders that do not exist are dropped;
2. looks for the tool's program names in those folders first, then on
   PATH. eSim's own Windows launcher also puts its bundled tools in front
   of PATH (windows/esim.bat:16-31);
3. runs "<program> <version args>" with run_command(): never through a
   shell, with stdin closed and a time limit, capturing stdout and stderr;
4. returns a DetectResult saying what happened.

run_command() is also used by doctor.py for its checks. Reading the version
number out of the output is versions.py's job.
"""

import glob
import os
import re
import shutil
import subprocess
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from esim_toolman.manifest import Tool

DEFAULT_TIMEOUT_SECONDS = 15.0
"""How long a version command may run. KiCad's kicad-cli can take a few
seconds to start on Windows, so this is generous."""

Which = Callable[..., str | None]
"""Same call shape as shutil.which(name, path=None)."""

Runner = Callable[..., subprocess.CompletedProcess[str]]
"""Same call shape as subprocess.run(args, **options)."""

# %NAME% (Windows style; names may contain brackets, as in
# %ProgramFiles(x86)%) or ${NAME} (POSIX style).
_VARIABLE_RE = re.compile(r"%([A-Za-z_][A-Za-z0-9_()]*)%|\$\{([A-Za-z_][A-Za-z0-9_]*)\}")


class Problem(StrEnum):
    """What went wrong while finding or running a program, if anything."""

    NOT_FOUND = "not_found"  # no program with any of the names was found
    TIMEOUT = "timeout"  # the command ran past the time limit
    START_FAILED = "start_failed"  # the program was found but could not be started
    EXIT_STATUS = "exit_status"  # the command exited with a non-zero code


@dataclass(frozen=True)
class CommandResult:
    """What happened when one command was run."""

    command: tuple[str, ...]
    stdout: str = ""
    stderr: str = ""
    exit_code: int | None = None  # None when the command never finished
    problem: Problem | None = None
    error: str | None = None  # a sentence for people, set whenever problem is set

    @property
    def output(self) -> str:
        """stdout and stderr together. Some tools print to stderr."""
        return "\n".join(part for part in (self.stdout, self.stderr) if part)


@dataclass(frozen=True)
class DetectResult:
    """What detect_tool() found out about one tool."""

    tool_key: str
    found: bool
    path: str | None = None
    command: tuple[str, ...] = ()
    stdout: str = ""
    stderr: str = ""
    exit_code: int | None = None
    problem: Problem | None = None
    error: str | None = None  # a sentence for people, set whenever problem is set
    searched: tuple[str, ...] = ()  # extra folders that were searched before PATH
    found_in_folder: str | None = None  # the extra folder it was found in; None = on PATH

    @property
    def output(self) -> str:
        """stdout and stderr together. Some tools print their version on stderr."""
        return "\n".join(part for part in (self.stdout, self.stderr) if part)


def detect_tool(
    tool: Tool,
    system: str,
    *,
    env: Mapping[str, str] | None = None,
    home: str | None = None,
    which: Which | None = None,
    runner: Runner | None = None,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
) -> DetectResult:
    """Find ``tool`` on this ``system`` ("linux" or "windows") and run its
    version command.

    The keyword arguments replace the real environment, home folder,
    shutil.which and subprocess.run. Tests use them; normal callers leave
    them out.
    """
    env = os.environ if env is None else env
    home = str(Path.home()) if home is None else home
    which = which or shutil.which

    directories = candidate_directories(tool, system, env=env, home=home)
    located = find_executable(tool.executables, directories, which)
    if located is None:
        names = " / ".join(tool.executables)
        also = f" or in {', '.join(directories)}" if directories else ""
        return DetectResult(
            tool_key=tool.key,
            found=False,
            problem=Problem.NOT_FOUND,
            error=f"{names} not found on PATH{also}",
            searched=directories,
        )

    path, folder = located
    result = run_command((path, *tool.version.args), runner=runner, timeout=timeout)
    return DetectResult(
        tool_key=tool.key,
        found=True,
        path=path,
        command=result.command,
        stdout=result.stdout,
        stderr=result.stderr,
        exit_code=result.exit_code,
        problem=result.problem,
        error=result.error,
        searched=directories,
        found_in_folder=folder,
    )


def run_command(
    command: Sequence[str],
    *,
    runner: Runner | None = None,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
    cwd: str | None = None,
) -> CommandResult:
    """Run one command safely and report what happened. Never raises for
    timeouts, programs that cannot start, or non-zero exit codes.

    The command is a list of arguments, never a shell string. stdin is
    closed so a program waiting for input fails instead of hanging, and
    ``timeout`` (seconds) stops programs that never finish.
    """
    runner = runner or subprocess.run
    command = tuple(command)
    shown = show_command(command)
    options: dict[str, object] = {
        "capture_output": True,
        "text": True,
        "encoding": "utf-8",
        "errors": "replace",  # odd bytes in the output must not crash us
        "stdin": subprocess.DEVNULL,
        "timeout": timeout,
        "check": False,
    }
    if cwd is not None:
        options["cwd"] = cwd
    try:
        completed = runner(list(command), **options)
    except subprocess.TimeoutExpired as exc:
        return CommandResult(
            command=command,
            stdout=_as_text(exc.stdout),
            stderr=_as_text(exc.stderr),
            problem=Problem.TIMEOUT,
            error=f"`{shown}` did not finish within {timeout:g} seconds",
        )
    except OSError as exc:
        return CommandResult(
            command=command,
            problem=Problem.START_FAILED,
            error=f"`{shown}` could not be started: {exc}",
        )

    problem = error = None
    if completed.returncode != 0:
        problem = Problem.EXIT_STATUS
        error = f"`{shown}` exited with code {completed.returncode}"
    return CommandResult(
        command=command,
        stdout=completed.stdout or "",
        stderr=completed.stderr or "",
        exit_code=completed.returncode,
        problem=problem,
        error=error,
    )


def show_command(command: Sequence[str]) -> str:
    """A short form of a command for messages: the program's file name
    without its folder, then the arguments, e.g. "ghdl -a t.vhdl"."""
    program = re.split(r"[\\/]", command[0])[-1] if command else ""
    return " ".join([program, *command[1:]])


def candidate_directories(
    tool: Tool,
    system: str,
    *,
    env: Mapping[str, str],
    home: str,
) -> tuple[str, ...]:
    """The tool's extra search folders for ``system`` that exist, in search order.

    A path with "*" can match several folders. They are sorted so the
    highest version number comes first (KiCad\\10.0 before KiCad\\9.0), on
    the idea that the newest install is the one in use.
    """
    patterns = {
        "linux": tool.search_paths.linux,
        "windows": tool.search_paths.windows,
    }.get(system, ())
    directories: list[str] = []
    for pattern in patterns:
        expanded = expand_search_path(pattern, env, home)
        if expanded is None:  # a variable was not set: nothing to search
            continue
        for match in sorted(glob.glob(expanded), key=_natural_key, reverse=True):
            if os.path.isdir(match) and match not in directories:
                directories.append(match)
    return tuple(directories)


def find_executable(
    names: Sequence[str],
    directories: Sequence[str],
    which: Which,
) -> tuple[str, str | None] | None:
    """Return (program path, extra folder it was in), or None if not found.

    The folder is None when the program was found on PATH. Folder by
    folder, every name is tried before the next folder. So with names
    ("ngspice_con", "ngspice"), eSim's own ngspice.exe in the first folder
    wins over an ngspice_con.exe in a later folder. PATH is searched last,
    again in name order. ``which`` adds ".exe" etc. on Windows.
    """
    for directory in directories:
        for name in names:
            found = which(name, path=directory)
            if found:
                return found, directory
    for name in names:
        found = which(name)
        if found:
            return found, None
    return None


def expand_search_path(pattern: str, env: Mapping[str, str], home: str) -> str | None:
    """Expand "~", %NAME% and ${NAME} in one search path.

    %NAME% ignores upper/lower case, like Windows does; ${NAME} does not,
    like POSIX shells. Returns None if a variable is not set, because a path
    that still contains "%NAME%" cannot exist.
    """
    if pattern == "~" or pattern.startswith(("~/", "~\\")):
        pattern = home + pattern[1:]

    upper_env = {key.upper(): value for key, value in env.items()}
    unset: list[str] = []

    def replace(match: re.Match[str]) -> str:
        percent_name, brace_name = match.groups()
        if percent_name is not None:
            value = upper_env.get(percent_name.upper())
        else:
            value = env.get(brace_name)
        if value is None:
            unset.append(match.group(0))
            return match.group(0)
        return value

    expanded = _VARIABLE_RE.sub(replace, pattern)
    return None if unset else expanded


def _natural_key(text: str) -> list[int | str]:
    """Sort key that compares runs of digits as numbers, so "9.0" < "10.0"."""
    return [int(part) if part.isdigit() else part.lower() for part in re.split(r"(\d+)", text)]


def _as_text(value: str | bytes | None) -> str:
    """Output kept from a timed-out command. Python gives it as bytes even
    when text=True was asked for, so decode it here."""
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value
