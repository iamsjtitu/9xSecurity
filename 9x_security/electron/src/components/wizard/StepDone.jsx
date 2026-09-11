import React, { useEffect, useState } from 'react';
import { CheckCircle2, Circle, Rocket } from 'lucide-react';
import { api } from '../../api';

// Step 4: summary + reliability toggles, then mark setup done.
export default function StepDone({ state, showToast, onFinish, onBack }) {
  const [autoStart, setAutoStart] = useState(null);
  const [autoConnect, setAutoConnect] = useState(true);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (window.native?.getAutoStart) window.native.getAutoStart().then(setAutoStart).catch(() => setAutoStart({ supported: false }));
    else setAutoStart({ supported: false, enabled: false });
    api('/api/settings').then((d) => setAutoConnect(d.auto_connect !== false)).catch(() => {});
  }, []);

  const toggleAutoStart = async (on) => {
    try { const r = await window.native.setAutoStart(on); setAutoStart({ supported: true, enabled: r.enabled }); } catch (e) { showToast(e.message, 'error'); }
  };
  const toggleAutoConnect = async (on) => {
    setAutoConnect(on);
    try { await api('/api/settings', { method: 'POST', body: JSON.stringify({ auto_connect: on }) }); } catch (e) { showToast(e.message, 'error'); }
  };
  const finish = async () => {
    setBusy(true);
    try { await api('/api/options', { method: 'POST', body: JSON.stringify({ setup_done: true }) }); onFinish(); } catch (e) { showToast(e.message, 'error'); setBusy(false); }
  };

  const Row = ({ ok, label, detail, testid }) => (
    <li className="flex items-start gap-3" data-testid={testid}>
      {ok ? <CheckCircle2 size={18} className="text-emerald-600 shrink-0 mt-0.5" /> : <Circle size={18} className="text-slate-300 shrink-0 mt-0.5" />}
      <div><div className="text-sm font-medium text-slate-900">{label}</div><div className="text-xs text-slate-500">{detail}</div></div>
    </li>
  );
  return (
    <div className="grid grid-cols-5 gap-6" data-testid="wizard-step-done">
      <div className="col-span-3 space-y-5">
        <ul className="space-y-3">
          <Row ok={state.connected} label="Camera" detail={state.connected ? 'Live — AI gaadiyan dekh raha hai' : 'Connected nahi — Dashboard se Connect karein'} testid="wizard-sum-camera" />
          <Row ok={!!state.line} label="Detection line" detail={state.line ? `Set hai · Entry direction: ${state.entry_direction === 'pos' ? 'arrow side' : 'ulti side'}` : 'Line nahi bani'} testid="wizard-sum-line" />
          <Row ok={state.wa_enabled} label="WhatsApp alerts" detail={state.wa_enabled ? 'ON — har crossing par photo jayega' : 'OFF — Settings > WhatsApp se on kar sakte hain'} testid="wizard-sum-wa" />
        </ul>
        <div className="rounded-lg border border-slate-200 bg-slate-50 p-4 space-y-3">
          <div className="text-sm font-semibold text-slate-900">Site 24×7 chalti rahe</div>
          <label className="flex items-center gap-3 text-sm text-slate-700 cursor-pointer">
            <input type="checkbox" className="h-4 w-4 accent-[#1f6feb]" checked={autoConnect} onChange={(e) => toggleAutoConnect(e.target.checked)} data-testid="wizard-auto-connect" />
            Engine start hone par camera apne aap connect (power cut / reboot ke baad)
          </label>
          <label className={`flex items-center gap-3 text-sm cursor-pointer ${autoStart?.supported ? 'text-slate-700' : 'text-slate-400'}`}>
            <input type="checkbox" className="h-4 w-4 accent-[#1f6feb]" checked={!!autoStart?.enabled} disabled={!autoStart?.supported} onChange={(e) => toggleAutoStart(e.target.checked)} data-testid="wizard-auto-start" />
            Windows start hone par 9x Security apne aap chalu {autoStart?.supported ? '' : '(sirf installed Windows app me)'}
          </label>
        </div>
      </div>
      <div className="col-span-2 flex flex-col justify-between">
        <div className="rounded-lg bg-[#0f172a] text-slate-100 p-5 space-y-2">
          <Rocket size={22} className="text-[#facc15]" />
          <div className="text-lg font-semibold">Site live hai!</div>
          <p className="text-sm text-slate-300">Ab har gaadi ke yellow line cross karne par photo + Entry/Exit record banega. Dashboard par live counters aur log dekhein.</p>
        </div>
        <div className="flex gap-2 pt-4">
          <button className="btn-ghost" onClick={onBack} data-testid="wizard-back-btn">← WhatsApp</button>
          <button className="btn-primary flex-1 justify-center" onClick={finish} disabled={busy} data-testid="wizard-finish-btn">Dashboard kholo</button>
        </div>
      </div>
    </div>
  );
}
