"""Runtime package version resolution."""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version as metadata_version
from pathlib import Path
import tomllib


def _version_from_pyproject() -> str | None:
    pyproject_path = Path(__file__).resolve().parents[1] / "pyproject.toml"
    if not pyproject_path.exists():
        return None
    try:
        with pyproject_path.open("rb") as file:
            payload = tomllib.load(file)
    except (OSError, tomllib.TOMLDecodeError):
        return None

    project = payload.get("project")
    if not isinstance(project, dict):
        return None
    raw_version = project.get("version")
    if not isinstance(raw_version, str):
        return None
    version = raw_version.strip()
    return version or None


def get_version() -> str:
    """Return the package version from the active source of truth."""
    source_version = _version_from_pyproject()
    if source_version is not None:
        return source_version
    try:
        return metadata_version("uncommon-route")
    except PackageNotFoundError:
        return "0.0.0"


VERSION = get_version()

