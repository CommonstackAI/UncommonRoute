#!/usr/bin/env python3
"""Synchronize UncommonRoute release version strings."""

from __future__ import annotations

import re
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[4]

REPLACEMENTS: list[tuple[Path, str, str]] = [
    (
        ROOT / "pyproject.toml",
        r'(?m)^version = "[^"]+"$',
        'version = "{version}"',
    ),
    (
        ROOT / "uncommon_route" / "cli.py",
        r'(?m)^VERSION = "[^"]+"$',
        'VERSION = "{version}"',
    ),
    (
        ROOT / "uncommon_route" / "proxy.py",
        r'(?m)^VERSION = "[^"]+"$',
        'VERSION = "{version}"',
    ),
    (
        ROOT / "uncommon_route" / "support.py",
        r'return "[^"]+"',
        'return "{version}"',
    ),
    (
        ROOT / "openclaw-plugin" / "package.json",
        r'(?m)^  "version": "[^"]+",$',
        '  "version": "{version}",',
    ),
    (
        ROOT / "openclaw-plugin" / "src" / "index.js",
        r'(?m)^const VERSION = "[^"]+";$',
        'const VERSION = "{version}";',
    ),
]


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: sync_version.py <semver>", file=sys.stderr)
        return 2
    version = sys.argv[1].strip()
    if not re.fullmatch(r"\d+\.\d+\.\d+", version):
        print(f"invalid semver: {version}", file=sys.stderr)
        return 2

    updated: list[str] = []
    for path, pattern, replacement in REPLACEMENTS:
        text = path.read_text()
        new_text, count = re.subn(pattern, replacement.format(version=version), text, count=1)
        if count != 1:
            print(f"failed to update {path}", file=sys.stderr)
            return 1
        if new_text != text:
            path.write_text(new_text)
            updated.append(str(path.relative_to(ROOT)))

    for rel_path in updated:
        print(rel_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
