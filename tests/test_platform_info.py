"""Tests for esim_toolman.platform_info.

The operating system, the os-release file and the PATH lookup are all
faked, so these tests give the same result on any machine.
"""

from pathlib import Path

from captured import UBUNTU_2404 as CAPTURED_UBUNTU_2404

from esim_toolman.platform_info import (
    check_ubuntu_version,
    detect_platform,
    parse_os_release,
)

UBUNTU_2404 = '''\
PRETTY_NAME="Ubuntu 24.04.1 LTS"
NAME="Ubuntu"
VERSION_ID="24.04"
ID=ubuntu
ID_LIKE=debian
'''

SUPPORTED = ("24.04", "26.04")


def write_os_release(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "os-release"
    path.write_text(text, encoding="utf-8")
    return path


def only(*names: str):
    """A fake shutil.which that finds exactly ``names`` on PATH."""
    return lambda name: f"/usr/bin/{name}" if name in names else None


def test_parse_os_release_handles_quotes_comments_and_blank_lines() -> None:
    text = "# a comment\n\nID=ubuntu\nNAME='Ubuntu'\nPRETTY_NAME=\"Ubuntu 24.04 LTS\"\nnot a field\n"
    assert parse_os_release(text) == {
        "ID": "ubuntu",
        "NAME": "Ubuntu",
        "PRETTY_NAME": "Ubuntu 24.04 LTS",
    }


def test_parse_os_release_survives_unbalanced_quotes() -> None:
    assert parse_os_release('NAME="Ubuntu\n')["NAME"] == "Ubuntu"


def test_supported_ubuntu_has_no_warning(tmp_path: Path) -> None:
    info = detect_platform(
        SUPPORTED,
        system="Linux",
        os_release_paths=[write_os_release(tmp_path, UBUNTU_2404)],
        which=only("apt-get"),
    )
    assert info.system == "linux"
    assert (info.distro_id, info.distro_version) == ("ubuntu", "24.04")
    assert info.distro_name == "Ubuntu 24.04.1 LTS"
    assert info.package_managers == ("apt-get",)
    assert info.warnings == ()
    assert info.describe() == "Linux (Ubuntu 24.04.1 LTS)"


def test_real_ubuntu_2404_os_release() -> None:
    # /etc/os-release copied from the WSL Ubuntu 24.04 machine.
    info = detect_platform(
        SUPPORTED,
        system="Linux",
        os_release_paths=[CAPTURED_UBUNTU_2404 / "os-release"],
        which=only("apt-get"),
    )
    assert (info.distro_id, info.distro_version) == ("ubuntu", "24.04")
    assert info.distro_name == "Ubuntu 24.04.5 LTS"
    assert info.warnings == ()


def test_untested_ubuntu_gives_a_warning_not_an_error(tmp_path: Path) -> None:
    text = UBUNTU_2404.replace("24.04", "25.10")
    info = detect_platform(
        SUPPORTED,
        system="Linux",
        os_release_paths=[write_os_release(tmp_path, text)],
        which=only("apt-get"),
    )
    assert info.distro_version == "25.10"
    (warning,) = info.warnings
    assert "Ubuntu 25.10" in warning
    assert "24.04, 26.04" in warning
    assert "untested" in warning


def test_non_ubuntu_linux_gives_a_warning(tmp_path: Path) -> None:
    text = 'ID=debian\nVERSION_ID="12"\nPRETTY_NAME="Debian GNU/Linux 12"\n'
    info = detect_platform(
        SUPPORTED,
        system="Linux",
        os_release_paths=[write_os_release(tmp_path, text)],
        which=only("apt-get"),
    )
    (warning,) = info.warnings
    assert "debian" in warning and "not Ubuntu" in warning


def test_second_os_release_path_is_the_fallback(tmp_path: Path) -> None:
    info = detect_platform(
        SUPPORTED,
        system="Linux",
        os_release_paths=[tmp_path / "missing", write_os_release(tmp_path, UBUNTU_2404)],
        which=only(),
    )
    assert info.distro_version == "24.04"


def test_unreadable_os_release_gives_a_warning(tmp_path: Path) -> None:
    info = detect_platform(
        SUPPORTED, system="Linux", os_release_paths=[tmp_path / "missing"], which=only()
    )
    assert info.distro_id is None
    (warning,) = info.warnings
    assert "os-release" in warning
    assert info.describe() == "Linux (unknown distribution)"


def test_windows_reads_no_os_release_and_finds_winget(tmp_path: Path) -> None:
    info = detect_platform(
        SUPPORTED, system="Windows", os_release_paths=[tmp_path / "missing"], which=only("winget")
    )
    assert info.system == "windows"
    assert info.distro_id is None
    assert info.package_managers == ("winget",)
    assert info.warnings == ()
    assert info.describe() == "Windows"


def test_no_package_manager_found() -> None:
    info = detect_platform(SUPPORTED, system="Windows", which=only())
    assert info.package_managers == ()


def test_other_systems_get_a_warning() -> None:
    info = detect_platform(SUPPORTED, system="Darwin", which=only())
    assert info.system == "darwin"
    (warning,) = info.warnings
    assert "not supported" in warning


def test_check_ubuntu_version_with_empty_tested_list() -> None:
    warning = check_ubuntu_version("ubuntu", "24.04", ())
    assert warning is not None and "(none)" in warning


def test_to_dict_has_the_platform_fields() -> None:
    info = detect_platform(SUPPORTED, system="Windows", which=only("winget"))
    assert info.to_dict() == {
        "system": "windows",
        "distro_id": None,
        "distro_version": None,
        "distro_name": None,
        "package_managers": ["winget"],
    }
