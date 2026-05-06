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
from textual.message import Message
from textual.reactive import reactive
from textual.screen import Screen
from textual.widgets import (
    Button,
    Checkbox,
    ContentSwitcher,
    Footer,
    Header,
    Input,
    RadioButton,
    RadioSet,
    Static,
)

from uncommon_route.version import VERSION



from uncommon_route.connections_store import ConnectionsStore
from uncommon_route.tui.i18n import (
    DEFAULT_LANG,
    LANG_EN,
    LANG_ZH,
    SUPPORTED_LANGS,
    t as translate,
)
from uncommon_route.tui.init_plan import (
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


SPLASH_BANNER_BLOCK = _render_banner("$accent")
DONE_BANNER = _render_banner("$accent")


def splash_full_text(lang: str) -> str:
    subtitle = translate("splash.subtitle", lang)
    return f"{SPLASH_BANNER_BLOCK}\n\n[dim]{subtitle}[/dim]"


class LangOption(Static):
    """A clickable text option in the splash language picker."""

    class Selected(Message):
        def __init__(self, lang: str) -> None:
            super().__init__()
            self.lang = lang

    DEFAULT_CSS = """
    LangOption {
        margin: 0 2;
        padding: 0 1;
        color: $text-muted;
        height: 1;
        width: auto;
    }

    LangOption:hover {
        background: $boost;
        color: $accent;
        text-style: bold;
    }
    """

    def __init__(self, content: str, *, lang: str, id: str | None = None) -> None:
        super().__init__(content, id=id, markup=True)
        self._lang_code = lang

    def on_click(self) -> None:
        self.post_message(self.Selected(self._lang_code))


class SplashScreen(Screen[str]):
    """Launch screen — UR logo + language picker. Dismisses with the
    chosen language code so the app can apply translations."""

    DEFAULT_CSS = """
    SplashScreen {
        align: center middle;
        background: $background;
    }

    #splash-banner {
        text-align: center;
        padding: 1 4;
    }

    #splash-version {
        text-align: center;
        color: $text-muted;
        margin-top: 1;
    }

    #splash-prompt {
        text-align: center;
        color: $text;
        text-style: bold;
        margin-top: 2;
    }

    #splash-options {
        width: auto;
        height: 1;
        margin-top: 2;
    }
    """

    BINDINGS = [
        Binding("e", "pick('en')", "EN", priority=True),
        Binding("z", "pick('zh')", "中文", priority=True),
    ]

    def __init__(self, lang: str = DEFAULT_LANG) -> None:
        super().__init__()
        self._lang = lang

    def compose(self) -> ComposeResult:
        with Middle():
            with Center():
                yield Static(splash_full_text(self._lang), id="splash-banner")
            with Center():
                yield Static(f"v{VERSION}", id="splash-version")
            with Center():
                yield Static("Select language / 选择语言", id="splash-prompt")
            with Center():
                with Horizontal(id="splash-options"):
                    yield LangOption(
                        r"[$accent]\[E][/]  English",
                        lang=LANG_EN,
                        id="opt-en",
                    )
                    yield LangOption(
                        r"[$accent]\[Z][/]  中文",
                        lang=LANG_ZH,
                        id="opt-zh",
                    )

    def on_lang_option_selected(self, event: LangOption.Selected) -> None:
        if event.lang in SUPPORTED_LANGS:
            self.dismiss(event.lang)

    def action_pick(self, lang: str) -> None:
        if lang in SUPPORTED_LANGS:
            self.dismiss(lang)


STEPS: list[tuple[str, str]] = [
    ("welcome", "step.welcome"),
    ("upstream", "step.upstream"),
    ("byok", "step.byok"),
    ("client", "step.client"),
    ("review", "step.review"),
    ("done", "step.done"),
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
        self._lang: str = DEFAULT_LANG
        try:
            self._existing_upstream = ConnectionsStore().primary().upstream
        except Exception:
            self._existing_upstream = ""

    def t(self, key: str, **fmt: object) -> str:
        return translate(key, self._lang, **fmt)

    # ------------------------------------------------------------------
    # Compose
    # ------------------------------------------------------------------

    def compose(self) -> ComposeResult:
        yield Header(show_clock=False)
        with Horizontal():
            with Vertical(id="sidebar"):
                yield Static(self.t("app.title"), id="sidebar-title")
                for i, (_, label_key) in enumerate(STEPS):
                    yield Static(
                        f"{i + 1}. {self.t(label_key)}",
                        id=f"step-row-{i}",
                        classes="step-row",
                    )
                yield Static("", classes="muted")
                yield Static(self.t("sidebar.shortcut.next_prev"), id="sidebar-shortcut-nav", classes="muted")
                yield Static(self.t("sidebar.shortcut.quit"), id="sidebar-shortcut-quit", classes="muted")
            with Vertical(id="main"):
                with ContentSwitcher(initial="welcome", id="switcher"):
                    yield self._build_welcome()
                    yield self._build_upstream()
                    yield self._build_byok()
                    yield self._build_client()
                    yield self._build_review()
                    yield self._build_done()
        yield Footer()

    def on_mount(self) -> None:
        self._refresh_sidebar()
        self._refresh_preview()
        self.push_screen(SplashScreen(self._lang), self._after_splash)

    def _after_splash(self, lang: str | None) -> None:
        if lang and lang in SUPPORTED_LANGS:
            self._lang = lang
        self._apply_translations()

    # ------------------------------------------------------------------
    # Step content builders
    # ------------------------------------------------------------------

    def _next_button(self, label_key: str = "upstream.button", *, button_id: str | None = None) -> Button:
        btn = Button(self.t(label_key), classes="step-next", variant="primary")
        btn.label_key = label_key  # remembered for re-translation
        if button_id:
            btn.id = button_id
        return btn

    def _build_welcome(self) -> Container:
        c = VerticalScroll(id="welcome")
        c.compose_add_child(Static(DONE_BANNER, id="welcome-banner"))
        c.compose_add_child(Static(self.t("welcome.tagline"), id="welcome-tagline"))
        c.compose_add_child(Static(self.t("welcome.intro"), id="welcome-intro", classes="muted"))
        c.compose_add_child(Static(self.t("welcome.checklist_label"), id="welcome-checklist-label", classes="section-label"))
        c.compose_add_child(Static(self.t("welcome.checklist"), id="welcome-checklist"))
        c.compose_add_child(Static(self.t("welcome.nav_hint"), id="welcome-nav-hint", classes="muted"))
        c.compose_add_child(self._next_button("welcome.button", button_id="btn-welcome-next"))
        return c

    def _build_upstream(self) -> Container:
        c = VerticalScroll(id="upstream")
        c.compose_add_child(Static(self.t("upstream.title"), id="upstream-title", classes="step-title"))
        c.compose_add_child(RadioSet(
            RadioButton(self.t("upstream.choice.commonstack"), id="conn-commonstack", value=True),
            RadioButton(self.t("upstream.choice.local"), id="conn-local"),
            RadioButton(self.t("upstream.choice.byok"), id="conn-byok"),
            RadioButton(self.t("upstream.choice.skip"), id="conn-skip"),
            id="conn-radio",
        ))
        c.compose_add_child(Static(self.t("upstream.url_label"), id="upstream-url-label", classes="section-label"))
        c.compose_add_child(Input(
            value="https://api.commonstack.ai/v1",
            placeholder="https://api.commonstack.ai/v1",
            id="conn-url",
        ))
        c.compose_add_child(Static(self.t("upstream.key_label"), id="upstream-key-label", classes="section-label"))
        c.compose_add_child(Input(placeholder=self.t("upstream.key_placeholder.commonstack"), password=True, id="conn-key"))
        clear_box = Checkbox(self._clear_upstream_label(), id="conn-clear")
        clear_box.display = False  # only relevant when BYOK is picked + existing upstream
        c.compose_add_child(clear_box)
        c.compose_add_child(Static("", id="problems-upstream", classes="step-problems"))
        c.compose_add_child(self._next_button("upstream.button", button_id="btn-upstream-next"))
        return c

    def _clear_upstream_label(self) -> str:
        if self._existing_upstream:
            return self.t("upstream.clear_label.with_url", url=self._existing_upstream)
        return self.t("upstream.clear_label.empty")

    def _build_byok(self) -> Container:
        c = VerticalScroll(id="byok")
        c.compose_add_child(Static(self.t("byok.title"), id="byok-title", classes="step-title"))
        c.compose_add_child(Static(self.t("byok.description"), id="byok-description", classes="muted"))
        for name in KNOWN_PROVIDERS:
            c.compose_add_child(Static(name, classes="section-label"))
            c.compose_add_child(Input(
                placeholder=self.t("byok.key_placeholder", name=name),
                password=True,
                id=f"byok-{name}",
            ))
        c.compose_add_child(Static("", id="problems-byok", classes="step-problems"))
        c.compose_add_child(self._next_button("byok.button", button_id="btn-byok-next"))
        return c

    def _build_client(self) -> Container:
        c = VerticalScroll(id="client")
        c.compose_add_child(Static(self.t("client.title"), id="client-title", classes="step-title"))
        c.compose_add_child(RadioSet(
            RadioButton(self.t("client.choice.claude_code"), id="client-claude-code"),
            RadioButton(self.t("client.choice.codex"), id="client-codex"),
            RadioButton(self.t("client.choice.openai"), id="client-openai"),
            RadioButton(self.t("client.choice.skip"), id="client-skip", value=True),
            id="client-radio",
        ))
        c.compose_add_child(Static(self.t("client.port_label"), id="client-port-label", classes="section-label"))
        c.compose_add_child(Input(value="8403", id="client-port"))
        c.compose_add_child(Checkbox(
            self.t("client.write_rc", rc=self._rc_display),
            value=True,
            id="client-write-rc",
        ))
        c.compose_add_child(self._next_button("client.button", button_id="btn-client-next"))
        return c

    def _build_review(self) -> Container:
        c = VerticalScroll(id="review")
        c.compose_add_child(Static(self.t("review.title"), id="review-title", classes="step-title"))
        c.compose_add_child(Static(self.t("review.description"), id="review-description", classes="muted"))
        c.compose_add_child(Static("", id="review-plan"))
        c.compose_add_child(Checkbox(self.t("review.start_proxy"), id="review-start"))
        c.compose_add_child(Static("", id="problems"))
        c.compose_add_child(Button(self.t("review.button"), id="btn-apply", variant="success"))
        return c

    def _build_done(self) -> Container:
        c = VerticalScroll(id="done")
        c.compose_add_child(Static(DONE_BANNER, id="done-banner"))
        c.compose_add_child(Static("", id="done-headline", classes="step-title"))
        c.compose_add_child(Static("", id="done-summary"))
        c.compose_add_child(Static("", id="done-hint", classes="muted"))
        c.compose_add_child(Button(self.t("done.button"), id="btn-quit", variant="primary", classes="step-next"))
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
        problems = validate(self.answers, step=current_id, t=self.t)
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

    def _apply_translations(self) -> None:
        """Re-evaluate every visible label after the user changes language."""

        # Static text widgets — selector → key
        statics: dict[str, str] = {
            "#sidebar-title": "app.title",
            "#sidebar-shortcut-nav": "sidebar.shortcut.next_prev",
            "#sidebar-shortcut-quit": "sidebar.shortcut.quit",
            "#welcome-tagline": "welcome.tagline",
            "#welcome-intro": "welcome.intro",
            "#welcome-checklist-label": "welcome.checklist_label",
            "#welcome-checklist": "welcome.checklist",
            "#welcome-nav-hint": "welcome.nav_hint",
            "#upstream-title": "upstream.title",
            "#upstream-url-label": "upstream.url_label",
            "#upstream-key-label": "upstream.key_label",
            "#byok-title": "byok.title",
            "#byok-description": "byok.description",
            "#client-title": "client.title",
            "#client-port-label": "client.port_label",
            "#review-title": "review.title",
            "#review-description": "review.description",
        }
        for selector, key in statics.items():
            try:
                self.query_one(selector, Static).update(self.t(key))
            except Exception:
                continue

        # RadioButton labels
        radios: dict[str, str] = {
            "#conn-commonstack": "upstream.choice.commonstack",
            "#conn-local": "upstream.choice.local",
            "#conn-byok": "upstream.choice.byok",
            "#conn-skip": "upstream.choice.skip",
            "#client-claude-code": "client.choice.claude_code",
            "#client-codex": "client.choice.codex",
            "#client-openai": "client.choice.openai",
            "#client-skip": "client.choice.skip",
        }
        for selector, key in radios.items():
            try:
                self.query_one(selector, RadioButton).label = self.t(key)
            except Exception:
                continue

        # Buttons that carry a `label_key` attribute (set by _next_button)
        for btn in self.query(Button):
            key = getattr(btn, "label_key", None)
            if key:
                btn.label = self.t(key)
        # Button labels not tied to label_key
        button_labels: dict[str, str] = {
            "#btn-apply": "review.button",
            "#btn-quit": "done.button",
        }
        for selector, key in button_labels.items():
            try:
                self.query_one(selector, Button).label = self.t(key)
            except Exception:
                continue

        # Checkboxes that carry a translated label
        try:
            self.query_one("#client-write-rc", Checkbox).label = self.t(
                "client.write_rc", rc=self._rc_display
            )
        except Exception:
            pass
        try:
            self.query_one("#review-start", Checkbox).label = self.t("review.start_proxy")
        except Exception:
            pass

        # BYOK provider rows — placeholders are translated, names stay literal
        for name in KNOWN_PROVIDERS:
            try:
                self.query_one(f"#byok-{name}", Input).placeholder = self.t(
                    "byok.key_placeholder", name=name
                )
            except Exception:
                continue

        # The clear-upstream checkbox label (depends on existing upstream)
        try:
            self.query_one("#conn-clear", Checkbox).label = self._clear_upstream_label()
        except Exception:
            pass

        # Trigger downstream refreshers — they read self.t() lazily
        self._update_api_key_placeholder()
        self._refresh_sidebar()
        self._refresh_preview()
        if self.step_index < len(STEPS) and STEPS[self.step_index][0] == "review":
            self._refresh_problems()
        if self.applied and self.last_result is not None:
            self._render_done(self.last_result)

    def _update_api_key_placeholder(self) -> None:
        try:
            key_input = self.query_one("#conn-key", Input)
        except Exception:
            return
        choice = self.answers.connection_choice
        if choice == "local":
            key_input.placeholder = self.t("upstream.key_placeholder.local")
        else:
            key_input.placeholder = self.t("upstream.key_placeholder.commonstack")

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
        for i, (_, label_key) in enumerate(STEPS):
            row = self.query_one(f"#step-row-{i}", Static)
            applicable = self._step_applicable(i)
            row.display = applicable
            if not applicable:
                continue
            visible += 1
            row.set_classes("step-row")
            row.update(f"{visible}. {self.t(label_key)}")
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
        """Render the plan into the Review step's #review-plan Static."""
        plan = build_plan(self.answers, rc_display=self._rc_display, t=self.t)
        if not plan:
            text = f"[dim]{self.t('preview.empty')}[/dim]"
        else:
            lines = []
            for item in plan:
                glyph = self.KIND_GLYPH.get(item.kind, "·")
                lines.append(f"{glyph} [b]{item.target}[/b]")
                if item.detail:
                    lines.append(f"   [dim]{item.detail}[/dim]")
            text = "\n".join(lines)
        try:
            self.query_one("#review-plan", Static).update(text)
        except Exception:
            pass

    def _refresh_problems(self) -> None:
        problems = validate(self.answers, t=self.t)
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
        problems = validate(self.answers, t=self.t)
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
            headline.update(self.t("done.headline.error"))
        elif result.failures:
            headline.update(self.t("done.headline.warning"))
        else:
            headline.update(self.t("done.headline.success"))

        lines: list[str] = [self.t("done.summary_header")]
        if not result.successes and not result.failures:
            lines.append(self.t("done.summary_empty"))
        for ok in result.successes:
            lines.append(f"  [$success]✓[/] {ok}")
        for err in result.failures:
            lines.append(f"  [$error]✗[/] {err}")
        summary.update("\n".join(lines))

        if not result.failures:
            hint.update(self.t("done.hint.success"))
        else:
            hint.update(self.t("done.hint.failure"))


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
