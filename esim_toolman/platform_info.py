"""Find out which operating system esim-toolman is running on.

Read-only: on Linux it reads /etc/os-release, and on every system it checks
which package managers are on PATH. It never changes anything.

An Ubuntu release missing from the manifest's tested list, or a Linux that
is not Ubuntu, gives a warning, never an error: esim-toolman carries on and
says that results there are untested.
"""

import platform
import shlex
import shutil
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

OS_RELEASE_PATHS: tuple[Path, ...] = (Path("/etc/os-release"), Path("/usr/lib/os-release"))
"""Where a Linux system describes itself. The second file is the standard
fallback when the first one is missing (see `man os-release`)."""

PACKAGE_MANAGERS: tuple[str, ...] = ("apt-get", "winget")
"""Package managers esim-toolman has (or will have) backends for."""


@dataclass(frozen=True)
class PlatformInfo:
    """What esim-toolman knows about the computer it runs on."""

    system: str  # "linux", "windows", or another lowercase name such as "darwin"
    distro_id: str | None = None  # ID from os-release, e.g. "ubuntu"
    distro_version: str | None = None  # VERSION_ID, e.g. "24.04"
    distro_name: str | None = None  # PRETTY_NAME, e.g. "Ubuntu 24.04.1 LTS"
    package_managers: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()

    def describe(self) -> str:
        """A short description for people, e.g. "Linux (Ubuntu 24.04.1 LTS)"."""
        if self.system == "linux":
            name = self.distro_name or " ".join(
                part for part in (self.distro_id, self.distro_version) if part
            )
            return f"Linux ({name or 'unknown distribution'})"
        if self.system == "windows":
            return "Windows"
        return self.system

    def to_dict(self) -> dict[str, object]:
        """The same information as plain values, for JSON output."""
        return {
            "system": self.system,
            "distro_id": self.distro_id,
            "distro_version": self.distro_version,
            "distro_name": self.distro_name,
            "package_managers": list(self.package_managers),
        }


def detect_platform(
    supported_ubuntu_versions: Sequence[str],
    *,
    system: str | None = None,
    os_release_paths: Sequence[Path] = OS_RELEASE_PATHS,
    which: Callable[[str], str | None] | None = None,
) -> PlatformInfo:
    """Describe this computer.

    ``supported_ubuntu_versions`` comes from the manifest. The keyword
    arguments let tests pretend to be another system; normal callers leave
    them out and get the real values.
    """
    system = (system or platform.system()).lower()
    which = which or shutil.which
    managers = tuple(name for name in PACKAGE_MANAGERS if which(name))

    if system == "windows":
        return PlatformInfo(system=system, package_managers=managers)
    if system != "linux":
        warning = f"{system} is not supported; esim-toolman is written for Ubuntu Linux and Windows."
        return PlatformInfo(system=system, package_managers=managers, warnings=(warning,))

    fields = read_os_release(os_release_paths)
    if fields is None:
        looked_in = ", ".join(path.as_posix() for path in os_release_paths)
        warning = f"Could not read os-release ({looked_in}), so the Linux distribution is unknown."
        return PlatformInfo(system=system, package_managers=managers, warnings=(warning,))

    distro_id = fields.get("ID") or None
    distro_version = fields.get("VERSION_ID") or None
    warning = check_ubuntu_version(distro_id, distro_version, supported_ubuntu_versions)
    return PlatformInfo(
        system=system,
        distro_id=distro_id,
        distro_version=distro_version,
        distro_name=fields.get("PRETTY_NAME") or None,
        package_managers=managers,
        warnings=(warning,) if warning else (),
    )


def check_ubuntu_version(
    distro_id: str | None,
    distro_version: str | None,
    supported_versions: Sequence[str],
) -> str | None:
    """Return a warning if this Linux is not a tested Ubuntu release, else None."""
    if distro_id != "ubuntu":
        return (
            f"This Linux distribution ({distro_id or 'unknown'}) is not Ubuntu. "
            "esim-toolman is written for Ubuntu, so package names and versions may differ."
        )
    if distro_version not in supported_versions:
        tested = ", ".join(supported_versions) or "none"
        return (
            f"Ubuntu {distro_version or '(unknown version)'} is not in the manifest's "
            f"tested list ({tested}). Continuing, but results on this release are untested."
        )
    return None


def read_os_release(paths: Sequence[Path] = OS_RELEASE_PATHS) -> dict[str, str] | None:
    """Parse the first readable os-release file, or return None if none can be read."""
    for path in paths:
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        return parse_os_release(text)
    return None


def parse_os_release(text: str) -> dict[str, str]:
    """Parse os-release text: KEY=value lines, values optionally quoted.

    Comments and blank lines are skipped. Quotes are removed the way a shell
    would (shlex), which is what the os-release format specifies.
    """
    fields: dict[str, str] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, raw_value = line.partition("=")
        try:
            value = " ".join(shlex.split(raw_value))
        except ValueError:  # unbalanced quotes: keep the text without outer quotes
            value = raw_value.strip().strip("\"'")
        fields[key.strip()] = value
    return fields
