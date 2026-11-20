"""The interface every package-manager backend follows.

A backend turns the manifest's data for one tool into one exact install
command, and runs it. It never invents a package name or ID: when the
manifest has nothing for this backend, build() returns None and the
installer prints the manifest's manual hint instead.
"""

import subprocess
from abc import ABC, abstractmethod
from collections.abc import Mapping
from dataclasses import dataclass
from typing import ClassVar

from esim_toolman.detect import CommandResult, Problem, Runner, show_command
from esim_toolman.manifest import Tool


@dataclass(frozen=True)
class InstallCommand:
    """One install command, ready to run."""

    backend: str  # "apt" or "winget"
    command: tuple[str, ...]  # the exact argument list
    packages: tuple[str, ...] = ()  # the apt packages it installs (empty for winget)
    fallback: bool = False  # built from the manifest's fallback packages

    def shown(self) -> str:
        """The command as one line, for plans and messages."""
        return " ".join(self.command)


class Backend(ABC):
    """A package manager: apt on Ubuntu, winget on Windows."""

    name: ClassVar[str]  # "apt" or "winget"
    program: ClassVar[str]  # the program that must be on PATH: "apt-get" or "winget"

    @abstractmethod
    def build(self, tool: Tool, *, use_fallback: bool = False) -> InstallCommand | None:
        """The install command for ``tool``, built only from the manifest.
        None when the manifest has no package for this backend (or no
        fallback, when ``use_fallback`` is set)."""

    @abstractmethod
    def manual_fix(self, tool: Tool) -> str:
        """What to tell the user when build() returns None."""

    def would_install(
        self,
        command: InstallCommand,
        *,
        runner: Runner | None = None,
        env: Mapping[str, str] | None = None,
    ) -> tuple[frozenset[str] | None, str | None]:
        """Which packages running ``command`` would install, without
        installing anything: (package names, None), or (None, error) if
        that could not be found out. Backends that cannot tell return
        (None, None)."""
        return None, None

    def run(self, command: InstallCommand, *, runner: Runner | None = None) -> CommandResult:
        """Run the install command with the terminal attached: output is not
        captured, so the user sees the package manager's progress and can
        type a sudo password. There is no time limit, because an install
        can take a long time."""
        runner = runner or subprocess.run
        try:
            completed = runner(list(command.command), check=False)
        except OSError as exc:
            return CommandResult(
                command=command.command,
                problem=Problem.START_FAILED,
                error=f"`{show_command(command.command)}` could not be started: {exc}",
            )
        if completed.returncode != 0:
            return CommandResult(
                command=command.command,
                exit_code=completed.returncode,
                problem=Problem.EXIT_STATUS,
                error=f"`{show_command(command.command)}` exited with code {completed.returncode}",
            )
        return CommandResult(command=command.command, exit_code=0)
