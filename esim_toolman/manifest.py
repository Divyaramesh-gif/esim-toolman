"""Load and check the tool manifest (manifest/tools.toml).

The manifest is the only place that names eSim's external tools. This module
reads it with the standard-library ``tomllib`` and checks it strictly before
any other module uses it:

* a table may only contain the keys the schema defines, so a typo such as
  ``minimun`` is an error instead of being silently ignored;
* required keys must be present and have the right type;
* regexes must compile, versions must be dotted numbers, and package names,
  program names and file names must be plain names. These names are passed
  to other programs later, so odd characters are rejected here.

The result is a tree of frozen (read-only) dataclasses. Any problem raises
``ManifestError`` with the dotted location of the bad value, for example
``tools.ghdl.version.pattern``.
"""

import re
import tomllib
from collections.abc import Set
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, ClassVar

from esim_toolman.versions import VERSION_RE, version_key

SCHEMA_VERSION = 1
"""The only manifest schema version this module understands."""

DEFAULT_MANIFEST_PATH = Path(__file__).resolve().parent.parent / "manifest" / "tools.toml"
"""Location of manifest/tools.toml in a source checkout of esim-toolman."""

# Shapes of the plain names the manifest may contain. All are matched against
# the whole string (fullmatch).
_SLUG_RE = re.compile(r"[a-z][a-z0-9-]*")  # tool keys and feature names
_EXECUTABLE_RE = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_.+-]*")  # no folders
_APT_PACKAGE_RE = re.compile(r"[a-z0-9][a-z0-9+.-]+")  # Debian package name rules
_WINGET_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9.+_-]*")
_WINGET_VERSION_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9.+_-]*")  # 9.0.3, 10.0.0-1, ...
_FILE_NAME_RE = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_.-]*")  # no folders, no ".."
_UBUNTU_RELEASE_RE = re.compile(r"\d+\.\d+")


class ManifestError(ValueError):
    """The manifest is missing, is not valid TOML, or does not follow the schema."""


# --------------------------------------------------------------------------
# The data model: what the rest of esim-toolman gets back.
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class SearchPaths:
    """Folders to search besides PATH, per platform (still unexpanded)."""

    linux: tuple[str, ...] = ()
    windows: tuple[str, ...] = ()


@dataclass(frozen=True)
class VersionSpec:
    """How to ask a tool for its version, and which versions eSim wants."""

    args: tuple[str, ...]
    pattern: re.Pattern[str]
    source: str
    minimum: str | None = None
    recommended: str | None = None


@dataclass(frozen=True)
class AptInstall:
    """How to install a tool with apt."""

    packages: tuple[str, ...]
    source: str
    fallback: tuple[str, ...] = ()
    conflicts: tuple[str, ...] = ()
    note: str | None = None


@dataclass(frozen=True)
class WingetInstall:
    """How to install a tool with winget, or why that is not possible.

    ``version`` pins one exact winget package version; None means the latest.
    """

    available: bool
    source: str
    id: str | None = None
    version: str | None = None
    note: str | None = None


# Every check has `problem` and `fix` (plain words, any platform) and may add
# `fix_linux` / `fix_windows`: the exact command for that platform, and
# `repair`: how `install` may repair a failed check. The only value is
# "fallback": install the tool's apt fallback packages.


@dataclass(frozen=True)
class OutputExcludesCheck:
    """Health check: the tool's output for ``args`` must not contain ``text``."""

    kind: ClassVar[str] = "output_excludes"
    args: tuple[str, ...]
    text: str
    problem: str
    fix: str
    source: str
    ignore_case: bool = False
    fix_linux: str | None = None
    fix_windows: str | None = None
    repair: str | None = None


@dataclass(frozen=True)
class CompileTestCheck:
    """Health check: write ``files`` to a temporary folder, then run the tool
    there once per argument list in ``commands``; every run must succeed."""

    kind: ClassVar[str] = "compile_test"
    files: tuple[tuple[str, str], ...]  # (file name, contents) pairs
    commands: tuple[tuple[str, ...], ...]
    problem: str
    fix: str
    source: str
    fix_linux: str | None = None
    fix_windows: str | None = None
    repair: str | None = None


@dataclass(frozen=True)
class OwnBuildCheck:
    """Health check: is the program eSim's own build? It is when its real
    path (symlinks followed) is inside one of ``paths``. If it is not,
    ``problem`` applies and the features in ``breaks`` will not work."""

    kind: ClassVar[str] = "own_build"
    paths: tuple[str, ...]  # unexpanded, like search_paths
    found_note: str  # shown when it IS eSim's own build
    breaks: tuple[str, ...]
    problem: str
    fix: str
    source: str
    fix_linux: str | None = None
    fix_windows: str | None = None
    repair: str | None = None


Check = OutputExcludesCheck | CompileTestCheck | OwnBuildCheck
"""Any one health check."""


@dataclass(frozen=True)
class Tool:
    """Everything the manifest says about one external tool."""

    key: str
    name: str
    purpose: str
    required: bool
    executables: tuple[str, ...]
    version: VersionSpec
    needed_for: tuple[str, ...] = ()
    search_paths: SearchPaths = field(default_factory=SearchPaths)
    apt: AptInstall | None = None
    winget: WingetInstall | None = None
    checks: tuple[Check, ...] = ()


@dataclass(frozen=True)
class Manifest:
    """The whole manifest. ``tools`` keeps the order of the file."""

    schema_version: int
    tools: dict[str, Tool]
    ubuntu_supported_versions: tuple[str, ...] = ()


# --------------------------------------------------------------------------
# Public entry points.
# --------------------------------------------------------------------------


def load_manifest(path: Path | str = DEFAULT_MANIFEST_PATH) -> Manifest:
    """Read and check a manifest file.

    Raises ManifestError if the file cannot be read, is not valid TOML, or
    breaks the schema. The error message starts with the file path.
    """
    path = Path(path)
    try:
        with path.open("rb") as handle:
            data = tomllib.load(handle)
    except OSError as exc:
        raise ManifestError(f"{path}: cannot read file ({exc.strerror})") from exc
    except tomllib.TOMLDecodeError as exc:
        raise ManifestError(f"{path}: not valid TOML: {exc}") from exc
    try:
        return parse_manifest(data)
    except ManifestError as exc:
        raise ManifestError(f"{path}: {exc}") from exc


def parse_manifest(data: dict[str, Any]) -> Manifest:
    """Check TOML data that has already been parsed and turn it into a Manifest."""
    _check_keys(data, "manifest", required={"schema_version", "tools"}, optional={"platforms"})

    schema_version = data["schema_version"]
    # type() rather than isinstance(): in Python, True is also an int.
    if type(schema_version) is not int or schema_version != SCHEMA_VERSION:
        raise ManifestError(f"schema_version: expected {SCHEMA_VERSION}, got {schema_version!r}")

    ubuntu_versions: tuple[str, ...] = ()
    if "platforms" in data:
        platforms = _table(data["platforms"], "platforms")
        _check_keys(platforms, "platforms", optional={"ubuntu"})
        if "ubuntu" in platforms:
            where = "platforms.ubuntu"
            ubuntu = _table(platforms["ubuntu"], where)
            _check_keys(ubuntu, where, required={"supported_versions"})
            ubuntu_versions = _names(
                ubuntu["supported_versions"], f"{where}.supported_versions",
                _UBUNTU_RELEASE_RE, "an Ubuntu release such as 24.04",
            )

    tools_table = _table(data["tools"], "tools")
    if not tools_table:
        raise ManifestError("tools: the manifest lists no tools")
    tools: dict[str, Tool] = {}
    for key, raw_tool in tools_table.items():
        if not _SLUG_RE.fullmatch(key):
            raise ManifestError(
                f"tools.{key}: a tool key must be lowercase letters, digits and hyphens"
            )
        tools[key] = _parse_tool(key, _table(raw_tool, f"tools.{key}"))

    return Manifest(
        schema_version=schema_version,
        tools=tools,
        ubuntu_supported_versions=ubuntu_versions,
    )


# --------------------------------------------------------------------------
# One parser per manifest section. Each takes the raw TOML value and its
# dotted location, checks it, and returns the matching dataclass.
# --------------------------------------------------------------------------


def _parse_tool(key: str, raw: dict[str, Any]) -> Tool:
    """Parse one [tools.<key>] table."""
    where = f"tools.{key}"
    _check_keys(
        raw, where,
        required={"name", "purpose", "required", "executables", "version"},
        optional={"needed_for", "search_paths", "install", "checks"},
    )
    apt, winget = _parse_install(raw.get("install", {}), f"{where}.install")
    checks = _parse_checks(raw.get("checks", []), f"{where}.checks")
    # A check that is repaired with the fallback needs fallback packages.
    for index, check in enumerate(checks):
        if check.repair == "fallback" and (apt is None or not apt.fallback):
            raise ManifestError(
                f"{where}.checks[{index}].repair: 'fallback' needs install.apt.fallback packages"
            )
    return Tool(
        key=key,
        name=_str(raw["name"], f"{where}.name"),
        purpose=_str(raw["purpose"], f"{where}.purpose"),
        required=_bool(raw["required"], f"{where}.required"),
        executables=_names(
            raw["executables"], f"{where}.executables",
            _EXECUTABLE_RE, "a plain program name (no folders)",
        ),
        version=_parse_version(raw["version"], f"{where}.version"),
        needed_for=_names(
            raw.get("needed_for", []), f"{where}.needed_for",
            _SLUG_RE, "a lowercase feature name such as vhdl-cosim", allow_empty=True,
        ),
        search_paths=_parse_search_paths(raw.get("search_paths", {}), f"{where}.search_paths"),
        apt=apt,
        winget=winget,
        checks=checks,
    )


def _parse_search_paths(value: Any, where: str) -> SearchPaths:
    """Parse [tools.<key>.search_paths]."""
    table = _table(value, where)
    _check_keys(table, where, optional={"linux", "windows"})
    return SearchPaths(
        linux=_str_list(table.get("linux", []), f"{where}.linux", allow_empty=True),
        windows=_str_list(table.get("windows", []), f"{where}.windows", allow_empty=True),
    )


def _parse_version(value: Any, where: str) -> VersionSpec:
    """Parse [tools.<key>.version]."""
    table = _table(value, where)
    _check_keys(
        table, where,
        required={"args", "pattern", "source"},
        optional={"minimum", "recommended"},
    )
    minimum = _version(table["minimum"], f"{where}.minimum") if "minimum" in table else None
    recommended = (
        _version(table["recommended"], f"{where}.recommended") if "recommended" in table else None
    )
    if minimum and recommended and version_key(minimum) > version_key(recommended):
        raise ManifestError(f"{where}: minimum {minimum} is higher than recommended {recommended}")
    return VersionSpec(
        # Never empty: some tools (ngspice, for one) start an interactive
        # session when run with no arguments.
        args=_str_list(table["args"], f"{where}.args"),
        pattern=_pattern(table["pattern"], f"{where}.pattern"),
        source=_str(table["source"], f"{where}.source"),
        minimum=minimum,
        recommended=recommended,
    )


def _parse_install(value: Any, where: str) -> tuple[AptInstall | None, WingetInstall | None]:
    """Parse [tools.<key>.install], which may hold an apt and a winget table."""
    table = _table(value, where)
    _check_keys(table, where, optional={"apt", "winget"})
    apt = _parse_apt(table["apt"], f"{where}.apt") if "apt" in table else None
    winget = _parse_winget(table["winget"], f"{where}.winget") if "winget" in table else None
    return apt, winget


def _parse_apt(value: Any, where: str) -> AptInstall:
    """Parse [tools.<key>.install.apt]."""
    table = _table(value, where)
    _check_keys(
        table, where,
        required={"packages", "source"},
        optional={"fallback", "conflicts", "note"},
    )
    what = "a valid apt package name"
    packages = _names(table["packages"], f"{where}.packages", _APT_PACKAGE_RE, what)
    fallback = _names(
        table.get("fallback", []), f"{where}.fallback", _APT_PACKAGE_RE, what, allow_empty=True
    )
    conflicts = _names(
        table.get("conflicts", []), f"{where}.conflicts", _APT_PACKAGE_RE, what, allow_empty=True
    )
    clash = set(conflicts) & (set(packages) | set(fallback))
    if clash:
        raise ManifestError(
            f"{where}.conflicts: {', '.join(sorted(clash))} is also listed to be installed"
        )
    return AptInstall(
        packages=packages,
        source=_str(table["source"], f"{where}.source"),
        fallback=fallback,
        conflicts=conflicts,
        note=_optional_str(table, "note", where),
    )


def _parse_winget(value: Any, where: str) -> WingetInstall:
    """Parse [tools.<key>.install.winget].

    A confirmed package needs its ID and may pin an exact ``version``. A tool
    without a package must say how to install it by hand (``note``) and must
    not carry an ID or a version.
    """
    table = _table(value, where)
    _check_keys(
        table, where,
        required={"available", "source"},
        optional={"id", "version", "note"},
    )
    available = _bool(table["available"], f"{where}.available")
    if available:
        if "id" not in table:
            raise ManifestError(f"{where}: 'id' is required when available = true")
        package_id = _name(table["id"], f"{where}.id", _WINGET_ID_RE, "a winget package ID")
        version = (
            _name(table["version"], f"{where}.version", _WINGET_VERSION_RE, "a winget version")
            if "version" in table
            else None
        )
    else:
        if "id" in table:
            raise ManifestError(f"{where}: 'id' must not be set when available = false")
        if "note" not in table:
            raise ManifestError(
                f"{where}: 'note' is required when available = false "
                "(say how to install the tool by hand)"
            )
        if "version" in table:
            raise ManifestError(f"{where}: 'version' must not be set when available = false")
        package_id = None
        version = None
    return WingetInstall(
        available=available,
        source=_str(table["source"], f"{where}.source"),
        id=package_id,
        version=version,
        note=_optional_str(table, "note", where),
    )


def _parse_checks(value: Any, where: str) -> tuple[Check, ...]:
    """Parse the [[tools.<key>.checks]] list."""
    if not isinstance(value, list):
        raise ManifestError(f"{where}: expected a list of check tables ([[...checks]])")
    return tuple(_parse_check(item, f"{where}[{index}]") for index, item in enumerate(value))


# How `install` may repair a failed check: only by installing the fallback.
_REPAIRS = ("fallback",)

# For each check kind: the keys it needs and the keys it may have, on top of
# the keys every check shares (kind, problem, fix, source, fix_linux,
# fix_windows, repair).
_CHECK_KEYS: dict[str, tuple[set[str], set[str]]] = {
    OutputExcludesCheck.kind: ({"args", "text"}, {"ignore_case"}),
    CompileTestCheck.kind: ({"files", "commands"}, set()),
    OwnBuildCheck.kind: ({"paths", "found_note", "breaks"}, set()),
}


def _parse_check(value: Any, where: str) -> Check:
    """Parse one check. Its ``kind`` decides which other keys it must have."""
    table = _table(value, where)
    if "kind" not in table:
        raise ManifestError(f"{where}: missing required key(s): kind")
    kind = _str(table["kind"], f"{where}.kind")
    if kind not in _CHECK_KEYS:
        known = ", ".join(sorted(_CHECK_KEYS))
        raise ManifestError(f"{where}.kind: unknown check kind {kind!r} (known: {known})")

    kind_required, kind_optional = _CHECK_KEYS[kind]
    _check_keys(
        table, where,
        required={"kind", "problem", "fix", "source"} | kind_required,
        optional={"fix_linux", "fix_windows", "repair"} | kind_optional,
    )
    repair = _optional_str(table, "repair", where)
    if repair is not None and repair not in _REPAIRS:
        raise ManifestError(
            f"{where}.repair: unknown repair {repair!r} (known: {', '.join(_REPAIRS)})"
        )
    shared: dict[str, Any] = {
        "problem": _str(table["problem"], f"{where}.problem"),
        "fix": _str(table["fix"], f"{where}.fix"),
        "source": _str(table["source"], f"{where}.source"),
        "fix_linux": _optional_str(table, "fix_linux", where),
        "fix_windows": _optional_str(table, "fix_windows", where),
        "repair": repair,
    }

    if kind == OutputExcludesCheck.kind:
        return OutputExcludesCheck(
            args=_str_list(table["args"], f"{where}.args"),
            text=_str(table["text"], f"{where}.text"),
            ignore_case=_bool(table.get("ignore_case", False), f"{where}.ignore_case"),
            **shared,
        )
    if kind == CompileTestCheck.kind:
        return CompileTestCheck(
            files=_parse_files(table["files"], f"{where}.files"),
            commands=_parse_commands(table["commands"], f"{where}.commands"),
            **shared,
        )
    return OwnBuildCheck(
        paths=_str_list(table["paths"], f"{where}.paths"),
        found_note=_str(table["found_note"], f"{where}.found_note"),
        breaks=_names(
            table["breaks"], f"{where}.breaks",
            _SLUG_RE, "a lowercase feature name such as vhdl-cosim",
        ),
        **shared,
    )


def _parse_files(value: Any, where: str) -> tuple[tuple[str, str], ...]:
    """Parse a compile_test ``files`` table: file name -> file contents.

    Names must be plain file names, so the doctor can only ever write inside
    its own temporary folder.
    """
    table = _table(value, where)
    if not table:
        raise ManifestError(f"{where}: must list at least one file")
    files: list[tuple[str, str]] = []
    for name, contents in table.items():
        if not _FILE_NAME_RE.fullmatch(name):
            raise ManifestError(f"{where}: {name!r} must be a plain file name with no folders")
        if not isinstance(contents, str):
            raise ManifestError(f"{where}[{name!r}]: expected the file contents as a string")
        files.append((name, contents))
    return tuple(files)


def _parse_commands(value: Any, where: str) -> tuple[tuple[str, ...], ...]:
    """Parse a compile_test ``commands`` list: a list of argument lists."""
    if not isinstance(value, list) or not value:
        raise ManifestError(f"{where}: expected a non-empty list of argument lists")
    return tuple(_str_list(item, f"{where}[{index}]") for index, item in enumerate(value))


# --------------------------------------------------------------------------
# Small checking helpers. Each one either returns a clean value or raises
# ManifestError naming the location of the bad value.
# --------------------------------------------------------------------------


def _check_keys(
    table: dict[str, Any],
    where: str,
    *,
    required: Set[str] = frozenset(),
    optional: Set[str] = frozenset(),
) -> None:
    """Every required key must be present, and no key outside the two sets."""
    missing = sorted(set(required) - table.keys())
    if missing:
        raise ManifestError(f"{where}: missing required key(s): {', '.join(missing)}")
    unknown = sorted(table.keys() - set(required) - set(optional))
    if unknown:
        raise ManifestError(f"{where}: unknown key(s): {', '.join(unknown)}")


def _table(value: Any, where: str) -> dict[str, Any]:
    """The value must be a TOML table."""
    if not isinstance(value, dict):
        raise ManifestError(f"{where}: expected a table, got {type(value).__name__}")
    return value


def _str(value: Any, where: str) -> str:
    """The value must be a string with some non-space text in it."""
    if not isinstance(value, str) or not value.strip():
        raise ManifestError(f"{where}: expected a non-empty string, got {value!r}")
    return value


def _optional_str(table: dict[str, Any], key: str, where: str) -> str | None:
    """table[key] checked as a string, or None when the key is absent."""
    return _str(table[key], f"{where}.{key}") if key in table else None


def _bool(value: Any, where: str) -> bool:
    """The value must be true or false."""
    if not isinstance(value, bool):
        raise ManifestError(f"{where}: expected true or false, got {value!r}")
    return value


def _str_list(value: Any, where: str, *, allow_empty: bool = False) -> tuple[str, ...]:
    """The value must be a list of strings (non-empty unless allow_empty)."""
    if not isinstance(value, list):
        raise ManifestError(f"{where}: expected a list of strings, got {value!r}")
    if not value and not allow_empty:
        raise ManifestError(f"{where}: must not be empty")
    return tuple(_str(item, f"{where}[{index}]") for index, item in enumerate(value))


def _name(value: Any, where: str, pattern: re.Pattern[str], what: str) -> str:
    """The value must be a string that fully matches ``pattern``."""
    text = _str(value, where)
    if not pattern.fullmatch(text):
        raise ManifestError(f"{where}: {text!r} is not {what}")
    return text


def _names(
    value: Any,
    where: str,
    pattern: re.Pattern[str],
    what: str,
    *,
    allow_empty: bool = False,
) -> tuple[str, ...]:
    """A list of strings that each fully match ``pattern``."""
    items = _str_list(value, where, allow_empty=allow_empty)
    for index, item in enumerate(items):
        _name(item, f"{where}[{index}]", pattern, what)
    return items


def _pattern(value: Any, where: str) -> re.Pattern[str]:
    """The value must be a regex with a capture group for the version number."""
    text = _str(value, where)
    try:
        compiled = re.compile(text)
    except re.error as exc:
        raise ManifestError(f"{where}: not a valid regular expression: {exc}") from exc
    if compiled.groups < 1:
        raise ManifestError(f"{where}: needs a capture group around the version number")
    return compiled


def _version(value: Any, where: str) -> str:
    """The value must be a version written as dotted numbers, such as "45.2"."""
    return _name(value, where, VERSION_RE, 'a version made of dotted numbers, such as "45.2"')
