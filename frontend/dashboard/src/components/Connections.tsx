import { useCallback, useEffect, useState } from "react";
import { motion } from "framer-motion";
import {
  deleteProvider,
  fetchConnections,
  fetchProviders,
  saveProvider,
  updateConnections,
  verifyProvider,
  type ConnectionState,
  type ProviderRecord,
} from "../api";
import { Card } from "./ui/Card";

interface Props {
  initialConnection: ConnectionState | null;
  onRefresh: () => void;
}

interface ProviderDraft {
  name: string;
  apiKey: string;
  baseUrl: string;
  modelsCsv: string;
  plan: string;
}

const EMPTY_PROVIDER: ProviderDraft = {
  name: "",
  apiKey: "",
  baseUrl: "",
  modelsCsv: "",
  plan: "",
};

export default function Connections({ initialConnection, onRefresh }: Props) {
  const [connection, setConnection] = useState<ConnectionState | null>(initialConnection);
  const [providers, setProviders] = useState<ProviderRecord[]>([]);
  const [upstream, setUpstream] = useState(initialConnection?.upstream ?? "");
  const [apiKey, setApiKey] = useState("");
  const [providerDraft, setProviderDraft] = useState<ProviderDraft>(EMPTY_PROVIDER);
  const [busyKey, setBusyKey] = useState<string | null>(null);
  const [message, setMessage] = useState<string>("");

  const load = useCallback(async () => {
    const [nextConnection, nextProviders] = await Promise.all([
      fetchConnections(),
      fetchProviders(),
    ]);
    if (nextConnection) {
      setConnection(nextConnection);
      setUpstream(nextConnection.upstream);
    }
    if (nextProviders) {
      setProviders(nextProviders.providers);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  async function handleSaveConnection() {
    setBusyKey("primary");
    setMessage("");
    const updated = await updateConnections(upstream.trim(), apiKey.trim());
    if (updated) {
      setConnection(updated);
      setUpstream(updated.upstream);
      setApiKey("");
      setMessage("Primary upstream updated.");
      onRefresh();
    } else {
      setMessage("Failed to update primary upstream.");
    }
    setBusyKey(null);
  }

  async function handleSaveProvider() {
    if (!providerDraft.name.trim() || !providerDraft.apiKey.trim()) {
      setMessage("Provider name and API key are required.");
      return;
    }
    setBusyKey("provider:add");
    setMessage("");
    const result = await saveProvider({
      name: providerDraft.name.trim().toLowerCase(),
      apiKey: providerDraft.apiKey.trim(),
      baseUrl: providerDraft.baseUrl.trim() || undefined,
      models: providerDraft.modelsCsv
        .split(",")
        .map((item) => item.trim())
        .filter(Boolean),
      plan: providerDraft.plan.trim() || undefined,
    });
    if (result) {
      setProviders(result.providers);
      setProviderDraft(EMPTY_PROVIDER);
      setMessage("Provider saved and loaded live.");
      onRefresh();
    } else {
      setMessage("Failed to save provider.");
    }
    setBusyKey(null);
  }

  async function handleRemoveProvider(name: string) {
    setBusyKey(`provider:remove:${name}`);
    setMessage("");
    const result = await deleteProvider(name);
    if (result) {
      setProviders(result.providers);
      setMessage(`Removed provider ${name}.`);
      onRefresh();
    } else {
      setMessage(`Failed to remove provider ${name}.`);
    }
    setBusyKey(null);
  }

  async function handleVerifyProvider(name: string) {
    setBusyKey(`provider:verify:${name}`);
    setMessage("");
    const result = await verifyProvider(name);
    if (result) {
      setMessage(result.ok ? `${name}: ${result.detail}` : `${name}: ${result.detail}`);
    } else {
      setMessage(`Failed to verify provider ${name}.`);
    }
    setBusyKey(null);
  }

  const connectionEditable = connection?.editable ?? true;

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-xl font-semibold tracking-tight text-[#111827]">Connections</h1>
        <p className="mt-1 text-[13px] font-medium text-[#6B7280]">
          Manage the primary upstream and live BYOK provider keys.
        </p>
      </div>

      {message ? (
        <div className="rounded-2xl bg-white px-4 py-3 text-[13px] font-medium text-[#4B5563] ring-1 ring-black/[0.04] shadow-sm">
          {message}
        </div>
      ) : null}

      <motion.div initial={{ opacity: 0, y: 12 }} animate={{ opacity: 1, y: 0 }}>
        <Card className="p-6">
          <div className="flex items-start justify-between gap-6">
            <div>
              <div className="text-[12px] font-medium text-[#9CA3AF] uppercase tracking-wider">Primary Upstream</div>
              <h2 className="mt-2 text-[18px] font-semibold tracking-tight text-[#111827]">Runtime connection</h2>
              <p className="mt-1 text-[13px] font-medium text-[#6B7280]">
                Source: {connection?.source ?? "unknown"} · Provider: {connection?.provider ?? "unknown"} ·
                {` `}{connection?.discovered ? "live catalog" : "static catalog"}
              </p>
            </div>
            <div className="rounded-2xl bg-gray-50/80 px-4 py-3 ring-1 ring-black/[0.04] text-right min-w-[240px]">
              <div className="text-[11px] font-medium uppercase tracking-wider text-[#9CA3AF]">Current</div>
              <div className="mt-2 text-[13px] font-mono text-[#111827] break-all">
                {connection?.upstream || "Not configured"}
              </div>
              <div className="mt-2 text-[12px] font-medium text-[#6B7280]">
                Key: {connection?.api_key_preview || "none"}
              </div>
            </div>
          </div>

          <div className="mt-6 grid grid-cols-2 gap-4">
            <Field
              label="Upstream URL"
              value={upstream}
              onChange={setUpstream}
              placeholder="https://api.commonstack.ai/v1"
              disabled={!connectionEditable || busyKey === "primary"}
            />
            <Field
              label="API Key"
              value={apiKey}
              onChange={setApiKey}
              placeholder={connection?.has_api_key ? "Leave empty to keep current key" : "sk-..."}
              disabled={!connectionEditable || busyKey === "primary"}
              type="password"
            />
          </div>

          <div className="mt-4 flex items-center justify-between">
            <div className="text-[12px] font-medium text-[#6B7280]">
              {connectionEditable
                ? "Changes apply live after validation succeeds."
                : `Locked by ${connection?.source ?? "external source"}.`}
            </div>
            <motion.button
              whileHover={{ scale: connectionEditable ? 1.02 : 1 }}
              whileTap={{ scale: connectionEditable ? 0.98 : 1 }}
              disabled={!connectionEditable || busyKey === "primary"}
              onClick={handleSaveConnection}
              className="rounded-xl bg-[#111827] px-5 py-2.5 text-[13px] font-medium text-white shadow-sm transition-colors hover:bg-black disabled:opacity-40"
            >
              Save Primary
            </motion.button>
          </div>
        </Card>
      </motion.div>

      <motion.div initial={{ opacity: 0, y: 12 }} animate={{ opacity: 1, y: 0 }} transition={{ delay: 0.05 }}>
        <Card className="p-6">
          <div className="flex items-center justify-between">
            <div>
              <div className="text-[12px] font-medium text-[#9CA3AF] uppercase tracking-wider">Provider Keys</div>
              <h2 className="mt-2 text-[18px] font-semibold tracking-tight text-[#111827]">Bring your own keys</h2>
              <p className="mt-1 text-[13px] font-medium text-[#6B7280]">
                Save provider credentials and make them available immediately to routing.
              </p>
            </div>
            <div className="text-[13px] font-medium text-[#6B7280]">
              {providers.length} configured
            </div>
          </div>

          <div className="mt-6 grid grid-cols-2 gap-4">
            <Field
              label="Provider Name"
              value={providerDraft.name}
              onChange={(value) => setProviderDraft((prev) => ({ ...prev, name: value }))}
              placeholder="openai"
              disabled={busyKey === "provider:add"}
            />
            <Field
              label="API Key"
              value={providerDraft.apiKey}
              onChange={(value) => setProviderDraft((prev) => ({ ...prev, apiKey: value }))}
              placeholder="sk-..."
              disabled={busyKey === "provider:add"}
              type="password"
            />
            <Field
              label="Base URL"
              value={providerDraft.baseUrl}
              onChange={(value) => setProviderDraft((prev) => ({ ...prev, baseUrl: value }))}
              placeholder="Optional override"
              disabled={busyKey === "provider:add"}
            />
            <Field
              label="Plan"
              value={providerDraft.plan}
              onChange={(value) => setProviderDraft((prev) => ({ ...prev, plan: value }))}
              placeholder="Optional note"
              disabled={busyKey === "provider:add"}
            />
          </div>

          <div className="mt-4">
            <label className="mb-1.5 block text-[12px] font-medium text-[#6B7280]">Models (comma separated)</label>
            <input
              value={providerDraft.modelsCsv}
              onChange={(e) => setProviderDraft((prev) => ({ ...prev, modelsCsv: e.target.value }))}
              placeholder="Optional explicit model list"
              disabled={busyKey === "provider:add"}
              className="w-full rounded-xl border border-black/[0.06] bg-white px-3 py-2.5 text-[13px] font-medium text-[#111827] shadow-sm focus:outline-none focus:ring-2 focus:ring-indigo-500/20"
            />
          </div>

          <div className="mt-4 flex justify-end">
            <motion.button
              whileHover={{ scale: 1.02 }}
              whileTap={{ scale: 0.98 }}
              disabled={busyKey === "provider:add"}
              onClick={handleSaveProvider}
              className="rounded-xl bg-[#111827] px-5 py-2.5 text-[13px] font-medium text-white shadow-sm transition-colors hover:bg-black disabled:opacity-40"
            >
              Add Provider
            </motion.button>
          </div>
        </Card>
      </motion.div>

      <motion.div initial={{ opacity: 0, y: 12 }} animate={{ opacity: 1, y: 0 }} transition={{ delay: 0.1 }}>
        <Card>
          <div className="border-b border-black/[0.04] px-6 py-4">
            <span className="text-[12px] font-medium uppercase tracking-wider text-[#9CA3AF]">Configured Providers</span>
          </div>
          <table className="w-full">
            <thead>
              <tr className="border-b border-black/[0.04]">
                <th className="px-6 py-3 text-left text-[11px] font-medium uppercase tracking-wider text-[#9CA3AF]">Name</th>
                <th className="px-6 py-3 text-left text-[11px] font-medium uppercase tracking-wider text-[#9CA3AF]">Base URL</th>
                <th className="px-6 py-3 text-left text-[11px] font-medium uppercase tracking-wider text-[#9CA3AF]">Models</th>
                <th className="px-6 py-3 text-left text-[11px] font-medium uppercase tracking-wider text-[#9CA3AF]">Key</th>
                <th className="px-6 py-3 text-right text-[11px] font-medium uppercase tracking-wider text-[#9CA3AF]"></th>
              </tr>
            </thead>
            <tbody>
              {providers.length === 0 ? (
                <tr>
                  <td colSpan={5} className="py-16 text-center text-[14px] font-medium text-[#9CA3AF]">
                    No provider keys saved yet.
                  </td>
                </tr>
              ) : (
                providers.map((provider) => (
                  <tr key={provider.name} className="border-b border-black/[0.03] last:border-0 hover:bg-gray-50 transition-colors">
                    <td className="px-6 py-4 text-[13px] font-semibold text-[#111827]">{provider.name}</td>
                    <td className="px-6 py-4 text-[12px] font-mono text-[#6B7280]">{provider.base_url || "—"}</td>
                    <td className="px-6 py-4 text-[13px] font-medium text-[#4B5563]">
                      {provider.model_count > 0 ? `${provider.model_count} models` : "Default set"}
                    </td>
                    <td className="px-6 py-4 text-[12px] font-mono text-[#6B7280]">{provider.api_key_preview || "—"}</td>
                    <td className="px-6 py-4">
                      <div className="flex justify-end gap-2">
                        <button
                          disabled={busyKey === `provider:verify:${provider.name}`}
                          onClick={() => handleVerifyProvider(provider.name)}
                          className="rounded-lg border border-black/[0.06] bg-white px-3 py-1.5 text-[12px] font-medium text-[#6B7280] shadow-sm transition-colors hover:bg-gray-50 hover:text-[#111827] disabled:opacity-40"
                        >
                          Verify
                        </button>
                        <button
                          disabled={busyKey === `provider:remove:${provider.name}`}
                          onClick={() => handleRemoveProvider(provider.name)}
                          className="rounded-lg border border-rose-200 bg-rose-50 px-3 py-1.5 text-[12px] font-medium text-rose-600 transition-colors hover:bg-rose-100 disabled:opacity-40"
                        >
                          Remove
                        </button>
                      </div>
                    </td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </Card>
      </motion.div>
    </div>
  );
}

interface FieldProps {
  label: string;
  value: string;
  onChange: (value: string) => void;
  placeholder: string;
  disabled?: boolean;
  type?: string;
}

function Field({ label, value, onChange, placeholder, disabled = false, type = "text" }: FieldProps) {
  return (
    <div>
      <label className="mb-1.5 block text-[12px] font-medium text-[#6B7280]">{label}</label>
      <input
        type={type}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder={placeholder}
        disabled={disabled}
        className="w-full rounded-xl border border-black/[0.06] bg-white px-3 py-2.5 text-[13px] font-medium text-[#111827] shadow-sm focus:outline-none focus:ring-2 focus:ring-indigo-500/20 disabled:opacity-50"
      />
    </div>
  );
}
