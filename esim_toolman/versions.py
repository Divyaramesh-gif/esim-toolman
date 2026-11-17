"""Read a tool's version from its output and judge it against the manifest.

Pure functions with no side effects:

* extract_version() pulls the version number out of a program's output,
  using the regex from the manifest;
* classify() turns "was it found, which version, what does eSim want" into
  one Status value.

Versions are compared as numbers, part by part, so "10" is newer than "9",
and trailing zeros do not matter ("9.0" equals "9").
"""

import re
from enum import StrEnum

VERSION_RE = re.compile(r"\d+(?:\.\d+)*")
"""A version as this project writes it: dotted numbers, such as 45.2 or 9.0.3."""


class Status(StrEnum):
    """The result of checking one tool."""

    OK = "OK"  # found, and at least the recommended version
    BELOW_RECOMMENDED = "BELOW_RECOMMENDED"  # usable, but older than eSim recommends
    TOO_OLD = "TOO_OLD"  # older than eSim's minimum
    NOT_FOUND = "NOT_FOUND"  # no program found
    UNKNOWN = "UNKNOWN"  # program found, but its version could not be read


def version_key(version: str) -> tuple[int, ...]:
    """Turn "9.0.3" into (9, 0, 3) so versions compare as numbers.

    Trailing zeros are dropped first, so "9", "9.0" and "9.0.0" all become
    (9,). Raises ValueError if the text is not dotted numbers.
    """
    if not VERSION_RE.fullmatch(version):
        raise ValueError(f"not a version made of dotted numbers: {version!r}")
    parts = [int(part) for part in version.split(".")]
    while len(parts) > 1 and parts[-1] == 0:
        parts.pop()
    return tuple(parts)


def compare_versions(left: str, right: str) -> int:
    """Return -1 if ``left`` is older than ``right``, 0 if equal, 1 if newer."""
    a, b = version_key(left), version_key(right)
    return (a > b) - (a < b)


def extract_version(output: str, pattern: re.Pattern[str]) -> str | None:
    """Return capture group 1 of the first match of ``pattern`` in ``output``.

    Returns None when nothing matches, or when the captured text is not
    dotted numbers, so unreadable output becomes UNKNOWN instead of an error.
    """
    match = pattern.search(output)
    if match is None:
        return None
    version = match.group(1)
    if version is None or not VERSION_RE.fullmatch(version):
        return None
    return version


def classify(
    found: bool,
    installed: str | None,
    minimum: str | None = None,
    recommended: str | None = None,
) -> Status:
    """Decide one Status. The checks run in this order; the first that applies wins:

    not found -> NOT_FOUND; no readable version -> UNKNOWN;
    below minimum -> TOO_OLD; below recommended -> BELOW_RECOMMENDED;
    otherwise OK (newer than recommended is also OK).
    """
    if not found:
        return Status.NOT_FOUND
    if installed is None:
        return Status.UNKNOWN
    if minimum is not None and compare_versions(installed, minimum) < 0:
        return Status.TOO_OLD
    if recommended is not None and compare_versions(installed, recommended) < 0:
        return Status.BELOW_RECOMMENDED
    return Status.OK
