"""Translation catalog for the init TUI.

Strings are referenced by key. When a key is missing in the requested
language we fall back to English; if it is missing in English too, we
return the key itself so the gap is loud rather than silent.
"""

from __future__ import annotations

LANG_EN = "en"
LANG_ZH = "zh"
DEFAULT_LANG = LANG_EN
SUPPORTED_LANGS: tuple[str, ...] = (LANG_EN, LANG_ZH)


_EN: dict[str, str] = {
    # Window / sidebar
    "app.title": "uncommon-route init",
    "sidebar.shortcut.next_prev": "Ctrl+N next   Ctrl+P prev",
    "sidebar.shortcut.quit": "Ctrl+Q quit",
    # Step labels (sidebar + ContentSwitcher)
    "step.welcome": "Welcome",
    "step.upstream": "Upstream",
    "step.byok": "BYOK keys",
    "step.client": "Client",
    "step.review": "Review & apply",
    "step.done": "Done",
    # Welcome
    "welcome.tagline": "[b]Cut LLM costs by 82% with automatic model routing.[/b]",
    "welcome.intro": (
        "UncommonRoute is a local proxy that picks the cheapest model "
        "capable of handling each request — Haiku-class for simple edits, "
        "Sonnet for harder work, Opus only when it really earns the spend.\n\n"
        "OpenAI- and Anthropic-compatible. No code changes — just point "
        "your client at localhost."
    ),
    "welcome.checklist_label": "This wizard will:",
    "welcome.checklist": (
        "  • choose how UncommonRoute talks to upstream models\n"
        "  • register your own provider keys (optional)\n"
        "  • configure Claude Code / Codex / OpenAI SDK exports\n"
        "  • optionally start the proxy in the background"
    ),
    "welcome.nav_hint": "Press Next (or Ctrl+N) to continue. Ctrl+P moves back at any time.",
    "welcome.button": "Get started ▶",
    # Upstream
    "upstream.title": "How should UncommonRoute connect upstream?",
    "upstream.choice.commonstack": "Commonstack managed upstream  (recommended)",
    "upstream.choice.local": "Local or custom upstream  (Ollama, gateway, self-hosted)",
    "upstream.choice.byok": "Bring your own provider keys (BYOK)",
    "upstream.choice.skip": "Skip — only configure the client side",
    "upstream.url_label": "Upstream URL",
    "upstream.key_label": "API key",
    "upstream.key_placeholder.commonstack": "ak-…",
    "upstream.key_placeholder.local": "leave blank if not needed",
    "upstream.clear_label.with_url": "Clear stored primary upstream  (currently: {url})",
    "upstream.clear_label.empty": "Clear stored primary upstream",
    "upstream.button": "Next ▶",
    # BYOK
    "byok.title": "Bring your own provider keys",
    "byok.description": (
        "Leave a row blank to skip that provider. Keys are stored "
        "locally at ~/.uncommon-route/providers.json (chmod 600)."
    ),
    "byok.key_placeholder": "{name} API key",
    "byok.button": "Next ▶",
    # Client
    "client.title": "Client integration",
    "client.choice.claude_code": "Claude Code",
    "client.choice.codex": "Codex",
    "client.choice.openai": "OpenAI SDK / Cursor",
    "client.choice.skip": "Skip — don't write shell exports",
    "client.port_label": "Proxy port (default 8403)",
    "client.write_rc": "Write exports to {rc}",
    "client.button": "Review ▶",
    # Review
    "review.title": "Review",
    "review.description": "Below is the exact set of changes that will be applied.",
    "review.start_proxy": "Start proxy in background after applying",
    "review.button": "Apply changes",
    # Done
    "done.headline.success": "[$success]You're all set.[/]",
    "done.headline.warning": "[$warning]Setup finished with warnings.[/]",
    "done.headline.error": "[$error]Setup did not complete.[/]",
    "done.summary_header": "[b]What was configured:[/b]",
    "done.summary_empty": "  [$text-disabled](nothing changed)[/]",
    "done.hint.success": (
        "Use model ID  [b]uncommon-route/auto[/b]  in your client.\n"
        "Press the Quit button or Ctrl+Q to exit."
    ),
    "done.hint.failure": (
        "Some steps failed — re-run init or use the matching subcommand "
        "(`provider`, `setup`, `serve`) to retry."
    ),
    "done.button": "Quit",
    # Preview / "Will write" panel
    "preview.title": "Will write",
    "preview.empty": "(nothing yet)",
    # Splash + language picker
    "splash.subtitle": "UncommonRoute  ·  local llm router",
    "splash.hint": "press any key…",
    "language.title": "Select language / 选择语言",
    "language.en_button": "English",
    "language.zh_button": "中文",
    # Validation messages
    "validate.commonstack_url": "Commonstack URL is required.",
    "validate.commonstack_key": "Commonstack API key is required.",
    "validate.local_url": "Upstream URL is required.",
    "validate.byok_empty": "Add at least one BYOK provider key (or pick a different connection).",
    # Plan items shown in "Will write" panel
    "plan.connections_set": "set primary upstream → {url}",
    "plan.connections_reset": "reset primary upstream (BYOK-only)",
    "plan.add_provider": "add provider → {name}",
    "plan.byok_no_keys": "no keys entered",
    "plan.skip_connection": "connection setup",
    "plan.skip_client": "client integration",
    "plan.skip_value": "skipped",
    "plan.client_exports": "insert/update shell exports for {client}",
    "plan.shell_exports": "shell exports ({client})",
    "plan.display_only": "display only",
    "plan.proxy_daemon_target": "proxy daemon",
    "plan.proxy_daemon_detail": "start on 127.0.0.1:{port}",
    "plan.byok_target": "BYOK providers",
    # Missing / fallback values
    "value.missing": "(missing)",
}


_ZH: dict[str, str] = {
    "app.title": "uncommon-route 初始化",
    "sidebar.shortcut.next_prev": "Ctrl+N 下一步   Ctrl+P 上一步",
    "sidebar.shortcut.quit": "Ctrl+Q 退出",
    "step.welcome": "欢迎",
    "step.upstream": "上游",
    "step.byok": "BYOK 密钥",
    "step.client": "客户端",
    "step.review": "确认并应用",
    "step.done": "完成",
    "welcome.tagline": "[b]通过自动模型路由削减 82% 的 LLM 成本。[/b]",
    "welcome.intro": (
        "UncommonRoute 是一个本地代理，会为每个请求挑选最便宜且能完成任务的模型——"
        "Haiku 级处理简单编辑，Sonnet 处理较难任务，Opus 只在真正值得时才动用。\n\n"
        "兼容 OpenAI 和 Anthropic 接口，无需改代码——把客户端指向 localhost 即可。"
    ),
    "welcome.checklist_label": "本向导将帮你：",
    "welcome.checklist": (
        "  • 选择 UncommonRoute 如何连接上游模型\n"
        "  • 注册你自己的 provider key（可选）\n"
        "  • 配置 Claude Code / Codex / OpenAI SDK 的环境变量\n"
        "  • 可选地在后台启动代理"
    ),
    "welcome.nav_hint": "按 Next（或 Ctrl+N）继续。Ctrl+P 可随时回到上一步。",
    "welcome.button": "开始 ▶",
    "upstream.title": "UncommonRoute 应该如何连接上游？",
    "upstream.choice.commonstack": "Commonstack 托管上游（推荐）",
    "upstream.choice.local": "本地或自定义上游（Ollama、网关、自托管）",
    "upstream.choice.byok": "使用自带的 provider key（BYOK）",
    "upstream.choice.skip": "跳过——只配置客户端",
    "upstream.url_label": "上游 URL",
    "upstream.key_label": "API key",
    "upstream.key_placeholder.commonstack": "ak-…",
    "upstream.key_placeholder.local": "可留空，本地上游通常无需 key",
    "upstream.clear_label.with_url": "清除已存储的主上游（当前：{url}）",
    "upstream.clear_label.empty": "清除已存储的主上游",
    "upstream.button": "下一步 ▶",
    "byok.title": "自带的 provider 密钥",
    "byok.description": (
        "留空则跳过该 provider。密钥仅存储在本地 "
        "~/.uncommon-route/providers.json（chmod 600）。"
    ),
    "byok.key_placeholder": "{name} API key",
    "byok.button": "下一步 ▶",
    "client.title": "客户端集成",
    "client.choice.claude_code": "Claude Code",
    "client.choice.codex": "Codex",
    "client.choice.openai": "OpenAI SDK / Cursor",
    "client.choice.skip": "跳过——不写入 shell 环境变量",
    "client.port_label": "代理端口（默认 8403）",
    "client.write_rc": "把环境变量写入 {rc}",
    "client.button": "查看变更 ▶",
    "review.title": "确认变更",
    "review.description": "以下是即将应用的全部改动。",
    "review.start_proxy": "应用后立即在后台启动代理",
    "review.button": "应用变更",
    "done.headline.success": "[$success]全部就绪。[/]",
    "done.headline.warning": "[$warning]配置已完成，但有警告。[/]",
    "done.headline.error": "[$error]配置未能完成。[/]",
    "done.summary_header": "[b]已完成的配置：[/b]",
    "done.summary_empty": "  [$text-disabled]（未发生改动）[/]",
    "done.hint.success": (
        "在客户端中使用模型 ID  [b]uncommon-route/auto[/b]。\n"
        "按 Quit 按钮或 Ctrl+Q 退出。"
    ),
    "done.hint.failure": (
        "部分步骤失败——可重新运行 init，或使用对应的子命令"
        "（`provider`、`setup`、`serve`）重试。"
    ),
    "done.button": "退出",
    "preview.title": "即将写入",
    "preview.empty": "（暂无）",
    "splash.subtitle": "UncommonRoute  ·  本地 LLM 路由",
    "splash.hint": "按任意键继续…",
    "language.title": "Select language / 选择语言",
    "language.en_button": "English",
    "language.zh_button": "中文",
    "validate.commonstack_url": "Commonstack URL 不能为空。",
    "validate.commonstack_key": "Commonstack API key 不能为空。",
    "validate.local_url": "上游 URL 不能为空。",
    "validate.byok_empty": "至少添加一个 BYOK provider key（或选择其他连接方式）。",
    "plan.connections_set": "设置主上游 → {url}",
    "plan.connections_reset": "重置主上游（BYOK-only）",
    "plan.add_provider": "添加 provider → {name}",
    "plan.byok_no_keys": "未输入任何密钥",
    "plan.skip_connection": "上游配置",
    "plan.skip_client": "客户端集成",
    "plan.skip_value": "已跳过",
    "plan.client_exports": "为 {client} 写入/更新 shell 环境变量",
    "plan.shell_exports": "shell 环境变量（{client}）",
    "plan.display_only": "仅展示",
    "plan.proxy_daemon_target": "代理 daemon",
    "plan.proxy_daemon_detail": "在 127.0.0.1:{port} 启动",
    "plan.byok_target": "BYOK provider",
    "value.missing": "（缺失）",
}


TRANSLATIONS: dict[str, dict[str, str]] = {
    LANG_EN: _EN,
    LANG_ZH: _ZH,
}


def t(key: str, lang: str = DEFAULT_LANG, **fmt: object) -> str:
    """Look up a translation, falling back to English then to the key."""
    catalog = TRANSLATIONS.get(lang, _EN)
    raw = catalog.get(key)
    if raw is None:
        raw = _EN.get(key, key)
    if fmt:
        try:
            return raw.format(**fmt)
        except (KeyError, IndexError):
            return raw
    return raw
