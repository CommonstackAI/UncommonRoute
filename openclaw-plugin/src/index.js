/**
 * OpenClaw Plugin — UncommonRoute
 *
 * Bridges the Python UncommonRoute router into OpenClaw's plugin system.
 * Auto-installs Python package on first run (zero manual setup).
 *
 * Architecture:
 *   openclaw plugins install @anjieyang/uncommon-route
 *     → this plugin loads
 *     → ensures `uncommon-route` Python package is installed (pipx/uv/pip)
 *     → spawns `uncommon-route serve` as a managed subprocess
 *     → registerProvider pointing at localhost proxy
 *     → registerCommand for /route, /spend, /feedback
 */

import { spawn, execSync } from "node:child_process";
import { setTimeout as sleep } from "node:timers/promises";

const VERSION = "0.7.9";
const DEFAULT_PORT = 8403;
const DEFAULT_UPSTREAM = "";
const HEALTH_TIMEOUT_MS = 15_000;
const HEALTH_POLL_MS = 500;
const PY_PACKAGE = "uncommon-route";

const MODELS = [
  { id: "uncommon-route/auto", name: "UncommonRoute Auto", reasoning: false, input: 0, output: 0, ctx: 200_000, max: 16_384 },
  { id: "uncommon-route/fast", name: "UncommonRoute Fast", reasoning: false, input: 0, output: 0, ctx: 200_000, max: 16_384 },
  { id: "uncommon-route/best", name: "UncommonRoute Best", reasoning: true, input: 0, output: 0, ctx: 200_000, max: 16_384 },
  { id: "moonshot/kimi-k2.5", name: "Kimi K2.5", reasoning: false, input: 0.60, output: 3.00, ctx: 128_000, max: 8_192 },
  { id: "google/gemini-3.1-pro", name: "Gemini 3.1 Pro", reasoning: false, input: 2.00, output: 12.00, ctx: 200_000, max: 16_384 },
  { id: "xai/grok-4-1-fast-reasoning", name: "Grok 4.1 Fast", reasoning: true, input: 0.20, output: 0.50, ctx: 200_000, max: 16_384 },
  { id: "deepseek/deepseek-chat", name: "DeepSeek Chat", reasoning: false, input: 0.28, output: 0.42, ctx: 128_000, max: 8_192 },
  { id: "deepseek/deepseek-reasoner", name: "DeepSeek Reasoner", reasoning: true, input: 0.28, output: 0.42, ctx: 128_000, max: 8_192 },
  { id: "google/gemini-2.5-flash", name: "Gemini 2.5 Flash", reasoning: false, input: 0.30, output: 2.50, ctx: 200_000, max: 16_384 },
  { id: "google/gemini-2.5-flash-lite", name: "Gemini 2.5 Flash Lite", reasoning: false, input: 0.10, output: 0.40, ctx: 200_000, max: 16_384 },
  { id: "openai/gpt-5.2", name: "GPT-5.2", reasoning: false, input: 1.75, output: 14.00, ctx: 200_000, max: 16_384 },
  { id: "openai/o4-mini", name: "o4 Mini", reasoning: true, input: 1.10, output: 4.40, ctx: 200_000, max: 16_384 },
  { id: "anthropic/claude-sonnet-4.6", name: "Claude Sonnet 4.6", reasoning: false, input: 3.00, output: 15.00, ctx: 200_000, max: 16_384 },
];

// ── Python dependency management ─────────────────────────────────────

function which(cmd) {
  try {
    return execSync(`which ${cmd}`, { encoding: "utf-8", stdio: ["pipe", "pipe", "pipe"] }).trim();
  } catch {
    return null;
  }
}

function isPythonPackageInstalled(pythonPath) {
  try {
    execSync(`${pythonPath} -c "import uncommon_route"`, { stdio: "pipe" });
    return true;
  } catch {
    return false;
  }
}

/**
 * Ensure the Python `uncommon-route` package is installed.
 * Tries: pipx → uv → pip (with --user fallback).
 * Returns the python executable to use.
 */
function ensurePythonDeps(logger) {
  const pythonCandidates = ["python3", "python"];
  let pythonPath = null;

  for (const candidate of pythonCandidates) {
    const path = which(candidate);
    if (path) {
      try {
        const ver = execSync(`${path} --version`, { encoding: "utf-8" }).trim();
        const match = ver.match(/(\d+)\.(\d+)/);
        if (match && (parseInt(match[1]) > 3 || (parseInt(match[1]) === 3 && parseInt(match[2]) >= 11))) {
          pythonPath = path;
          break;
        }
      } catch { /* skip */ }
    }
  }

  if (!pythonPath) {
    logger.error("Python 3.11+ not found. Install Python first: https://python.org");
    logger.error("  macOS:   brew install python@3.12");
    logger.error("  Ubuntu:  sudo apt install python3.12");
    return null;
  }

  if (isPythonPackageInstalled(pythonPath)) {
    logger.info(`Python package '${PY_PACKAGE}' already installed`);
    return pythonPath;
  }

  logger.info(`Installing Python package '${PY_PACKAGE}'...`);

  // Strategy 1: pipx (isolated, clean)
  if (which("pipx")) {
    try {
      execSync(`pipx install ${PY_PACKAGE}`, { stdio: "pipe" });
      logger.info(`Installed via pipx`);
      return pythonPath;
    } catch { /* fallthrough */ }
  }

  // Strategy 2: uv (fast, modern)
  if (which("uv")) {
    try {
      execSync(`uv pip install ${PY_PACKAGE}`, { stdio: "pipe" });
      logger.info(`Installed via uv`);
      return pythonPath;
    } catch { /* fallthrough */ }
  }

  // Strategy 3: pip install --user
  try {
    execSync(`${pythonPath} -m pip install ${PY_PACKAGE} --user --quiet`, { stdio: "pipe" });
    logger.info(`Installed via pip --user`);
    return pythonPath;
  } catch { /* fallthrough */ }

  // Strategy 4: pip install --break-system-packages (last resort on managed envs)
  try {
    execSync(`${pythonPath} -m pip install ${PY_PACKAGE} --user --break-system-packages --quiet`, { stdio: "pipe" });
    logger.info(`Installed via pip (break-system-packages)`);
    return pythonPath;
  } catch (err) {
    logger.error(`Failed to install '${PY_PACKAGE}': ${err.message}`);
    logger.error(`  Manual install: pip install ${PY_PACKAGE}`);
    return null;
  }
}

// ── Helpers ──────────────────────────────────────────────────────────

function buildModels(baseUrl) {
  return {
    baseUrl,
    api: "openai-completions",
    apiKey: "uncommon-route-local-proxy",
    models: MODELS.map((m) => ({
      id: m.id,
      name: m.name,
      api: "openai-completions",
      reasoning: m.reasoning,
      input: ["text"],
      cost: { input: m.input, output: m.output, cacheRead: 0, cacheWrite: 0 },
      contextWindow: m.ctx,
      maxTokens: m.max,
    })),
  };
}

async function waitForHealth(port, timeoutMs = HEALTH_TIMEOUT_MS) {
  const url = `http://127.0.0.1:${port}/health`;
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    try {
      const resp = await fetch(url, { signal: AbortSignal.timeout(2000) });
      if (resp.ok) return true;
    } catch { /* not ready */ }
    await sleep(HEALTH_POLL_MS);
  }
  return false;
}

async function fetchJson(url) {
  try {
    const resp = await fetch(url, { signal: AbortSignal.timeout(5000) });
    return resp.ok ? await resp.json() : null;
  } catch { return null; }
}

async function postJson(url, body) {
  try {
    const resp = await fetch(url, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(body),
      signal: AbortSignal.timeout(5000),
    });
    return resp.ok ? await resp.json() : null;
  } catch { return null; }
}

// ── Plugin ───────────────────────────────────────────────────────────

/** @type {import("node:child_process").ChildProcess | null} */
let pyProc = null;

const plugin = {
  id: "@anjieyang/uncommon-route",
  name: "UncommonRoute",
  description: "Local LLM router plugin that cuts premium-model spend with smart routing",
  version: VERSION,

  register(api) {
    const isDisabled =
      process.env.UNCOMMON_ROUTE_DISABLED === "true" ||
      process.env.UNCOMMON_ROUTE_DISABLED === "1";
    if (isDisabled) {
      api.logger.info("UncommonRoute disabled via UNCOMMON_ROUTE_DISABLED");
      return;
    }

    const cfg = api.pluginConfig || {};
    const port = cfg.port || Number(process.env.UNCOMMON_ROUTE_PORT) || DEFAULT_PORT;
    const upstream = cfg.upstream || process.env.UNCOMMON_ROUTE_UPSTREAM || DEFAULT_UPSTREAM;
    const baseUrl = `http://127.0.0.1:${port}/v1`;

    if (!upstream) {
      api.logger.warn("UncommonRoute: No upstream configured. Set UNCOMMON_ROUTE_UPSTREAM or configure 'upstream' in plugin config.");
      api.logger.warn("  Example: UNCOMMON_ROUTE_UPSTREAM=https://api.commonstack.ai/v1 UNCOMMON_ROUTE_API_KEY=csk-...");
    }

    // 1. Register provider immediately (sync, models available right away)
    api.registerProvider({
      id: "uncommon-route",
      label: "UncommonRoute",
      docsPath: "https://github.com/CommonstackAI/UncommonRoute",
      aliases: ["ur", "uncommon"],
      envVars: [],
      get models() { return buildModels(baseUrl); },
      auth: [],
    });

    if (!api.config.models) api.config.models = { providers: {} };
    if (!api.config.models.providers) api.config.models.providers = {};
    api.config.models.providers["uncommon-route"] = buildModels(baseUrl);

    api.logger.info(`UncommonRoute provider registered (${MODELS.length} models)`);

    // 2. Register commands
    api.registerCommand({
      name: "route",
      description: "Show which model UncommonRoute would pick for a prompt",
      acceptsArgs: true,
      requireAuth: false,
      handler: async (ctx) => {
        const prompt = (ctx.args || ctx.commandBody || "").trim();
        if (!prompt) return { text: "Usage: /route <prompt>" };
        try {
          const resp = await fetch(`http://127.0.0.1:${port}/v1/chat/completions`, {
            method: "POST",
            headers: { "content-type": "application/json" },
            body: JSON.stringify({ model: "uncommon-route/auto", messages: [{ role: "user", content: `/debug ${prompt}` }] }),
            signal: AbortSignal.timeout(5000),
          });
          lastRequestId = resp.headers.get("x-uncommon-route-request-id");
          lastTier = resp.headers.get("x-uncommon-route-tier");
          const data = await resp.json();
          let text = data?.choices?.[0]?.message?.content || "No response";
          if (lastRequestId) {
            text += `\n\n_Rate this: \`/feedback ok\` · \`/feedback weak\` · \`/feedback strong\`_`;
          }
          return { text };
        } catch (err) {
          return { text: `Error: ${err.message}. Is proxy running?`, isError: true };
        }
      },
    });

    api.registerCommand({
      name: "spend",
      description: "View or manage spending limits (/spend set hourly 5.00)",
      acceptsArgs: true,
      requireAuth: false,
      handler: async (ctx) => {
        const args = (ctx.args || "").trim();
        const spendUrl = `http://127.0.0.1:${port}/v1/spend`;
        if (!args || args === "status") {
          const data = await fetchJson(spendUrl);
          if (!data) return { text: "Proxy not running. Restart gateway.", isError: true };
          const lines = ["**Spending Status**", ""];
          const { limits, spent, remaining, calls } = data;
          if (limits.per_request != null) lines.push(`Per-request: max $${limits.per_request.toFixed(2)}`);
          if (limits.hourly != null) lines.push(`Hourly: $${spent.hourly.toFixed(4)} / $${limits.hourly.toFixed(2)} ($${remaining.hourly?.toFixed(4)} left)`);
          if (limits.daily != null) lines.push(`Daily: $${spent.daily.toFixed(4)} / $${limits.daily.toFixed(2)} ($${remaining.daily?.toFixed(4)} left)`);
          if (limits.session != null) lines.push(`Session: $${spent.session.toFixed(4)} / $${limits.session.toFixed(2)} ($${remaining.session?.toFixed(4)} left)`);
          if (Object.keys(limits).length === 0) lines.push("No limits set. Use `/spend set hourly 5.00`");
          lines.push("", `Total calls: ${calls}`);
          return { text: lines.join("\n") };
        }
        const parts = args.split(/\s+/);
        if (parts[0] === "set" && parts.length >= 3) {
          const result = await postJson(spendUrl, { action: "set", window: parts[1], amount: parseFloat(parts[2]) });
          return result ? { text: `Set ${parts[1]} limit: $${parseFloat(parts[2]).toFixed(2)}` } : { text: "Failed", isError: true };
        }
        if (parts[0] === "clear" && parts.length >= 2) {
          const result = await postJson(spendUrl, { action: "clear", window: parts[1] });
          return result ? { text: `Cleared ${parts[1]} limit` } : { text: "Failed", isError: true };
        }
        return { text: "Usage: `/spend [status | set <window> <amount> | clear <window>]`\nWindows: per_request, hourly, daily, session" };
      },
    });

    let lastRequestId = null;
    let lastTier = null;

    api.registerCommand({
      name: "feedback",
      description: "Rate last routing decision: /feedback ok|weak|strong|status",
      acceptsArgs: true,
      requireAuth: false,
      handler: async (ctx) => {
        const args = (ctx.args || "").trim().toLowerCase();
        const feedbackUrl = `http://127.0.0.1:${port}/v1/feedback`;

        if (!args || args === "status") {
          const data = await fetchJson(feedbackUrl);
          if (!data) return { text: "Proxy not running.", isError: true };
          const lines = [
            "**Online Learning Status**",
            "",
            `Pending contexts: ${data.pending_contexts}`,
            `Total updates: ${data.total_online_updates}`,
            `Updates (last hour): ${data.updates_last_hour}`,
            `Online model: ${data.online_model_active ? "active" : "inactive (base model)"}`,
            "",
            "Usage: `/feedback ok` (correct) | `/feedback weak` (should be harder) | `/feedback strong` (should be easier)",
          ];
          return { text: lines.join("\n") };
        }

        if (["ok", "weak", "strong"].includes(args)) {
          if (!lastRequestId) {
            return { text: "No recent routing decision to give feedback on. Send a message with `uncommon-route/auto` first.", isError: true };
          }
          const result = await postJson(feedbackUrl, { request_id: lastRequestId, signal: args });
          if (!result) return { text: "Proxy not running.", isError: true };
          if (!result.ok) return { text: `Feedback failed: ${result.reason || result.action}`, isError: true };

          const emoji = { reinforced: "✓", updated: "↑", no_change: "—" }[result.action] || "•";
          const tierInfo = result.from_tier === result.to_tier
            ? `${result.from_tier} (reinforced)`
            : `${result.from_tier} → ${result.to_tier}`;
          lastRequestId = null;
          return { text: `${emoji} Feedback applied: ${tierInfo}  (total updates: ${result.total_updates})` };
        }

        if (args === "rollback") {
          const result = await postJson(feedbackUrl, { action: "rollback" });
          return { text: result?.ok ? "✓ Online weights rolled back to base model" : "Rollback failed" };
        }

        return { text: "Usage: `/feedback [ok|weak|strong|status|rollback]`\n• **ok** — tier was correct\n• **weak** — model was too weak, should route to harder tier\n• **strong** — model was overkill, should route to easier tier" };
      },
    });

    // 3. Register service for lifecycle
    api.registerService({
      id: "uncommon-route-proxy",
      start: () => {},
      stop: async () => {
        if (pyProc && !pyProc.killed) {
          pyProc.kill("SIGTERM");
          await sleep(1000);
          if (!pyProc.killed) pyProc.kill("SIGKILL");
          api.logger.info("UncommonRoute proxy stopped");
        }
        pyProc = null;
      },
    });

    // 4. Apply spend limits from config
    if (cfg.spendLimits) {
      const applyLimits = async () => {
        await sleep(3000);
        for (const [window, amount] of Object.entries(cfg.spendLimits)) {
          if (typeof amount === "number" && amount > 0) {
            await postJson(`http://127.0.0.1:${port}/v1/spend`, { action: "set", window, amount });
          }
        }
        api.logger.info("Spend limits applied from config");
      };
      applyLimits().catch(() => {});
    }

    // 5. Only spawn proxy in gateway mode
    const isGateway = process.argv.some((a) => a === "gateway" || a === "start" || a === "serve");
    if (!isGateway) {
      api.logger.info("Not in gateway mode — proxy starts with `openclaw gateway start`");
      return;
    }

    // 6. Auto-install Python deps + spawn proxy
    const bootstrap = async () => {
      const pythonPath = cfg.pythonPath || process.env.UNCOMMON_ROUTE_PYTHON || null;
      let python = pythonPath;

      if (!python || !isPythonPackageInstalled(python)) {
        api.logger.info("Checking Python dependencies...");
        python = ensurePythonDeps(api.logger);
        if (!python) {
          api.logger.error("Cannot start — Python setup failed. See errors above.");
          return;
        }
      }

      const args = ["-m", "uncommon_route.cli", "serve", "--port", String(port), "--upstream", upstream];
      pyProc = spawn(python, args, {
        stdio: ["ignore", "pipe", "pipe"],
        env: { ...process.env, PYTHONUNBUFFERED: "1" },
      });

      pyProc.stdout?.on("data", (chunk) => {
        const line = chunk.toString().trim();
        if (line) api.logger.info(`[proxy] ${line}`);
      });
      pyProc.stderr?.on("data", (chunk) => {
        const line = chunk.toString().trim();
        if (line) api.logger.warn(`[proxy] ${line}`);
      });
      pyProc.on("exit", (code) => {
        if (code !== null && code !== 0) api.logger.error(`Proxy exited with code ${code}`);
        pyProc = null;
      });

      api.logger.info(`Starting proxy on port ${port}...`);
      const healthy = await waitForHealth(port);
      if (healthy) {
        api.logger.info(`UncommonRoute ready at http://127.0.0.1:${port}`);
        api.logger.info(`Default model: uncommon-route/auto`);
      } else {
        api.logger.warn("Proxy health check timed out — may need more time to start");
      }
    };

    bootstrap().catch((err) => {
      api.logger.error(`Bootstrap failed: ${err.message}`);
    });
  },
};

export default plugin;
