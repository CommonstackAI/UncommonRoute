from __future__ import annotations

from pathlib import Path
import tomllib

from uncommon_route import cli, proxy, support
from uncommon_route.version import VERSION, get_version


def test_runtime_version_matches_pyproject() -> None:
    with (Path(__file__).resolve().parents[1] / "pyproject.toml").open("rb") as file:
        pyproject = tomllib.load(file)

    assert VERSION == pyproject["project"]["version"]
    assert get_version() == VERSION


def test_version_surfaces_use_same_source() -> None:
    assert cli.VERSION == VERSION
    assert proxy.VERSION == VERSION
    assert support._package_version() == VERSION

