"""Tests for esim_toolman.manifest.

Most tests start from VALID_TOML (a small manifest with one made-up tool that
uses every schema field), break one thing, and check that parse_manifest
rejects it with a message naming the right place. The last group checks the
real manifest/tools.toml.
"""

import tomllib
from pathlib import Path
from typing import Any

import pytest

from esim_toolman.manifest import (
    CompileTestCheck,
    ManifestError,
    OutputExcludesCheck,
    load_manifest,
    parse_manifest,
)

VALID_TOML = r'''
schema_version = 1

[platforms.ubuntu]
supported_versions = ["24.04"]

[tools.demo]
name = "Demo"
purpose = "A made-up tool used only in these tests"
required = false
needed_for = ["demo-feature"]
executables = ["demo-cli", "demo"]

[tools.demo.search_paths]
linux = ["~/demo/bin"]
windows = ['%ProgramFiles%\Demo\bin']

[tools.demo.version]
args = ["--version"]
pattern = 'demo (\d+(?:\.\d+)*)'
minimum = "1.0"
recommended = "1.2"
source = "test"

[tools.demo.install.apt]
packages = ["demo"]
fallback = ["demo-lite"]
conflicts = ["demo-broken"]
note = "a note"
source = "test"

[tools.demo.install.winget]
available = true
id = "Demo.Demo"
source = "test"

[[tools.demo.checks]]
kind = "output_excludes"
args = ["--version"]
text = "broken"
ignore_case = true
problem = "it is broken"
fix = "fix it"
source = "test"

[[tools.demo.checks]]
kind = "compile_test"
commands = [["--check", "a.txt"]]
problem = "it cannot compile"
fix = "fix it"
source = "test"

[tools.demo.checks.files]
"a.txt" = "hello\n"
'''


def valid_data() -> dict[str, Any]:
    """A fresh dict of VALID_TOML, so each test can change it freely."""
    return tomllib.loads(VALID_TOML)


def demo(data: dict[str, Any]) -> dict[str, Any]:
    """The [tools.demo] table inside ``data``."""
    return data["tools"]["demo"]


def expect_error(data: dict[str, Any], *fragments: str) -> None:
    """parse_manifest(data) must raise ManifestError mentioning every fragment."""
    with pytest.raises(ManifestError) as caught:
        parse_manifest(data)
    message = str(caught.value)
    for fragment in fragments:
        assert fragment in message, f"{fragment!r} not in {message!r}"


# ---------------------------------------------------------------- valid input


def test_valid_manifest_parses_every_field() -> None:
    manifest = parse_manifest(valid_data())

    assert manifest.schema_version == 1
    assert manifest.ubuntu_supported_versions == ("24.04",)
    tool = manifest.tools["demo"]
    assert tool.key == "demo"
    assert tool.name == "Demo"
    assert tool.required is False
    assert tool.executables == ("demo-cli", "demo")
    assert tool.needed_for == ("demo-feature",)
    assert tool.search_paths.linux == ("~/demo/bin",)
    assert tool.search_paths.windows == (r"%ProgramFiles%\Demo\bin",)

    assert tool.version.args == ("--version",)
    match = tool.version.pattern.search("demo 1.2.3")
    assert match is not None and match.group(1) == "1.2.3"
    assert (tool.version.minimum, tool.version.recommended) == ("1.0", "1.2")

    assert tool.apt is not None
    assert tool.apt.packages == ("demo",)
    assert tool.apt.fallback == ("demo-lite",)
    assert tool.apt.conflicts == ("demo-broken",)
    assert tool.winget is not None
    assert (tool.winget.available, tool.winget.id) == (True, "Demo.Demo")

    first, second = tool.checks
    assert isinstance(first, OutputExcludesCheck)
    assert (first.text, first.ignore_case) == ("broken", True)
    assert isinstance(second, CompileTestCheck)
    assert second.files == (("a.txt", "hello\n"),)
    assert second.commands == (("--check", "a.txt"),)


def test_optional_parts_can_be_left_out() -> None:
    data = valid_data()
    del data["platforms"]
    tool = demo(data)
    for key in ("needed_for", "search_paths", "install", "checks"):
        del tool[key]
    del tool["version"]["minimum"]
    del tool["version"]["recommended"]

    parsed = parse_manifest(data).tools["demo"]

    assert parsed.needed_for == ()
    assert parsed.search_paths.linux == () and parsed.search_paths.windows == ()
    assert parsed.apt is None and parsed.winget is None
    assert parsed.checks == ()
    assert parsed.version.minimum is None and parsed.version.recommended is None


def test_versions_compare_as_numbers_not_text() -> None:
    data = valid_data()
    # As text "10" < "9", but as numbers 9 < 10. Trailing zeros are ignored.
    demo(data)["version"]["minimum"] = "9.0"
    demo(data)["version"]["recommended"] = "10"
    parse_manifest(data)

    demo(data)["version"]["minimum"] = "1.2.0"
    demo(data)["version"]["recommended"] = "1.2"
    parse_manifest(data)


# ---------------------------------------------------------- structure errors


def test_wrong_schema_version() -> None:
    data = valid_data()
    data["schema_version"] = 2
    expect_error(data, "schema_version", "expected 1")


def test_schema_version_true_is_not_1() -> None:
    data = valid_data()
    data["schema_version"] = True
    expect_error(data, "schema_version")


def test_no_tools() -> None:
    data = valid_data()
    data["tools"] = {}
    expect_error(data, "no tools")


def test_missing_required_key_names_its_location() -> None:
    data = valid_data()
    del demo(data)["purpose"]
    expect_error(data, "tools.demo", "missing", "purpose")


def test_unknown_key_is_rejected() -> None:
    data = valid_data()
    demo(data)["version"]["minimun"] = "1.0"  # typo of "minimum"
    expect_error(data, "tools.demo.version", "unknown", "minimun")


def test_bad_tool_key() -> None:
    data = valid_data()
    data["tools"]["Demo Tool"] = data["tools"].pop("demo")
    expect_error(data, "tools.Demo Tool", "lowercase")


@pytest.mark.parametrize(
    ("key", "bad_value", "location"),
    [
        ("required", "yes", "tools.demo.required"),
        ("name", "", "tools.demo.name"),
        ("executables", "demo", "tools.demo.executables"),
        ("executables", [], "tools.demo.executables"),
        ("needed_for", ["Demo Feature"], "tools.demo.needed_for[0]"),
    ],
)
def test_wrong_value_types(key: str, bad_value: Any, location: str) -> None:
    data = valid_data()
    demo(data)[key] = bad_value
    expect_error(data, location)


def test_executable_must_not_include_a_folder() -> None:
    data = valid_data()
    demo(data)["executables"] = ["/usr/bin/demo"]
    expect_error(data, "tools.demo.executables[0]", "plain program name")


def test_ubuntu_release_format() -> None:
    data = valid_data()
    data["platforms"]["ubuntu"]["supported_versions"] = ["noble"]
    expect_error(data, "platforms.ubuntu.supported_versions[0]")


# ------------------------------------------------------------ version errors


def test_invalid_regex() -> None:
    data = valid_data()
    demo(data)["version"]["pattern"] = "demo ("
    expect_error(data, "tools.demo.version.pattern", "not a valid regular expression")


def test_regex_needs_a_capture_group() -> None:
    data = valid_data()
    demo(data)["version"]["pattern"] = r"demo \d+"
    expect_error(data, "tools.demo.version.pattern", "capture group")


def test_version_must_be_dotted_numbers() -> None:
    data = valid_data()
    demo(data)["version"]["minimum"] = "1.x"
    expect_error(data, "tools.demo.version.minimum", "dotted numbers")


def test_version_written_as_a_toml_number_is_rejected() -> None:
    data = valid_data()
    demo(data)["version"]["minimum"] = 1.0
    expect_error(data, "tools.demo.version.minimum")


def test_minimum_above_recommended() -> None:
    data = valid_data()
    demo(data)["version"]["minimum"] = "2.0"
    expect_error(data, "tools.demo.version", "higher than recommended")


def test_version_args_must_not_be_empty() -> None:
    data = valid_data()
    demo(data)["version"]["args"] = []
    expect_error(data, "tools.demo.version.args", "must not be empty")


# ------------------------------------------------------------ install errors


def test_bad_apt_package_name() -> None:
    data = valid_data()
    demo(data)["install"]["apt"]["packages"] = ["demo; rm -rf /"]
    expect_error(data, "tools.demo.install.apt.packages[0]", "apt package name")


def test_conflict_also_listed_to_install() -> None:
    data = valid_data()
    demo(data)["install"]["apt"]["conflicts"] = ["demo-lite"]
    expect_error(data, "tools.demo.install.apt.conflicts", "demo-lite", "also listed")


def test_winget_available_needs_an_id() -> None:
    data = valid_data()
    del demo(data)["install"]["winget"]["id"]
    expect_error(data, "tools.demo.install.winget", "'id' is required")


def test_winget_unavailable_must_not_have_an_id() -> None:
    data = valid_data()
    winget = demo(data)["install"]["winget"]
    winget["available"] = False
    winget["note"] = "install it by hand"
    expect_error(data, "tools.demo.install.winget", "'id' must not be set")


def test_winget_unavailable_needs_a_manual_fix_note() -> None:
    data = valid_data()
    winget = demo(data)["install"]["winget"]
    winget["available"] = False
    del winget["id"]
    expect_error(data, "tools.demo.install.winget", "'note' is required")


def test_winget_unavailable_with_note_is_valid() -> None:
    data = valid_data()
    winget = demo(data)["install"]["winget"]
    winget["available"] = False
    del winget["id"]
    winget["note"] = "install it by hand"
    parsed = parse_manifest(data).tools["demo"].winget
    assert parsed is not None
    assert (parsed.available, parsed.id, parsed.note) == (False, None, "install it by hand")


# -------------------------------------------------------------- check errors


def test_unknown_check_kind() -> None:
    data = valid_data()
    demo(data)["checks"][0]["kind"] = "smoke"
    expect_error(data, "tools.demo.checks[0].kind", "unknown check kind", "compile_test")


def test_check_without_kind() -> None:
    data = valid_data()
    del demo(data)["checks"][0]["kind"]
    expect_error(data, "tools.demo.checks[0]", "kind")


def test_check_keys_depend_on_kind() -> None:
    data = valid_data()
    # "files" belongs to compile_test, not output_excludes.
    demo(data)["checks"][0]["files"] = {"a.txt": "x"}
    expect_error(data, "tools.demo.checks[0]", "unknown", "files")


def test_compile_test_file_name_must_not_leave_the_temp_folder() -> None:
    data = valid_data()
    demo(data)["checks"][1]["files"] = {"../a.txt": "x"}
    expect_error(data, "tools.demo.checks[1].files", "plain file name")


def test_compile_test_needs_commands() -> None:
    data = valid_data()
    demo(data)["checks"][1]["commands"] = []
    expect_error(data, "tools.demo.checks[1].commands", "non-empty list")


# ------------------------------------------------------------- load_manifest


def test_load_missing_file(tmp_path: Path) -> None:
    missing = tmp_path / "nope.toml"
    with pytest.raises(ManifestError, match="cannot read file"):
        load_manifest(missing)


def test_load_invalid_toml(tmp_path: Path) -> None:
    path = tmp_path / "bad.toml"
    path.write_text("schema_version = = 1\n", encoding="utf-8")
    with pytest.raises(ManifestError, match="not valid TOML"):
        load_manifest(path)


def test_load_error_starts_with_the_file_path(tmp_path: Path) -> None:
    path = tmp_path / "tools.toml"
    path.write_text(VALID_TOML.replace("schema_version = 1", "schema_version = 2"),
                    encoding="utf-8")
    with pytest.raises(ManifestError) as caught:
        load_manifest(path)
    assert str(caught.value).startswith(str(path))
    assert "schema_version" in str(caught.value)


# --------------------------------------------------- the real manifest file


def test_real_manifest_lists_the_five_tools() -> None:
    manifest = load_manifest()
    assert list(manifest.tools) == ["ngspice", "kicad", "ghdl", "verilator", "iverilog"]
    for tool in manifest.tools.values():
        assert tool.apt is not None, f"{tool.key} has no apt section"
        assert tool.winget is not None, f"{tool.key} has no winget section"


def test_real_manifest_winget_ids_are_only_the_confirmed_ones() -> None:
    # Only these were confirmed with `winget show` (docs/esim-dependencies.md).
    manifest = load_manifest()
    available = {
        key: tool.winget.id
        for key, tool in manifest.tools.items()
        if tool.winget is not None and tool.winget.available
    }
    assert available == {"kicad": "KiCad.KiCad", "iverilog": "Icarus.Verilog"}


def test_real_manifest_ghdl_checks_and_conflicts() -> None:
    ghdl = load_manifest().tools["ghdl"]
    assert ghdl.apt is not None and ghdl.apt.conflicts == ("ghdl-mcode",)
    kinds = [check.kind for check in ghdl.checks]
    assert kinds == ["output_excludes", "compile_test"]
    compile_test = ghdl.checks[1]
    assert isinstance(compile_test, CompileTestCheck)
    assert compile_test.files[0][0] == "t.vhdl"


@pytest.mark.parametrize(
    ("key", "sample_output", "expected"),
    [
        # Format string "** %s-%s : %s" from ngspice src/frontend/misccoms.c.
        ("ngspice", "** ngspice-45.2 : Circuit level simulation program", "45.2"),
        # eSim's build checks that kicad-cli's output contains "9.0.3".
        ("kicad", "9.0.3", "9.0.3"),
        # The samples below follow each tool's usual output; they are not
        # captured from a real run (see docs/esim-dependencies.md).
        ("ghdl", "GHDL 4.1.0 (Ubuntu 4.1.0+dfsg-0ubuntu2.1) [Dunoon edition]", "4.1.0"),
        ("verilator", "Verilator 5.020 2024-01-01 rev (Debian 5.020-1)", "5.020"),
        ("iverilog", "Icarus Verilog version 12.0 (stable) (v12_0)", "12.0"),
    ],
)
def test_real_manifest_version_patterns(key: str, sample_output: str, expected: str) -> None:
    pattern = load_manifest().tools[key].version.pattern
    match = pattern.search(sample_output)
    assert match is not None and match.group(1) == expected
