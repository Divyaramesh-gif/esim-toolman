"""Package-manager backends: apt on Linux, winget on Windows."""

from esim_toolman.backends.apt import AptBackend
from esim_toolman.backends.base import Backend, InstallCommand
from esim_toolman.backends.winget import WingetBackend

__all__ = ["AptBackend", "Backend", "InstallCommand", "WingetBackend", "backend_for"]


def backend_for(system: str) -> Backend | None:
    """The backend for this system ("linux" or "windows"), or None."""
    if system == "linux":
        return AptBackend()
    if system == "windows":
        return WingetBackend()
    return None
