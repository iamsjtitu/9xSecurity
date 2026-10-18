import React, { useEffect, useState } from 'react';
import { ScanLine, RefreshCw } from 'lucide-react';
import { api, poll } from '../api';

export default function PlateCreditsCard({ onOpenSettings }) {
  const [u, setU] = useState(null);
  const [busy, setBusy] = useState(false);

  const load = async (refresh = false) => {
    try {
      if (refresh) setBusy(true);
      const r = await api(`/api/plate/cloud/usage${refresh ? '?refresh=1' : ''}`, { timeout: 30000 });
      setU(r);
    } catch (_) { /* noop */ } finally { setBusy(false); }
  };
  useEffect(() => poll(() => load(false), 5 * 60 * 1000), []); // eslint-disable-line

  if (!u || !u.key_set) return null; // no Plate Recognizer key -> nothing to show

  const pct = u.valid && u.limit ? Math.max(0, Math.min(100, Math.round((u.remaining / u.limit) * 100))) : 0;
  const tone = !u.valid ? 'rose' : u.critical ? 'rose' : u.low ? 'amber' : 'emerald';
  const bar = { rose: 'bg-rose-500', amber: 'bg-amber-500', emerald: 'bg-emerald-500' }[tone];
  const text = { rose: 'text-rose-700', amber: 'text-amber-700', emerald: 'text-emerald-700' }[tone];
  const resets = u.resets_on ? new Date(u.resets_on).toLocaleDateString('en-IN', { day: 'numeric', month: 'short' }) : '';

  return (
    <div className={`card p-5 space-y-2 ${tone === 'rose' ? 'border-rose-200' : tone === 'amber' ? 'border-amber-200' : ''}`} data-testid="plate-credits-card">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2 text-sm font-semibold text-slate-800">
          <ScanLine size={16} className="text-[#1f6feb]" /> Plate Recognizer credits
          {!u.enabled && <span className="rounded-full bg-slate-100 px-2 py-0.5 text-[10px] font-semibold text-slate-500" data-testid="plate-credits-off">OFF</span>}
        </div>
        <button type="button" className="text-slate-400 hover:text-slate-700" onClick={() => load(true)} disabled={busy} title="Abhi check karo" data-testid="plate-credits-refresh">
          <RefreshCw size={14} className={busy ? 'animate-spin' : ''} />
        </button>
      </div>
      {u.valid ? (
        <>
          <div className="flex items-end justify-between">
            <div className={`text-2xl font-bold tracking-tight ${text}`} data-testid="plate-credits-remaining">{u.remaining.toLocaleString('en-IN')} <span className="text-sm font-semibold text-slate-400">baaki</span></div>
            <div className="text-xs text-slate-500" data-testid="plate-credits-used">{u.used.toLocaleString('en-IN')} / {u.limit.toLocaleString('en-IN')} use hue</div>
          </div>
          <div className="h-2 w-full rounded-full bg-slate-100 overflow-hidden">
            <div className={`h-full ${bar} transition-all duration-500`} style={{ width: `${pct}%` }} />
          </div>
          <div className="text-[11px] text-slate-400">
            {resets ? `Reset: ${resets}` : 'Mahine ke hisaab se'}{u.checked_at ? ` · check ${new Date(u.checked_at).toLocaleTimeString('en-IN', { hour: '2-digit', minute: '2-digit' })}` : ''}
          </div>
          {(u.low || u.critical) && (
            <div className={`rounded-md px-3 py-2 text-xs font-semibold ${u.critical ? 'bg-rose-50 text-rose-800' : 'bg-amber-50 text-amber-800'}`} data-testid="plate-credits-warning">
              {u.critical ? 'Credits khatam hone wale hain — number padhna band ho jayega. platerecognizer.com par plan badhayein.' : 'Credits kam bache hain — is mahine ke liye plan dekh lein.'}
            </div>
          )}
        </>
      ) : (
        <div className="rounded-md bg-rose-50 px-3 py-2 text-xs font-semibold text-rose-800" data-testid="plate-credits-error">
          {u.message} {onOpenSettings && <button type="button" className="underline ml-1" onClick={onOpenSettings}>Settings</button>}
        </div>
      )}
    </div>
  );
}
