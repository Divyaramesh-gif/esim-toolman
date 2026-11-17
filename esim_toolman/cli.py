"""Command-line interface: ``python -m esim_toolman <command>``.

Only ``status`` exists so far. It is read-only: for every tool in the
manifest it finds the program, runs its version command, and prints a table
(or JSON with ``--json``).

Exit codes:
  0  every required tool is OK
  1  at least one required tool is not OK
  2  esim-toolman could not run (bad arguments or a broken manifest)
"""

import argparse
import json
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from esim_toolman.detect import DetectResult, detect_tool
from esim_toolman.manifest import (
    DEFAULT_MANIFEST_PATH,
    Manifest,
    ManifestError,
    Tool,
    load_manifest,
)
from esim_toolman.platform_info import PlatformInfo, detect_platform
from esim_toolman.versions import Status, classify, extract_version

EXIT_OK = 0
EXIT_NOT_OK = 1
EXIT_ERROR = 2


@dataclass(frozen=True)
class ToolStatus:
    """One row of the status report: the tool, what detection found, the verdict."""

    tool: Tool
    detection: DetectResult
    installed: str | None
    status: Status

    @property
    def detail(self) -> str | None:
        """A sentence explaining any status other than a clean OK."""
        version = self.tool.version
        if self.status is Status.NOT_FOUND:
            return self.detection.error
        if self.status is Status.UNKNOWN:
            reason = self.detection.error or "its output has no version number the manifest pattern recognises"
            return f"found, but the version could not be read: {reason}"
        if self.status is Status.TOO_OLD:
            return f"version {self.installed} is older than the minimum {version.minimum}"
        if self.status is Status.BELOW_RECOMMENDED:
            return (
                f"version {self.installed} works but is older than the "
                f"recommended {version.recommended}"
            )
        return self.detection.error  # OK, but the version command still reported a problem

    def to_dict(self) -> dict[str, object]:
        """The row as plain values, for JSON output."""
        return {
            "key": self.tool.key,
            "name": self.tool.name,
            "required": self.tool.required,
            "status": self.status.value,
            "installed_version": self.installed,
            "minimum_version": self.tool.version.minimum,
            "recommended_version": self.tool.version.recommended,
            "path": self.detection.path,
            "problem": self.detection.problem.value if self.detection.problem else None,
            "detail": self.detail,
            "searched": list(self.detection.searched),
        }


def check_tools(manifest: Manifest, info: PlatformInfo) -> list[ToolStatus]:
    """Detect every tool in the manifest and judge its version."""
    results: list[ToolStatus] = []
    for tool in manifest.tools.values():
        detection = detect_tool(tool, info.system)
        installed = (
            extract_version(detection.output, tool.version.pattern) if detection.found else None
        )
        status = classify(
            detection.found, installed, tool.version.minimum, tool.version.recommended
        )
        results.append(ToolStatus(tool, detection, installed, status))
    return results


def required_tools_ok(statuses: Sequence[ToolStatus]) -> bool:
    """True when every required tool has status OK. Optional tools do not count."""
    return all(item.status is Status.OK for item in statuses if item.tool.required)


def format_table(info: PlatformInfo, statuses: Sequence[ToolStatus]) -> str:
    """The plain-text report: platform, warnings, the table, then details."""
    lines = [
        f"Platform: {info.describe()}",
        f"Package managers: {', '.join(info.package_managers) or 'none found'}",
    ]
    lines += [f"Warning: {warning}" for warning in info.warnings]
    lines.append("")

    header = ("TOOL", "STATUS", "INSTALLED", "RECOMMENDED", "PATH")
    rows = [
        (
            item.tool.name + (" *" if item.tool.required else ""),
            item.status.value,
            item.installed or "-",
            item.tool.version.recommended or "-",
            item.detection.path or "-",
        )
        for item in statuses
    ]
    # Pad every column except the last (PATH) to its widest cell.
    widths = [max(len(row[column]) for row in (header, *rows)) for column in range(len(header) - 1)]
    for row in (header, *rows):
        padded = [cell.ljust(width) for cell, width in zip(row, widths)]
        lines.append("  ".join([*padded, row[-1]]).rstrip())

    lines.append("")
    lines.append("* required: the exit code is 0 only if every required tool is OK")
    details = [(item.tool.key, item.detail) for item in statuses if item.detail]
    if details:
        lines.append("")
        lines.append("Details:")
        lines += [f"  {key}: {detail}" for key, detail in details]
    return "\n".join(lines)


def format_json(info: PlatformInfo, statuses: Sequence[ToolStatus]) -> str:
    """The same report as JSON."""
    report = {
        "platform": info.to_dict(),
        "warnings": list(info.warnings),
        "tools": [item.to_dict() for item in statuses],
        "required_tools_ok": required_tools_ok(statuses),
    }
    return json.dumps(report, indent=2)


def run_status(manifest_path: Path, *, as_json: bool) -> int:
    """The ``status`` command. Returns the exit code."""
    try:
        manifest = load_manifest(manifest_path)
    except ManifestError as exc:
        print(f"esim-toolman: {exc}", file=sys.stderr)
        return EXIT_ERROR
    info = detect_platform(manifest.ubuntu_supported_versions)
    statuses = check_tools(manifest, info)
    print(format_json(info, statuses) if as_json else format_table(info, statuses))
    return EXIT_OK if required_tools_ok(statuses) else EXIT_NOT_OK


def build_parser() -> argparse.ArgumentParser:
    """The argument parser: global options, then one sub-command."""
    parser = argparse.ArgumentParser(
        prog="esim-toolman",
        description="Detects, diagnoses, installs and configures eSim's external tools.",
    )
    parser.add_argument(
        "--manifest",
        type=Path,
        default=DEFAULT_MANIFEST_PATH,
        help="tool manifest to use (default: manifest/tools.toml)",
    )
    commands = parser.add_subparsers(dest="command", required=True, metavar="COMMAND")
    status = commands.add_parser(
        "status",
        help="show which tools are installed and whether their versions are OK (read-only)",
    )
    status.add_argument("--json", action="store_true", help="print JSON instead of a table")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Entry point. ``argv`` defaults to the real command line.

    argparse itself exits with code 2 on bad arguments.
    """
    args = build_parser().parse_args(argv)
    if args.command == "status":
        return run_status(args.manifest, as_json=args.json)
    return EXIT_ERROR  # not reached: argparse only accepts known commands
