from __future__ import annotations

import getpass
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import TextIO


@dataclass(frozen=True, slots=True)
class MenuOption:
    value: str
    label: str
    hint: str = ""


class TerminalPrompter:
    def __init__(
        self,
        *,
        stdin: TextIO | None = None,
        stdout: TextIO | None = None,
        env: dict[str, str] | None = None,
    ) -> None:
        self.stdin = stdin or sys.stdin
        self.stdout = stdout or sys.stdout
        self.env = env or dict(os.environ)
        self._interactive = (
            bool(getattr(self.stdin, "isatty", lambda: False)())
            and bool(getattr(self.stdout, "isatty", lambda: False)())
            and self.env.get("TERM", "").lower() != "dumb"
            and not self.env.get("CI")
        )

    @property
    def interactive(self) -> bool:
        return self._interactive

    def choose(
        self,
        prompt: str,
        options: list[MenuOption],
        *,
        default: int = 0,
    ) -> MenuOption:
        if not options:
            raise ValueError("at least one option is required")
        if self._interactive:
            return self._choose_tty(prompt, options, default=default)
        return self._choose_numbered(prompt, options, default=default)

    def confirm(self, prompt: str, *, default: bool = True) -> bool:
        suffix = "[Y/n]" if default else "[y/N]"
        while True:
            self.stdout.write(f"{prompt} {suffix} ")
            self.stdout.flush()
            answer = self.stdin.readline()
            if answer == "":
                return default
            clean = answer.strip().lower()
            if not clean:
                return default
            if clean in {"y", "yes"}:
                return True
            if clean in {"n", "no"}:
                return False
            self.stdout.write("Please answer y or n.\n")

    def ask(
        self,
        prompt: str,
        *,
        default: str = "",
        secret: bool = False,
        allow_empty: bool = False,
    ) -> str:
        while True:
            display_default = f" [{default}]" if default else ""
            full_prompt = f"{prompt}{display_default}: "
            if secret and self._interactive:
                answer = getpass.getpass(full_prompt)
            else:
                self.stdout.write(full_prompt)
                self.stdout.flush()
                answer = self.stdin.readline()
                if answer == "":
                    answer = ""
                else:
                    answer = answer.rstrip("\n")
            clean = answer.strip()
            if not clean and default:
                clean = default
            if clean or allow_empty:
                return clean
            self.stdout.write("This value is required.\n")

    def _choose_numbered(
        self,
        prompt: str,
        options: list[MenuOption],
        *,
        default: int = 0,
    ) -> MenuOption:
        self.stdout.write(f"{prompt}\n")
        for index, option in enumerate(options, start=1):
            default_tag = " (default)" if index - 1 == default else ""
            hint = f" — {option.hint}" if option.hint else ""
            self.stdout.write(f"  {index}. {option.label}{default_tag}{hint}\n")
        self.stdout.flush()

        while True:
            self.stdout.write(f"Select an option [{default + 1}]: ")
            self.stdout.flush()
            answer = self.stdin.readline()
            if answer == "":
                return options[default]
            clean = answer.strip()
            if not clean:
                return options[default]
            if clean.isdigit():
                index = int(clean) - 1
                if 0 <= index < len(options):
                    return options[index]
            self.stdout.write("Enter one of the numbers above.\n")

    def _choose_tty(
        self,
        prompt: str,
        options: list[MenuOption],
        *,
        default: int = 0,
    ) -> MenuOption:
        index = max(0, min(default, len(options) - 1))
        lines = self._menu_lines(prompt, options, index)
        block_height = len(lines)
        self.stdout.write("\x1b[?25l")
        self.stdout.write("\n".join(lines))
        self.stdout.write("\n")
        self.stdout.flush()

        try:
            while True:
                key = self._read_key()
                if key == "up":
                    index = (index - 1) % len(options)
                elif key == "down":
                    index = (index + 1) % len(options)
                elif key == "enter":
                    self.stdout.write("\x1b[?25h")
                    self.stdout.flush()
                    return options[index]
                elif key == "interrupt":
                    raise KeyboardInterrupt
                else:
                    continue

                lines = self._menu_lines(prompt, options, index)
                self.stdout.write(f"\x1b[{block_height}F")
                for line in lines:
                    self.stdout.write("\x1b[2K")
                    self.stdout.write(line)
                    self.stdout.write("\n")
                self.stdout.flush()
        finally:
            self.stdout.write("\x1b[?25h")
            self.stdout.flush()

    def _menu_lines(
        self,
        prompt: str,
        options: list[MenuOption],
        selected_index: int,
    ) -> list[str]:
        lines = [prompt, ""]
        for index, option in enumerate(options):
            prefix = "›" if index == selected_index else " "
            label = option.label
            if option.hint:
                label = f"{label} — {option.hint}"
            lines.append(f"  {prefix} {label}")
        lines.append("")
        lines.append("  Use ↑/↓ to move, Enter to select.")
        return lines

    def _read_key(self) -> str:
        if os.name == "nt":
            import msvcrt

            first = msvcrt.getch()
            if first in {b"\x00", b"\xe0"}:
                second = msvcrt.getch()
                if second == b"H":
                    return "up"
                if second == b"P":
                    return "down"
                return "other"
            if first in {b"\r", b"\n"}:
                return "enter"
            if first == b"\x03":
                return "interrupt"
            return "other"

        import termios
        import tty

        fd = self.stdin.fileno()
        old_settings = termios.tcgetattr(fd)
        try:
            tty.setraw(fd)
            first = self.stdin.read(1)
            if first == "\x1b":
                second = self.stdin.read(1)
                third = self.stdin.read(1)
                if second == "[" and third == "A":
                    return "up"
                if second == "[" and third == "B":
                    return "down"
                return "other"
            if first in {"\r", "\n"}:
                return "enter"
            if first == "\x03":
                return "interrupt"
            return "other"
        finally:
            termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)


def shell_quote(value: str) -> str:
    escaped = (
        value.replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("$", "\\$")
        .replace("`", "\\`")
    )
    return f'"{escaped}"'


def upsert_shell_block(path: Path, block_name: str, lines: list[str]) -> Path:
    start_marker = f"# >>> uncommon-route {block_name} >>>"
    end_marker = f"# <<< uncommon-route {block_name} <<<"
    block_lines = [start_marker, "# Added by `uncommon-route init`.", *lines, end_marker]
    block = "\n".join(block_lines)

    existing = path.read_text() if path.exists() else ""
    if start_marker in existing and end_marker in existing:
        start = existing.index(start_marker)
        end = existing.index(end_marker, start) + len(end_marker)
        updated = f"{existing[:start].rstrip()}\n\n{block}\n{existing[end:].lstrip()}"
    else:
        separator = "\n\n" if existing.strip() else ""
        updated = f"{existing.rstrip()}{separator}{block}\n"

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(updated)
    return path
