"""Textual TUI for `uncommon-route init`.

Layout:

    ┌──────────────┬───────────────────────────────────┬───────────────┐
    │ Steps        │ Active step content               │ Will write    │
    │  1. Welcome  │  (form fields / radios / etc.)    │  (live plan)  │
    │  2. Upstream │                                   │               │
    │  3. BYOK     │                                   │               │
    │  4. Client   │                                   │               │
    │  5. Review   │                                   │               │
    │  6. Done     │                                   │               │
    └──────────────┴───────────────────────────────────┴───────────────┘

The app collects answers into `InitAnswers` (defined in init_plan.py),
shows a live preview of the plan in the right pane, and applies the
plan in a worker on the final step.
"""

from __future__ import annotations

from pathlib import Path
from typing import Callable

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Center, Container, Horizontal, Middle, Vertical, VerticalScroll
from textual.reactive import reactive
from textual.screen import Screen
from textual.theme import Theme
from textual.widgets import (
    Button,
    Checkbox,
    ContentSwitcher,
    Footer,
    Header,
    Input,
    Label,
    RadioButton,
    RadioSet,
    Static,
)

from uncommon_route.version import VERSION


# Figma palette — deep indigo brand color + cool purple ramp
PAL_DEEP_INDIGO = "#1B1B7E"
PAL_MID_PURPLE = "#6E72A6"
PAL_LIGHT_PURPLE = "#B7BCD5"
PAL_LAVENDER = "#E5E7F2"

UNCOMMON_ROUTE_THEME = Theme(
    name="uncommon-route",
    primary=PAL_DEEP_INDIGO,
    secondary=PAL_MID_PURPLE,
    accent=PAL_LIGHT_PURPLE,
    foreground="#FFFFFF",
    background="#06061A",
    surface="#10103A",
    panel="#16163C",
    success=PAL_LIGHT_PURPLE,  # done state — match brand instead of generic green
    warning="#E0BB5C",
    error="#E07878",
    dark=True,
    variables={
        "text-muted": PAL_LIGHT_PURPLE,
        "text-disabled": PAL_MID_PURPLE,
    },
)

from uncommon_route.connections_store import ConnectionsStore
from uncommon_route.tui.init_plan import (
    CLIENT_CHOICES,
    CONNECTION_CHOICES,
    KNOWN_PROVIDERS,
    ApplyResult,
    InitAnswers,
    apply_plan,
    build_plan,
    validate,
)


_BANNER_GLYPHS = (
    "██   ██   ██████ ",
    "██   ██   ██   ██",
    "██   ██   ██████ ",
    "██   ██   ██   ██",
    " █████    ██   ██",
)


def _render_banner(color: str) -> str:
    return "\n".join(f"[bold {color}]{line}[/]" for line in _BANNER_GLYPHS)


SPLASH_BANNER = _render_banner("#FFFFFF") + f"\n\n[{PAL_LIGHT_PURPLE}]ROUTE  ·  local llm router[/]"
DONE_BANNER = _render_banner("$accent")


class SplashScreen(Screen):
    """Brief launch screen — auto-dismisses after a beat or on any key."""

    DEFAULT_CSS = f"""
    SplashScreen {{
        align: center middle;
        background: {PAL_DEEP_INDIGO};
    }}

    #splash-banner {{
        text-align: center;
        padding: 1 4;
    }}

    #splash-version {{
        text-align: center;
        color: {PAL_LIGHT_PURPLE};
        margin-top: 1;
    }}

    #splash-hint {{
        text-align: center;
        color: {PAL_MID_PURPLE};
        margin-top: 2;
    }}
    """

    AUTO_DISMISS_SECONDS = 1.6

    def compose(self) -> ComposeResult:
        with Middle():
            with Center():
                yield Static(SPLASH_BANNER, id="splash-banner")
            with Center():
                yield Static(f"v{VERSION}", id="splash-version")
            with Center():
                yield Static("press any key…", id="splash-hint")

    def on_mount(self) -> None:
        self._timer = self.set_timer(self.AUTO_DISMISS_SECONDS, self._auto_dismiss)

    def _auto_dismiss(self) -> None:
        if self.is_active:
            self.dismiss()

    def on_key(self, event) -> None:
        self._timer.stop()
        self.dismiss()


STEPS: list[tuple[str, str]] = [
    ("welcome", "Welcome"),
    ("upstream", "Upstream"),
    ("byok", "BYOK keys"),
    ("client", "Client"),
    ("review", "Review & apply"),
    ("done", "Done"),
]


CSS = """
Screen {
    layout: horizontal;
}

#sidebar {
    width: 26;
    background: $panel;
    padding: 1 2;
    border-right: solid $primary;
}

#sidebar-title {
    text-style: bold;
    color: $accent;
    margin-bottom: 1;
}

.step-row {
    padding: 0 1;
    color: $text-muted;
    height: 1;
}

.step-row.active {
    color: $text;
    text-style: bold;
}

.step-row.done {
    color: $success;
}

#main {
    width: 1fr;
    padding: 1 2;
}

#preview {
    width: 40;
    background: $boost;
    padding: 1 2;
    border-left: solid $primary;
}

.step-title {
    text-style: bold;
    color: $accent;
    margin-bottom: 1;
}

.muted {
    color: $text-muted;
}

.success {
    color: $success;
}

.warn {
    color: $warning;
}

.error {
    color: $error;
}

.section-label {
    margin-top: 1;
    color: $text-muted;
}

RadioSet {
    margin-bottom: 1;
}

Input {
    margin-bottom: 1;
}

Checkbox {
    margin-bottom: 0;
}

Button {
    margin: 0 1 0 0;
}

.step-next {
    margin-top: 1;
}

.step-problems {
    margin-top: 1;
    color: $warning;
}

#preview-list {
    height: 1fr;
}

#problems {
    margin-top: 1;
    color: $warning;
}

#done-banner {
    margin-bottom: 1;
}

#done-summary {
    margin-top: 1;
    margin-bottom: 1;
}

#welcome-banner {
    margin-bottom: 1;
}

#welcome-tagline {
    margin-bottom: 1;
    color: $accent;
}

#welcome-intro {
    margin-bottom: 1;
}
"""


class InitApp(App):
    """Stepper-style wizard for uncommon-route init."""

    CSS = CSS
    TITLE = "uncommon-route init"
    BINDINGS = [
        Binding("ctrl+n", "next_step", "Next", priority=True),
        Binding("ctrl+p", "prev_step", "Prev", priority=True),
        Binding("ctrl+q", "quit", "Quit", priority=True),
    ]

    step_index: reactive[int] = reactive(0)

    def __init__(
        self,
        *,
        rc_path: Path,
        rc_display: str,
        render_exports: Callable[[str, int], list[str]],
        start_background_proxy: Callable[..., tuple[bool, str]],
        port: int = 8403,
    ) -> None:
        super().__init__()
        self._rc_path = rc_path
        self._rc_display = rc_display
        self._render_exports = render_exports
        self._start_background_proxy = start_background_proxy
        self.answers = InitAnswers(
            port=port,
            connection_choice="commonstack",
            upstream_url="https://api.commonstack.ai/v1",
        )
        self.applied: bool = False
        self.last_result: ApplyResult | None = None
        try:
            self._existing_upstream = ConnectionsStore().primary().upstream
        except Exception:
            self._existing_upstream = ""

    # ------------------------------------------------------------------
    # Compose
    # ------------------------------------------------------------------

    def compose(self) -> ComposeResult:
        yield Header(show_clock=False)
        with Horizontal():
            with Vertical(id="sidebar"):
                yield Static("uncommon-route init", id="sidebar-title")
                for i, (_, label) in enumerate(STEPS):
                    yield Static(f"{i + 1}. {label}", id=f"step-row-{i}", classes="step-row")
                yield Static("", classes="muted")
                yield Static("Ctrl+N next   Ctrl+P prev", classes="muted")
                yield Static("Ctrl+Q quit", classes="muted")
            with Vertical(id="main"):
                with ContentSwitcher(initial="welcome", id="switcher"):
                    yield self._build_welcome()
                    yield self._build_upstream()
                    yield self._build_byok()
                    yield self._build_client()
                    yield self._build_review()
                    yield self._build_done()
            with VerticalScroll(id="preview"):
                yield Static("Will write", classes="step-title")
                yield Static("(nothing yet)", id="preview-body", classes="muted")
        yield Footer()

    def on_mount(self) -> None:
        self.register_theme(UNCOMMON_ROUTE_THEME)
        self.theme = "uncommon-route"
        self._refresh_sidebar()
        self._refresh_preview()
        self.push_screen(SplashScreen())

    # ------------------------------------------------------------------
    # Step content builders
    # ------------------------------------------------------------------

    def _next_button(self, label: str = "Next ▶") -> Button:
        return Button(label, classes="step-next", variant="primary")

    def _build_welcome(self) -> Container:
        c = VerticalScroll(id="welcome")
        c.compose_add_child(Static(DONE_BANNER, id="welcome-banner"))
        c.compose_add_child(Static(
            "[b]Cut LLM costs by 82% with automatic model routing.[/b]",
            id="welcome-tagline",
        ))
        c.compose_add_child(Static(
            "UncommonRoute is a local proxy that picks the cheapest model "
            "capable of handling each request — Haiku-class for simple edits, "
            "Sonnet for harder work, Opus only when it really earns the spend.\n\n"
            "OpenAI- and Anthropic-compatible. No code changes — just point "
            "your client at localhost.",
            id="welcome-intro",
            classes="muted",
        ))
        c.compose_add_child(Static("This wizard will:", classes="section-label"))
        c.compose_add_child(Static(
            "  • choose how UncommonRoute talks to upstream models\n"
            "  • register your own provider keys (optional)\n"
            "  • configure Claude Code / Codex / OpenAI SDK exports\n"
            "  • optionally start the proxy in the background"
        ))
        c.compose_add_child(Static(
            "Press Next (or Ctrl+N) to continue. Ctrl+P moves back at any time.",
            classes="muted",
        ))
        c.compose_add_child(self._next_button("Get started ▶"))
        return c

    def _build_upstream(self) -> Container:
        c = VerticalScroll(id="upstream")
        c.compose_add_child(Static("How should UncommonRoute connect upstream?", classes="step-title"))
        c.compose_add_child(RadioSet(
            RadioButton("Commonstack managed upstream  (recommended)", id="conn-commonstack", value=True),
            RadioButton("Local or custom upstream  (Ollama, gateway, self-hosted)", id="conn-local"),
            RadioButton("Bring your own provider keys (BYOK)", id="conn-byok"),
            RadioButton("Skip — only configure the client side", id="conn-skip"),
            id="conn-radio",
        ))
        c.compose_add_child(Static("Upstream URL", classes="section-label"))
        c.compose_add_child(Input(
            value="https://api.commonstack.ai/v1",
            placeholder="https://api.commonstack.ai/v1",
            id="conn-url",
        ))
        c.compose_add_child(Static("API key", classes="section-label"))
        c.compose_add_child(Input(placeholder="ak-…", password=True, id="conn-key"))
        clear_label = (
            f"Clear stored primary upstream  (currently: {self._existing_upstream})"
            if self._existing_upstream
            else "Clear stored primary upstream"
        )
        clear_box = Checkbox(clear_label, id="conn-clear")
        clear_box.display = False  # only relevant when BYOK is picked + existing upstream
        c.compose_add_child(clear_box)
        c.compose_add_child(Static("", id="problems-upstream", classes="step-problems"))
        c.compose_add_child(self._next_button())
        return c

    def _build_byok(self) -> Container:
        c = VerticalScroll(id="byok")
        c.compose_add_child(Static("Bring your own provider keys", classes="step-title"))
        c.compose_add_child(Static(
            "Leave a row blank to skip that provider. "
            "Keys are stored locally at ~/.uncommon-route/providers.json (chmod 600).",
            classes="muted",
        ))
        for name in KNOWN_PROVIDERS:
            c.compose_add_child(Static(name, classes="section-label"))
            c.compose_add_child(Input(
                placeholder=f"{name} API key",
                password=True,
                id=f"byok-{name}",
            ))
        c.compose_add_child(Static("", id="problems-byok", classes="step-problems"))
        c.compose_add_child(self._next_button())
        return c

    def _build_client(self) -> Container:
        c = VerticalScroll(id="client")
        c.compose_add_child(Static("Client integration", classes="step-title"))
        c.compose_add_child(RadioSet(
            RadioButton("Claude Code", id="client-claude-code"),
            RadioButton("Codex", id="client-codex"),
            RadioButton("OpenAI SDK / Cursor", id="client-openai"),
            RadioButton("Skip — don't write shell exports", id="client-skip", value=True),
            id="client-radio",
        ))
        c.compose_add_child(Static("Proxy port (default 8403)", classes="section-label"))
        c.compose_add_child(Input(value="8403", id="client-port"))
        c.compose_add_child(Checkbox(
            f"Write exports to {self._rc_display}",
            value=True,
            id="client-write-rc",
        ))
        c.compose_add_child(self._next_button("Review ▶"))
        return c

    def _build_review(self) -> Container:
        c = VerticalScroll(id="review")
        c.compose_add_child(Static("Review", classes="step-title"))
        c.compose_add_child(Static(
            "Below is the exact set of changes that will be applied.",
            classes="muted",
        ))
        c.compose_add_child(Static("", id="review-plan"))
        c.compose_add_child(Checkbox("Start proxy in background after applying", id="review-start"))
        c.compose_add_child(Static("", id="problems"))
        c.compose_add_child(Button("Apply changes", id="btn-apply", variant="success"))
        return c

    def _build_done(self) -> Container:
        c = VerticalScroll(id="done")
        c.compose_add_child(Static(DONE_BANNER, id="done-banner"))
        c.compose_add_child(Static("", id="done-headline", classes="step-title"))
        c.compose_add_child(Static("", id="done-summary"))
        c.compose_add_child(Static("", id="done-hint", classes="muted"))
        c.compose_add_child(Button("Quit", id="btn-quit", variant="primary", classes="step-next"))
        return c

    # ------------------------------------------------------------------
    # Navigation
    # ------------------------------------------------------------------

    def _step_applicable(self, index: int) -> bool:
        """BYOK is only applicable when the user picked BYOK as their upstream."""
        if 0 <= index < len(STEPS) and STEPS[index][0] == "byok":
            return self.answers.connection_choice == "byok"
        return True

    def action_next_step(self) -> None:
        current_id = STEPS[self.step_index][0]
        problems = validate(self.answers, step=current_id)
        box = self._problems_box(current_id)
        if problems:
            if box is not None:
                box.update("[$warning]" + "\n".join(f"⚠ {p}" for p in problems) + "[/]")
            return
        if box is not None:
            box.update("")
        i = self.step_index + 1
        while i < len(STEPS) and not self._step_applicable(i):
            i += 1
        if i < len(STEPS):
            self.step_index = i

    def _problems_box(self, step_id: str) -> Static | None:
        try:
            return self.query_one(f"#problems-{step_id}", Static)
        except Exception:
            return None

    def action_prev_step(self) -> None:
        i = self.step_index - 1
        while i >= 0 and not self._step_applicable(i):
            i -= 1
        if i >= 0:
            self.step_index = i

    def watch_step_index(self, _old: int, new: int) -> None:
        switcher = self.query_one("#switcher", ContentSwitcher)
        switcher.current = STEPS[new][0]
        self._refresh_sidebar()
        self._refresh_preview()
        if STEPS[new][0] == "review":
            self._refresh_problems()

    def _update_api_key_placeholder(self) -> None:
        try:
            key_input = self.query_one("#conn-key", Input)
        except Exception:
            return
        choice = self.answers.connection_choice
        if choice == "commonstack":
            key_input.placeholder = "ak-…"
        elif choice == "local":
            key_input.placeholder = "leave blank if not needed"
        else:
            key_input.placeholder = "ak-…"

    def _update_clear_visibility(self) -> None:
        """Show the clear-upstream checkbox only for BYOK + existing upstream."""
        try:
            box = self.query_one("#conn-clear", Checkbox)
        except Exception:
            return
        applicable = (
            self.answers.connection_choice == "byok"
            and bool(self._existing_upstream)
        )
        box.display = applicable
        if not applicable and box.value:
            box.value = False
            self.answers.clear_primary_for_byok = False

    def _refresh_sidebar(self) -> None:
        visible = 0
        for i, (_, label) in enumerate(STEPS):
            row = self.query_one(f"#step-row-{i}", Static)
            applicable = self._step_applicable(i)
            row.display = applicable
            if not applicable:
                continue
            visible += 1
            row.set_classes("step-row")
            row.update(f"{visible}. {label}")
            if i < self.step_index:
                row.add_class("done")
            elif i == self.step_index:
                row.add_class("active")

    # ------------------------------------------------------------------
    # Answers ↔ widgets
    # ------------------------------------------------------------------

    def on_radio_set_changed(self, event: RadioSet.Changed) -> None:
        rs_id = event.radio_set.id
        pressed_id = event.pressed.id or ""
        if rs_id == "conn-radio":
            mapping = {
                "conn-commonstack": "commonstack",
                "conn-local": "local",
                "conn-byok": "byok",
                "conn-skip": "skip",
            }
            self.answers.connection_choice = mapping.get(pressed_id, "skip")
            if self.answers.connection_choice == "commonstack" and not self.answers.upstream_url:
                # Pre-fill canonical URL
                url_input = self.query_one("#conn-url", Input)
                if not url_input.value:
                    url_input.value = "https://api.commonstack.ai/v1"
            self._update_clear_visibility()
            self._update_api_key_placeholder()
            self._refresh_sidebar()
        elif rs_id == "client-radio":
            mapping = {
                "client-claude-code": "claude-code",
                "client-codex": "codex",
                "client-openai": "openai",
                "client-skip": "skip",
            }
            self.answers.client_choice = mapping.get(pressed_id, "skip")
        self._refresh_preview()

    def on_input_changed(self, event: Input.Changed) -> None:
        wid = event.input.id or ""
        value = event.value
        if wid == "conn-url":
            self.answers.upstream_url = value
        elif wid == "conn-key":
            self.answers.upstream_api_key = value
        elif wid.startswith("byok-"):
            name = wid.removeprefix("byok-")
            self.answers.byok_keys[name] = value
        elif wid == "client-port":
            try:
                self.answers.port = int(value)
            except ValueError:
                pass
        self._refresh_preview()

    def on_checkbox_changed(self, event: Checkbox.Changed) -> None:
        wid = event.checkbox.id or ""
        if wid == "conn-clear":
            self.answers.clear_primary_for_byok = event.value
        elif wid == "client-write-rc":
            self.answers.write_rc = event.value
        elif wid == "review-start":
            self.answers.start_proxy = event.value
        self._refresh_preview()

    # ------------------------------------------------------------------
    # Preview / problems
    # ------------------------------------------------------------------

    KIND_GLYPH = {
        "write": "[$success]●[/]",
        "append": "[$accent]+[/]",
        "skip": "[$text-disabled]○[/]",
        "warn": "[$warning]![/]",
        "info": "[$text-disabled]·[/]",
    }

    def _refresh_preview(self) -> None:
        plan = build_plan(self.answers, rc_display=self._rc_display)
        if not plan:
            text = "[dim](nothing yet)[/dim]"
        else:
            lines = []
            for item in plan:
                glyph = self.KIND_GLYPH.get(item.kind, "·")
                lines.append(f"{glyph} [b]{item.target}[/b]")
                if item.detail:
                    lines.append(f"   [dim]{item.detail}[/dim]")
            text = "\n".join(lines)
        try:
            self.query_one("#preview-body", Static).update(text)
        except Exception:
            pass
        try:
            review_plan = self.query_one("#review-plan", Static)
            review_plan.update(text)
        except Exception:
            pass

    def _refresh_problems(self) -> None:
        problems = validate(self.answers)
        try:
            box = self.query_one("#problems", Static)
        except Exception:
            return
        if problems:
            box.update("[$warning]" + "\n".join(f"⚠ {p}" for p in problems) + "[/]")
            self.query_one("#btn-apply", Button).disabled = True
        else:
            box.update("")
            self.query_one("#btn-apply", Button).disabled = False

    # ------------------------------------------------------------------
    # Apply
    # ------------------------------------------------------------------

    def on_button_pressed(self, event: Button.Pressed) -> None:
        bid = event.button.id
        if bid == "btn-apply":
            self._do_apply()
        elif bid == "btn-quit":
            self.exit()
        elif "step-next" in event.button.classes:
            self.action_next_step()

    def _do_apply(self) -> None:
        problems = validate(self.answers)
        if problems:
            self._refresh_problems()
            return
        self.applied = True
        self.step_index = STEPS.index(("done", "Done")) if ("done", "Done") in STEPS else len(STEPS) - 1
        result = apply_plan(
            self.answers,
            rc_path=self._rc_path,
            rc_display=self._rc_display,
            render_exports=self._render_exports,
            start_background_proxy=self._start_background_proxy,
        )
        self.last_result = result
        self._render_done(result)

    def _render_done(self, result: ApplyResult) -> None:
        headline = self.query_one("#done-headline", Static)
        summary = self.query_one("#done-summary", Static)
        hint = self.query_one("#done-hint", Static)

        if result.failures and not result.successes:
            headline.update("[$error]Setup did not complete.[/]")
        elif result.failures:
            headline.update("[$warning]Setup finished with warnings.[/]")
        else:
            headline.update("[$success]You're all set.[/]")

        lines: list[str] = ["[b]What was configured:[/b]"]
        if not result.successes and not result.failures:
            lines.append("  [$text-disabled](nothing changed)[/]")
        for ok in result.successes:
            lines.append(f"  [$success]✓[/] {ok}")
        for err in result.failures:
            lines.append(f"  [$error]✗[/] {err}")
        summary.update("\n".join(lines))

        if not result.failures:
            hint.update(
                "Use model ID  [b]uncommon-route/auto[/b]  in your client.\n"
                "Press the Quit button or Ctrl+Q to exit."
            )
        else:
            hint.update(
                "Some steps failed — re-run init or use the matching subcommand "
                "(`provider`, `setup`, `serve`) to retry."
            )


def run_init_tui(
    *,
    rc_path: Path,
    rc_display: str,
    render_exports: Callable[[str, int], list[str]],
    start_background_proxy: Callable[..., tuple[bool, str]],
    port: int = 8403,
) -> ApplyResult | None:
    app = InitApp(
        rc_path=rc_path,
        rc_display=rc_display,
        render_exports=render_exports,
        start_background_proxy=start_background_proxy,
        port=port,
    )
    app.run()
    return app.last_result
