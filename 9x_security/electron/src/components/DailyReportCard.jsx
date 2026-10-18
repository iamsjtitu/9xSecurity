import React, { useEffect, useState } from 'react';
import { FileText, Send, FolderOpen } from 'lucide-react';
import { api, BASE, getToken } from '../api';
import TimeField from './TimeField.jsx';

const ROWS = [
  ['truck', '🚛 Truck'], ['car', '🚗 Car'], ['bus', '🚌 Bus'], ['tractor', '🚜 Tractor'],
  ['two_wheeler', '🏍️ Two-wheeler'], ['person', '👤 Person'], ['other', '🚙 Other'],
];

export default function DailyReportCard({ s, set, showToast }) {
  const [today, setToday] = useState(null);
  const [busy, setBusy] = useState('');
  const [result, setResult] = useState('');
  const on = !!s.daily_report_enabled;

  const loadToday = () => api('/api/report/daily').then(setToday).catch(() => setToday(null));
  useEffect(() => {
    loadToday();
    const id = setInterval(loadToday, 60000);
    return () => clearInterval(id);
  }, []); // eslint-disable-line

  const sendNow = async () => {
    setBusy('send');
    setResult('PDF ban rahi hai aur bheji ja rahi hai… (10-30 sec)');
    try {
      const r = await api('/api/report/send', { method: 'POST', body: JSON.stringify({}), timeout: 180000 });
      setResult(r.detail);
      showToast(r.ok ? 'PDF report WhatsApp par SENT ✅' : 'Report bhejne me dikkat ❌ — neeche dekhein', r.ok ? 'success' : 'error');
      loadToday();
    } catch (e) {
      setResult(`Error: ${e.message}`);
      showToast(e.message, 'error');
    } finally {
      setBusy('');
    }
  };

  const openPdf = async () => {
    setBusy('open');
    try {
      const r = await api('/api/report/daily', { timeout: 60000 });
      setToday(r);
      if (window.native?.openFile) {
        const res = await window.native.openFile(r.pdf_path);
        if (res && res !== '') showToast(`PDF nahi khuli (${res}) — folder: ${r.folder}`, 'error');
      } else {
        window.open(`${BASE}${r.pdf_url}&t=${getToken()}`, '_blank');
      }
    } catch (e) {
      showToast(e.message, 'error');
    } finally {
      setBusy('');
    }
  };

  const total = today?.summary?.total || {};
  return (
    <div className="rounded-lg border border-slate-200 p-4 space-y-3" data-testid="daily-report-card">
      <div className="flex items-center gap-2.5 text-sm font-medium text-slate-800">
        <FileText size={15} className="text-[#1f6feb]" /> Daily PDF Report — din bhar ki Entry/Exit ginti WhatsApp par
      </div>
      <p className="text-xs text-slate-400 -mt-1">
        Roz set kiye time par ek PDF jayegi: har gate ke liye Truck / Car / Bus / Two-wheeler / Person ki Entry aur Exit ginti,
        saath me poori event list (time, gate, number). Har report <b>pichhle 24 ghante</b> (kal is time se aaj is time tak) cover
        karti hai — koi gaadi chhootti nahi, koi do baar nahi ginti. Upar wale numbers/groups par jayegi.
      </p>
      <label className="flex items-center gap-2.5 text-sm text-slate-700 cursor-pointer">
        <input type="checkbox" className="h-4 w-4 accent-[#1f6feb]" checked={on}
          onChange={(e) => set('daily_report_enabled', e.target.checked)} data-testid="daily-report-toggle" />
        Roz automatic PDF report bhejo
      </label>
      <div className="flex items-center flex-wrap gap-3 text-sm text-slate-700">
        <span>Har din is time par</span>
        <TimeField value={s.daily_report_time || '21:00'} onChange={(v) => set('daily_report_time', v)} disabled={!on} testid="daily-report-time" />
        {on && s.daily_report_next && (
          <span className="text-xs font-medium text-[#1f6feb]" data-testid="daily-report-next">→ agli PDF {s.daily_report_next}</span>
        )}
      </div>
      {s.daily_report_sent_at && (
        <p className="text-xs text-slate-500" data-testid="daily-report-last">
          Aakhri report bheji: {new Date(s.daily_report_sent_at).toLocaleString('en-IN', { dateStyle: 'medium', timeStyle: 'short' })}
        </p>
      )}
      <p className="text-xs text-slate-400">
        Save dabana na bhoolein. Computer report ke time band ho to on hote hi wo report apne aap chali jayegi.
      </p>

      {today?.summary && (
        <div className="rounded-md bg-slate-50 border border-slate-200 p-3" data-testid="daily-report-today">
          <div className="text-xs font-semibold text-slate-700 mb-1.5">
            {today.summary.partial ? 'Abhi tak' : 'Is report me'} — Entry {today.summary.entry} / Exit {today.summary.exit}
            {Object.keys(today.summary.gates || {}).length > 1 ? ` · ${Object.keys(today.summary.gates).length} gates` : ''}
          </div>
          <div className="text-[11px] text-slate-400 mb-1.5" data-testid="daily-report-period">{today.period}</div>
          <div className="grid grid-cols-2 gap-x-6 gap-y-0.5 text-xs text-slate-600">
            {ROWS.filter(([k]) => (total[k]?.Entry || total[k]?.Exit || k === 'truck' || k === 'car')).map(([k, label]) => (
              <div key={k} className="flex justify-between" data-testid={`daily-report-row-${k}`}>
                <span>{label}</span>
                <span className="font-mono">E {total[k]?.Entry ?? 0} / X {total[k]?.Exit ?? 0}</span>
              </div>
            ))}
          </div>
        </div>
      )}

      <div className="flex flex-wrap gap-3 pt-1">
        <button type="button" className="btn-ghost" onClick={openPdf} disabled={!!busy} data-testid="daily-report-open-btn">
          <FolderOpen size={14} /> PDF dekho
        </button>
        <button type="button" className="btn-ghost" onClick={sendNow} disabled={!!busy} data-testid="daily-report-send-btn">
          <Send size={14} /> {busy === 'send' ? 'Bhej rahe hain…' : 'Abhi WhatsApp par bhejo (test)'}
        </button>
      </div>
      {result && (
        <pre className="text-xs text-slate-700 whitespace-pre-wrap break-all font-mono leading-relaxed rounded-lg border border-slate-200 bg-slate-50 p-3"
          data-testid="daily-report-result">{result}</pre>
      )}
    </div>
  );
}
