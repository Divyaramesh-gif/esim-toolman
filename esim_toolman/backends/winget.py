"""winget backend (Windows): winget install --id <id> --exact [--version <pinned>]
--accept-package-agreements --accept-source-agreements."""

from esim_toolman.backends.base import Backend, InstallCommand
from esim_toolman.manifest import Tool


class WingetBackend(Backend):
    """Installs the manifest's confirmed winget package."""

    name = "winget"
    program = "winget"

    def build(self, tool: Tool, *, use_fallback: bool = False) -> InstallCommand | None:
        winget = tool.winget
        # winget entries have no fallback; only confirmed packages have an ID.
        if use_fallback or winget is None or not winget.available or winget.id is None:
            return None
        command = ["winget", "install", "--id", winget.id, "--exact"]
        if winget.version:
            command += ["--version", winget.version]
        command += ["--accept-package-agreements", "--accept-source-agreements"]
        return InstallCommand(backend=self.name, command=tuple(command))

    def manual_fix(self, tool: Tool) -> str:
        if tool.winget is not None and tool.winget.note:
            return tool.winget.note
        return f"The manifest has no winget package for {tool.name}; install it by hand."
