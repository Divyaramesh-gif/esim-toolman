"""apt backend (Ubuntu): sudo apt-get install -y <packages from the manifest>."""

import re
from collections.abc import Mapping

from esim_toolman.backends.base import Backend, InstallCommand
from esim_toolman.detect import Runner, run_command
from esim_toolman.manifest import Tool

# `apt-get install -s` prints one "Inst <package> (...)" line per package
# it would install.
_INST_RE = re.compile(r"^Inst (\S+)", re.MULTILINE)


class AptBackend(Backend):
    """Installs the manifest's apt packages with apt-get."""

    name = "apt"
    program = "apt-get"

    def build(self, tool: Tool, *, use_fallback: bool = False) -> InstallCommand | None:
        apt = tool.apt
        if apt is None:
            return None
        packages = apt.fallback if use_fallback else apt.packages
        if not packages:
            return None
        return InstallCommand(
            backend=self.name,
            command=("sudo", "apt-get", "install", "-y", *packages),
            packages=packages,
            fallback=use_fallback,
        )

    def manual_fix(self, tool: Tool) -> str:
        return f"The manifest has no apt package for {tool.name}; install it by hand."

    def would_install(
        self,
        command: InstallCommand,
        *,
        runner: Runner | None = None,
        env: Mapping[str, str] | None = None,
    ) -> tuple[frozenset[str] | None, str | None]:
        """Ask apt what it would install, with `apt-get install -s`: a
        simulation that needs no root and changes nothing. LC_ALL=C keeps
        the output in English so the "Inst" lines can be found."""
        result = run_command(
            ("apt-get", "install", "-s", *command.packages),
            runner=runner,
            env={**(env or {}), "LC_ALL": "C"},
        )
        if result.problem is not None:
            reason = result.error or "the simulation failed"
            last_line = result.output.strip().splitlines()[-1:] or [""]
            return None, f"{reason}: {last_line[0]}" if last_line[0] else reason
        return frozenset(_INST_RE.findall(result.stdout)), None
