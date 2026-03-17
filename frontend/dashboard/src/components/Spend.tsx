import { useState } from "react";
import { motion } from "framer-motion";
import { setSpendLimit, clearSpendLimit, type Spend } from "../api";
import { Card } from "./ui/Card";

const WINDOWS = ["per_request", "hourly", "daily"];

interface Props {
  spend: Spend | null;
  onRefresh: () => void;
}

export default function SpendPanel({ spend, onRefresh }: Props) {
  const [window, setWindow] = useState("hourly");
  const [amount, setAmount] = useState("5.00");
  const [busy, setBusy] = useState(false);

  const limits = spend?.limits ?? {};
  const spent = spend?.spent ?? {};
  const remaining = spend?.remaining ?? {};
  const calls = spend?.calls ?? 0;
  const activeWindows = WINDOWS.filter((w) => limits[w] != null);

  async function handleSet() {
    const val = parseFloat(amount);
    if (isNaN(val)) return;
    setBusy(true);
    await setSpendLimit(window, val);
    onRefresh();
    setBusy(false);
  }

  async function handleClear() {
    setBusy(true);
    await clearSpendLimit(window);
    onRefresh();
    setBusy(false);
  }

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-xl font-semibold tracking-tight text-[#111827]">Budget</h1>
        <p className="text-[13px] font-medium text-[#6B7280] mt-1">{calls} total calls</p>
      </div>

      <motion.div initial={{ opacity: 0, y: 12 }} animate={{ opacity: 1, y: 0 }}>
        <Card className="p-6">
          <div className="text-[12px] font-medium text-[#9CA3AF] uppercase tracking-wider mb-4">Set Limit</div>
          <div className="flex items-end gap-3">
            <div>
              <label className="block text-[12px] font-medium text-[#6B7280] mb-1.5">Window</label>
              <select value={window} onChange={(e) => setWindow(e.target.value)}
                className="bg-white border border-black/[0.06] shadow-sm text-[#111827] rounded-xl px-3 py-2.5 text-[13px] font-medium focus:outline-none focus:ring-2 focus:ring-indigo-500/20"
              >
                {WINDOWS.map((w) => <option key={w} value={w}>{w}</option>)}
              </select>
            </div>
            <div>
              <label className="block text-[12px] font-medium text-[#6B7280] mb-1.5">Amount ($)</label>
              <input type="number" value={amount} onChange={(e) => setAmount(e.target.value)} min={0} step={0.5}
                className="bg-white border border-black/[0.06] shadow-sm text-[#111827] rounded-xl px-3 py-2.5 text-[13px] font-medium w-28 focus:outline-none focus:ring-2 focus:ring-indigo-500/20"
              />
            </div>
            <motion.button whileHover={{ scale: 1.02 }} whileTap={{ scale: 0.98 }} disabled={busy} onClick={handleSet}
              className="bg-[#111827] text-white px-5 py-2.5 rounded-xl text-[13px] font-medium shadow-sm hover:bg-black transition-colors disabled:opacity-40"
            >Set Limit</motion.button>
            <motion.button whileHover={{ scale: 1.02 }} whileTap={{ scale: 0.98 }} disabled={busy} onClick={handleClear}
              className="bg-white border border-black/[0.06] shadow-sm text-[#6B7280] px-5 py-2.5 rounded-xl text-[13px] font-medium hover:text-[#111827] hover:bg-gray-50 transition-colors disabled:opacity-40"
            >Clear</motion.button>
          </div>
        </Card>
      </motion.div>

      <motion.div initial={{ opacity: 0, y: 12 }} animate={{ opacity: 1, y: 0 }} transition={{ delay: 0.05 }}>
        <Card>
          <div className="px-6 py-4 border-b border-black/[0.04]">
            <span className="text-[12px] font-medium text-[#9CA3AF] uppercase tracking-wider">Current Limits</span>
          </div>
          <table className="w-full">
            <thead>
              <tr className="border-b border-black/[0.04]">
                <th className="text-left text-[11px] font-medium text-[#9CA3AF] uppercase tracking-wider py-3 px-6">Window</th>
                <th className="text-right text-[11px] font-medium text-[#9CA3AF] uppercase tracking-wider py-3 px-6">Limit</th>
                <th className="text-right text-[11px] font-medium text-[#9CA3AF] uppercase tracking-wider py-3 px-6">Spent</th>
                <th className="text-right text-[11px] font-medium text-[#9CA3AF] uppercase tracking-wider py-3 px-6">Remaining</th>
              </tr>
            </thead>
            <tbody>
              {activeWindows.length > 0 ? activeWindows.map((w) => {
                const isLow = remaining[w] != null && remaining[w] < limits[w] * 0.2;
                return (
                  <tr key={w} className="border-b border-black/[0.03] last:border-0 hover:bg-gray-50 transition-colors">
                    <td className="text-[13px] font-medium text-[#4B5563] py-3.5 px-6">{w}</td>
                    <td className="text-right font-mono text-[13px] text-[#111827] py-3.5 px-6">${limits[w].toFixed(2)}</td>
                    <td className="text-right font-mono text-[13px] text-[#6B7280] py-3.5 px-6">${(spent[w] ?? 0).toFixed(4)}</td>
                    <td className={`text-right font-mono text-[13px] font-semibold py-3.5 px-6 ${isLow ? "text-rose-500" : "text-emerald-600"}`}>
                      {remaining[w] != null ? `$${remaining[w].toFixed(4)}` : "—"}
                    </td>
                  </tr>
                );
              }) : (
                <tr><td colSpan={4} className="py-16 text-center text-[14px] font-medium text-[#9CA3AF]">No limits set</td></tr>
              )}
            </tbody>
          </table>
        </Card>
      </motion.div>
    </div>
  );
}
