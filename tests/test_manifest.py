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
from captured import WINDOWS_11, load, output

from esim_toolman.manifest import (
    CompileTestCheck,
    ManifestError,
    OutputExcludesCheck,
    OwnBuildCheck,
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
version = "1.2.0"
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
fix_linux = "sudo apt-get install demo-lite"
source = "test"

[tools.demo.checks.files]
"a.txt" = "hello\n"

[[tools.demo.checks]]
kind = "own_build"
paths = ["~/own-demo"]
breaks = ["demo-cosim"]
found_note = "this is the own build"
problem = "this is not the own build"
fix = "install the own build"
fix_windows = "run the own installer"
source = "test"
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
    assert tool.winget.version == "1.2.0"

    first, second, third = tool.checks
    assert isinstance(first, OutputExcludesCheck)
    assert (first.text, first.ignore_case) == ("broken", True)
    assert (first.fix_linux, first.fix_windows) == (None, None)
    assert isinstance(second, CompileTestCheck)
    assert second.files == (("a.txt", "hello\n"),)
    assert second.commands == (("--check", "a.txt"),)
    assert second.fix_linux == "sudo apt-get install demo-lite"
    assert isinstance(third, OwnBuildCheck)
    assert third.paths == ("~/own-demo",)
    assert third.breaks == ("demo-cosim",)
    assert third.found_note == "this is the own build"
    assert third.fix_windows == "run the own installer"


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


def test_winget_unavailable_must_not_pin_a_version() -> None:
    data = valid_data()
    winget = demo(data)["install"]["winget"]
    winget["available"] = False
    del winget["id"]
    winget["note"] = "install it by hand"
    expect_error(data, "tools.demo.install.winget", "'version' must not be set")


def test_winget_unavailable_with_note_is_valid() -> None:
    data = valid_data()
    winget = demo(data)["install"]["winget"]
    winget["available"] = False
    del winget["id"]
    del winget["version"]
    winget["note"] = "install it by hand"
    parsed = parse_manifest(data).tools["demo"].winget
    assert parsed is not None
    assert (parsed.available, parsed.id, parsed.version) == (False, None, None)
    assert parsed.note == "install it by hand"


def test_winget_version_is_optional() -> None:
    data = valid_data()
    del demo(data)["install"]["winget"]["version"]
    parsed = parse_manifest(data).tools["demo"].winget
    assert parsed is not None and parsed.version is None


def test_winget_version_must_be_a_plain_version() -> None:
    data = valid_data()
    demo(data)["install"]["winget"]["version"] = "9.0.3 --force"
    expect_error(data, "tools.demo.install.winget.version", "winget version")


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


def test_own_build_needs_paths() -> None:
    data = valid_data()
    del demo(data)["checks"][2]["paths"]
    expect_error(data, "tools.demo.checks[2]", "missing", "paths")


def test_own_build_breaks_must_be_feature_names() -> None:
    data = valid_data()
    demo(data)["checks"][2]["breaks"] = ["Demo Co-Sim"]
    expect_error(data, "tools.demo.checks[2].breaks[0]", "feature name")


def test_platform_fix_must_be_a_string() -> None:
    data = valid_data()
    demo(data)["checks"][0]["fix_linux"] = ["sudo", "apt-get"]
    expect_error(data, "tools.demo.checks[0].fix_linux")


def test_unknown_platform_fix_key() -> None:
    data = valid_data()
    demo(data)["checks"][0]["fix_macos"] = "brew install demo"
    expect_error(data, "tools.demo.checks[0]", "unknown", "fix_macos")


# --------------------------------------------------------------------- repair


def test_repair_is_optional_and_off_by_default() -> None:
    tool = parse_manifest(valid_data()).tools["demo"]
    assert [check.repair for check in tool.checks] == [None, None, None]


def test_repair_fallback_is_read() -> None:
    data = valid_data()
    demo(data)["checks"][1]["repair"] = "fallback"
    tool = parse_manifest(data).tools["demo"]
    assert tool.checks[1].repair == "fallback"


def test_unknown_repair_is_rejected() -> None:
    data = valid_data()
    demo(data)["checks"][1]["repair"] = "sudo apt-get install demo-lite"
    expect_error(data, "tools.demo.checks[1].repair", "unknown repair", "fallback")


def test_repair_fallback_needs_apt_fallback_packages() -> None:
    # Without fallback packages there is nothing to repair with.
    data = valid_data()
    demo(data)["checks"][1]["repair"] = "fallback"
    del demo(data)["install"]["apt"]["fallback"]
    expect_error(data, "tools.demo.checks[1].repair", "install.apt.fallback")


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


def test_real_manifest_pins_kicad_9_for_winget() -> None:
    # eSim 2.6 is tested with KiCad 9.0.3; winget's latest is KiCad 10.
    kicad = load_manifest().tools["kicad"]
    assert kicad.winget is not None and kicad.winget.version == "9.0.3"
    assert kicad.version.recommended == "9"


def test_real_manifest_ghdl_checks_and_conflicts() -> None:
    ghdl = load_manifest().tools["ghdl"]
    assert ghdl.apt is not None and ghdl.apt.conflicts == ("ghdl-mcode",)
    kinds = [check.kind for check in ghdl.checks]
    assert kinds == ["output_excludes", "compile_test"]
    compile_test = ghdl.checks[1]
    assert isinstance(compile_test, CompileTestCheck)
    assert compile_test.files[0][0] == "t.vhdl"
    assert compile_test.fix_linux == "sudo apt-get install ghdl-gcc"
    # `install` repairs a failed compile test with the apt fallback (ghdl-gcc);
    # a failed mcode check is not repaired automatically.
    assert compile_test.repair == "fallback"
    assert ghdl.checks[0].repair is None
    assert ghdl.apt.fallback == ("ghdl-gcc",)


def test_real_manifest_ngspice_own_build_check() -> None:
    ngspice = load_manifest().tools["ngspice"]
    (check,) = ngspice.checks
    assert isinstance(check, OwnBuildCheck)
    assert "~/nghdl-simulator" in check.paths
    # Ubuntu's ngspice has no ghdl.cm, Ngveri.cm or ivlng (checked on 24.04).
    assert check.breaks == ("vhdl-cosim", "verilog-models", "verilog-cosim")
    # Every feature an own_build check says it breaks must be a feature some
    # tool in the manifest provides.
    features = {f for tool in load_manifest().tools.values() for f in tool.needed_for}
    assert set(check.breaks) <= features


@pytest.mark.parametrize(
    ("key", "captured_run", "expected"),
    [
        ("ngspice", "ngspice-version", "42"),
        ("kicad", "kicad-cli-version", "7.0.11"),
        ("ghdl", "ghdl-version", "4.1.0"),
        ("verilator", "verilator-version", "5.020"),
        ("iverilog", "iverilog-version", "12.0"),
    ],
)
def test_version_patterns_on_real_ubuntu_2404_output(key: str, captured_run: str, expected: str) -> None:
    # Outputs captured on Ubuntu 24.04 (tests/fixtures/ubuntu-24.04/).
    tool = load_manifest().tools[key]
    run = load(captured_run)
    assert run["command"][1:] == list(tool.version.args)  # the manifest's own command
    assert run["exit_code"] == 0
    match = tool.version.pattern.search(output(captured_run))
    assert match is not None and match.group(1) == expected


def test_iverilog_pattern_on_real_windows_output() -> None:
    # Bleyer's Icarus build, installed by a real `winget install` on Windows 11.
    tool = load_manifest().tools["iverilog"]
    run = load("iverilog-version", WINDOWS_11)
    assert run["command"][1:] == list(tool.version.args)
    assert run["exit_code"] == 0  # -V exits with 0 on this build too
    match = tool.version.pattern.search(output("iverilog-version", WINDOWS_11))
    assert match is not None and match.group(1) == "12.0"


def test_real_manifest_searches_bleyers_icarus_folder_on_windows() -> None:
    # A real winget install put Icarus in C:\iverilog\bin and did not add it
    # to PATH; eSim's own build is still searched first.
    tool = load_manifest().tools["iverilog"]
    assert tool.search_paths.windows == (
        r"%SystemDrive%\FOSSEE\eSim\library\bin\iverilog\bin",
        r"%SystemDrive%\iverilog\bin",
    )


def test_ngspice_pattern_on_esims_own_build_format() -> None:
    # eSim's own ngspice 45.2 was not available to capture; this follows the
    # format string "** %s-%s : %s" in ngspice's src/frontend/misccoms.c.
    pattern = load_manifest().tools["ngspice"].version.pattern
    match = pattern.search("** ngspice-45.2 : Circuit level simulation program")
    assert match is not None and match.group(1) == "45.2"


def test_real_manifest_iverilog_own_build_check() -> None:
    (check,) = load_manifest().tools["iverilog"].checks
    assert isinstance(check, OwnBuildCheck)
    assert "~/nghdl-simulator/iverilog" in check.paths
    assert check.breaks == ("verilog-cosim",)


def test_user_facing_notes_hold_no_developer_todos() -> None:
    # A rule for esim-toolman's own developers once sat in a note that the
    # doctor shows to users; such rules belong in TOML comments.
    for tool in load_manifest().tools.values():
        for note in (tool.apt.note if tool.apt else None, tool.winget.note if tool.winget else None):
            assert note is None or "not written" not in note, tool.key
