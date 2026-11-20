"""Command-line interface: ``python -m esim_toolman <command>``.

  status   for every tool: is it installed, which version, is that OK?
           Read-only. Exit code 0 if every required tool is OK, otherwise 1.
  doctor   diagnoses problems and suggests a fix for this platform.
           Read-only. Exit code 1 if there is any ERROR finding, otherwise 0.
  install  `install <tool>` or `install --missing`: shows a plan, asks,
           installs, verifies (see installer.py). --dry-run only shows the
           plan. Exit code 0 if everything planned was installed and verified.
  log      prints the last actions from the action log.

status and doctor accept --json. Exit code 2 means esim-toolman could not
run (bad arguments, an unknown tool, or a broken manifest).
"""

import argparse
import json
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from esim_toolman import actionlog, installer
from esim_toolman.detect import DetectResult, detect_tool
from esim_toolman.doctor import (
    PLATFORM,
    Finding,
    Severity,
    grouped,
    run_doctor,
    summarize,
    summary_line,
)
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


def format_status_text(info: PlatformInfo, statuses: Sequence[ToolStatus]) -> str:
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


def format_status_json(info: PlatformInfo, statuses: Sequence[ToolStatus]) -> str:
    """The same report as JSON."""
    report = {
        "platform": info.to_dict(),
        "warnings": list(info.warnings),
        "tools": [item.to_dict() for item in statuses],
        "required_tools_ok": required_tools_ok(statuses),
    }
    return json.dumps(report, indent=2)


def format_doctor_text(manifest: Manifest, info: PlatformInfo, findings: Sequence[Finding]) -> str:
    """The doctor report: findings grouped by tool (already ordered with
    ERRORs first), each with what it breaks and how to fix it, then a
    summary line."""
    lines = [f"Platform: {info.describe()}", "Read-only: the doctor changed nothing.", ""]
    indent = " " * 11  # lines up with the text after the severity label
    for key, group in grouped(findings):
        heading = PLATFORM if key is None else group[0].tool_name
        if key is not None and manifest.tools[key].required:
            heading += " (required)"
        lines.append(heading)
        for finding in group:
            lines.append(f"  {finding.severity.value:<8} {finding.problem}")
            if finding.breaks:
                lines.append(f"{indent}Breaks: {', '.join(finding.breaks)}")
            if finding.fix:
                first, *rest = finding.fix.splitlines()
                lines.append(f"{indent}Fix: {first}")
                lines += [f"{indent}     {line}" for line in rest]
        lines.append("")
    lines.append(summary_line(summarize(findings)))
    return "\n".join(lines)


def format_doctor_json(info: PlatformInfo, findings: Sequence[Finding]) -> str:
    """The same doctor report as JSON."""
    report = {
        "platform": info.to_dict(),
        "findings": [finding.to_dict() for finding in findings],
        "summary": summarize(findings),
    }
    return json.dumps(report, indent=2)


def _load(manifest_path: Path) -> Manifest | None:
    """Load the manifest, or print why it is broken and return None."""
    try:
        return load_manifest(manifest_path)
    except ManifestError as exc:
        print(f"esim-toolman: {exc}", file=sys.stderr)
        return None


def run_status_command(manifest_path: Path, *, as_json: bool) -> int:
    """The ``status`` command. Returns the exit code."""
    manifest = _load(manifest_path)
    if manifest is None:
        return EXIT_ERROR
    info = detect_platform(manifest.ubuntu_supported_versions)
    statuses = check_tools(manifest, info)
    print(format_status_json(info, statuses) if as_json else format_status_text(info, statuses))
    return EXIT_OK if required_tools_ok(statuses) else EXIT_NOT_OK


def run_doctor_command(manifest_path: Path, *, as_json: bool) -> int:
    """The ``doctor`` command. Returns 1 if any finding is an ERROR, else 0."""
    manifest = _load(manifest_path)
    if manifest is None:
        return EXIT_ERROR
    info = detect_platform(manifest.ubuntu_supported_versions)
    findings = run_doctor(manifest, info)
    if as_json:
        print(format_doctor_json(info, findings))
    else:
        print(format_doctor_text(manifest, info, findings))
    has_error = any(finding.severity is Severity.ERROR for finding in findings)
    return EXIT_NOT_OK if has_error else EXIT_OK


def run_install_command(
    manifest_path: Path,
    *,
    tool: str | None,
    missing: bool,
    dry_run: bool,
    yes: bool,
) -> int:
    """The ``install`` command. Returns the installer's exit code."""
    manifest = _load(manifest_path)
    if manifest is None:
        return EXIT_ERROR
    info = detect_platform(manifest.ubuntu_supported_versions)
    machine = installer.make_machine(info)
    try:
        return installer.install(
            manifest, machine, tool_key=tool, missing=missing, dry_run=dry_run, yes=yes
        )
    except KeyboardInterrupt:
        print("\nInterrupted. The action log records what had already run.", file=sys.stderr)
        return 130


def run_log_command(count: int) -> int:
    """The ``log`` command: print the last ``count`` actions."""
    path = actionlog.default_log_path()
    entries, unreadable = actionlog.read_last(count, path)
    if not entries:
        print(f"No actions logged yet ({path}).")
    for entry in entries:
        print(actionlog.format_entry(entry))
    if unreadable:
        print(f"esim-toolman: skipped {unreadable} unreadable line(s) in {path}", file=sys.stderr)
    return EXIT_OK


def _positive_int(text: str) -> int:
    """argparse type: a whole number of 1 or more."""
    try:
        value = int(text)
    except ValueError:
        raise argparse.ArgumentTypeError(f"not a whole number: {text!r}") from None
    if value < 1:
        raise argparse.ArgumentTypeError("must be 1 or more")
    return value


def build_parser() -> argparse.ArgumentParser:
    """The argument parser: global options, then one sub-command."""
    parser = argparse.ArgumentParser(
        prog="esim-toolman",
        description="Checks, diagnoses and installs eSim's external tools.",
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
    doctor = commands.add_parser(
        "doctor",
        help="diagnose problems and suggest fixes for this platform (read-only)",
    )
    doctor.add_argument("--json", action="store_true", help="print JSON instead of text")

    install = commands.add_parser(
        "install",
        help="install a tool, or every missing required tool, after showing a plan",
    )
    target = install.add_mutually_exclusive_group(required=True)
    target.add_argument("tool", nargs="?", help="the tool to install, e.g. iverilog")
    target.add_argument(
        "--missing",
        action="store_true",
        help="install every required tool that is NOT_FOUND or TOO_OLD",
    )
    install.add_argument(
        "--dry-run", action="store_true", help="only print the plan; run nothing"
    )
    install.add_argument(
        "-y", "--yes", action="store_true", help="do not ask for confirmation"
    )

    log = commands.add_parser("log", help="show the last actions esim-toolman ran")
    log.add_argument(
        "-n", "--last", type=_positive_int, default=20, metavar="N",
        help="how many actions to show (default: 20)",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Entry point. ``argv`` defaults to the real command line.

    argparse itself exits with code 2 on bad arguments.
    """
    args = build_parser().parse_args(argv)
    if args.command == "status":
        return run_status_command(args.manifest, as_json=args.json)
    if args.command == "doctor":
        return run_doctor_command(args.manifest, as_json=args.json)
    if args.command == "install":
        return run_install_command(
            args.manifest, tool=args.tool, missing=args.missing, dry_run=args.dry_run, yes=args.yes
        )
    if args.command == "log":
        return run_log_command(args.last)
    return EXIT_ERROR  # not reached: argparse only accepts known commands
