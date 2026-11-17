"""Tests for esim_toolman.versions (pure functions, no mocking needed)."""

import re

import pytest

from esim_toolman.versions import (
    Status,
    classify,
    compare_versions,
    extract_version,
    version_key,
)


@pytest.mark.parametrize(
    ("left", "right", "expected"),
    [
        ("10", "9", 1),  # numbers, not text: as text "10" < "9"
        ("9.0", "9", 0),  # trailing zeros do not matter
        ("9.0.3", "9", 1),
        ("8.0.4", "9", -1),
        ("5.020", "5.0", 1),  # Verilator style: 5.020 means 5.20
        ("45.2", "42", 1),
        ("12.0", "12", 0),
    ],
)
def test_compare_versions(left: str, right: str, expected: int) -> None:
    assert compare_versions(left, right) == expected


def test_version_key_drops_trailing_zeros() -> None:
    assert version_key("9.0.0") == (9,)
    assert version_key("0") == (0,)


@pytest.mark.parametrize("bad", ["", "9.x", "v9", "9..0", "1.2-dev"])
def test_version_key_rejects_non_numeric(bad: str) -> None:
    with pytest.raises(ValueError):
        version_key(bad)


def test_extract_version_takes_group_one() -> None:
    pattern = re.compile(r"ngspice-(\d+(?:\.\d+)*)")
    output = "******\n** ngspice-45.2 : Circuit level simulation program\n"
    assert extract_version(output, pattern) == "45.2"


def test_extract_version_no_match_is_none() -> None:
    assert extract_version("command not understood", re.compile(r"demo (\d+)")) is None


def test_extract_version_non_numeric_capture_is_none() -> None:
    # A pattern that captures too much must give "unreadable", not a crash.
    assert extract_version("demo v1.2-beta", re.compile(r"demo v(\S+)")) is None


@pytest.mark.parametrize(
    ("found", "installed", "minimum", "recommended", "expected"),
    [
        (False, None, "1", "2", Status.NOT_FOUND),
        (True, None, "1", "2", Status.UNKNOWN),
        (True, "0.9", "1", "2", Status.TOO_OLD),
        (True, "1.5", "1", "2", Status.BELOW_RECOMMENDED),
        (True, "1", "1", "2", Status.BELOW_RECOMMENDED),  # exactly the minimum
        (True, "2", "1", "2", Status.OK),  # exactly the recommended version
        (True, "10.0.8", "7", "9", Status.OK),  # newer than recommended is OK
        (True, "3", None, None, Status.OK),  # no limits in the manifest
        (True, "0.1", None, "2", Status.BELOW_RECOMMENDED),  # no minimum set
    ],
)
def test_classify(
    found: bool,
    installed: str | None,
    minimum: str | None,
    recommended: str | None,
    expected: Status,
) -> None:
    assert classify(found, installed, minimum, recommended) is expected
