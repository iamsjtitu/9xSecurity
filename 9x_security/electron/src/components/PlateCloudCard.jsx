import React, { useState } from 'react';
import { ScanLine, KeyRound, ExternalLink } from 'lucide-react';
import { api } from '../api';

export default function PlateCloudCard({ s, set, showToast, save, busy }) {
  const [check, setCheck] = useState(null);
  const [checking, setChecking] = useState(false);
  const key = (s.pr_api_key || '').trim();

  const checkKey = async () => {
    setChecking(true);
    setCheck(null);
    try {
      const r = await api('/api/plate/cloud/test', { method: 'POST', body: JSON.stringify({ pr_api_key: key }), timeout: 60000 });
      setCheck(r);
      showToast(r.valid ? 'Plate Recognizer key sahi hai ✔' : `Key check fail: ${r.message}`, r.valid ? 'success' : 'error');
    } catch (e) {
      setCheck({ valid: false, message: e.message });
      showToast(e.message, 'error');
    } finally {
      setChecking(false);
    }
  };

  return (
    <div className="space-y-4" data-testid="plate-cloud-card">
      <h3 className="text-lg font-semibold text-slate-900 flex items-center gap-2">
        <ScanLine size={18} className="text-[#1f6feb]" /> Number Plate — Plate Recognizer (cloud AI)
      </h3>
      <p className="text-sm text-slate-600">
        India ki plates ke liye bana paid AI: local OCR se kahin zyada sahi. <b>Sirf 4-wheeler</b> (car / truck / bus / tractor)
        ki photo cloud par jaati hai — two-wheeler aur person kabhi nahi. Number WhatsApp photo + caption aur Daily PDF
        me aata hai; shak ho to <b>Not detected</b> (galat number kabhi nahi).
      </p>
      <label className="flex items-center gap-2.5 text-sm text-slate-700 cursor-pointer">
        <input type="checkbox" className="h-4 w-4 accent-[#1f6feb]" checked={!!s.pr_enabled}
          onChange={(e) => set('pr_enabled', e.target.checked)} data-testid="pr-enabled-toggle" />
        Plate Recognizer se number padho (apni API key)
      </label>
      <div>
        <label className="label">API Token (platerecognizer.com — har user apni key)</label>
        <div className="flex gap-2">
          <input type="text" className="input font-mono flex-1" value={s.pr_api_key || ''} spellCheck={false} autoComplete="off"
            placeholder="40 characters ka token, jaise 3b15…" onChange={(e) => set('pr_api_key', e.target.value)} data-testid="pr-api-key-input" />
          <button type="button" className="btn-ghost whitespace-nowrap" onClick={checkKey} disabled={checking || !key} data-testid="pr-key-check-btn">
            <KeyRound size={14} /> {checking ? 'Check…' : 'Key check karo'}
          </button>
        </div>
        <p className="mt-1 text-xs" data-testid="pr-api-key-status">
          {s.pr_api_key_set && key
            ? <span className="text-emerald-700">✔ Key saved hai ({key.length} characters)</span>
            : <span className="text-amber-700">Key saved nahi — token paste karke Save dabayein</span>}
        </p>
        {check && (
          <div className={`mt-2 rounded-lg border p-3 text-xs ${check.valid ? 'border-emerald-200 bg-emerald-50 text-emerald-900' : 'border-rose-200 bg-rose-50 text-rose-800'}`}
            data-testid="pr-key-check-result">
            <div className="font-semibold">{check.message}</div>
            {check.valid && (
              <div className="mt-1 text-emerald-800">
                Is mahine: {check.used} / {check.limit} lookups · {check.remaining} baaki
                {check.resets_on ? ` · reset: ${String(check.resets_on).slice(0, 10)}` : ''}
              </div>
            )}
          </div>
        )}
      </div>
      <div className="rounded-lg border border-slate-200 bg-slate-50 p-3 text-xs text-slate-600 space-y-1" data-testid="pr-key-help">
        <div className="font-semibold text-slate-700">API key kahan milegi?</div>
        <ol className="list-decimal pl-4 space-y-0.5">
          <li><b>app.platerecognizer.com</b> par login karein (jo account banaya hai).</li>
          <li>Left menu me <b>Snapshot</b> → <b>Cloud API</b> (ya upar <b>Account</b> → <b>API Token</b>).</li>
          <li>Wahan <b>API Token</b> dikhega (40 characters) — copy karke upar paste karein, Save dabayein, phir "Key check karo".</li>
        </ol>
        <div>Free plan: 2,500 plate/mahina (1 lookup/second). Zyada gaadiyan ho to unka paid plan lein — key wahi rehti hai.</div>
        <a className="inline-flex items-center gap-1 text-[#1f6feb] hover:underline" href="https://app.platerecognizer.com/accounts/plan/" target="_blank" rel="noreferrer"
          data-testid="pr-dashboard-link"><ExternalLink size={12} /> app.platerecognizer.com/accounts/plan</a>
      </div>
      <div className="space-y-2" data-testid="pr-mode-group">
        <div className="text-sm font-medium text-slate-800">Kab use ho</div>
        {[
          ['always', 'Cloud pehle (sabse sahi) — har 4-wheeler par 1–2 lookup; cloud fail ho to local OCR'],
          ['fallback', 'Pehle local OCR, cloud sirf jab local number na padh paaye (credits bachao)'],
        ].map(([v, label]) => (
          <label key={v} className="flex items-start gap-2.5 text-sm text-slate-700 cursor-pointer">
            <input type="radio" name="pr_mode" className="mt-0.5 accent-[#1f6feb]" checked={(s.pr_mode || 'always') === v}
              onChange={() => set('pr_mode', v)} data-testid={`pr-mode-${v}`} />
            {label}
          </label>
        ))}
      </div>
      <p className="text-xs text-slate-400">
        Camera side bhi zaroori: plate frame me kam se kam 100–150 px chaudi dikhe (camera ka main stream 1080p/1440p), gaadi ke
        saamne/neeche angle, raat ke liye IR ya white light. Dhool/keechad/tirchi plate koi AI nahi padh sakta — tab Not detected aayega.
      </p>
      <button className="btn-primary" onClick={save} disabled={busy} data-testid="plate-save-btn">Save</button>
    </div>
  );
}
