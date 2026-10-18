import React, { useState } from 'react';
import { FileText, FileSpreadsheet, CalendarRange } from 'lucide-react';
import { api, BASE, getToken } from '../api';

const iso = (d) => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;

const PRESETS = [
  ['Aaj', () => { const t = new Date(); return [iso(t), iso(t)]; }],
  ['7 din', () => { const t = new Date(); const s = new Date(); s.setDate(t.getDate() - 6); return [iso(s), iso(t)]; }],
  ['Is mahina', () => { const t = new Date(); return [iso(new Date(t.getFullYear(), t.getMonth(), 1)), iso(t)]; }],
  ['Pichhla mahina', () => { const t = new Date(); return [iso(new Date(t.getFullYear(), t.getMonth() - 1, 1)), iso(new Date(t.getFullYear(), t.getMonth(), 0))]; }],
];

export default function ReportRangeCard({ showToast }) {
  const [[start, end], setRange] = useState(PRESETS[1][1]());
  const [busy, setBusy] = useState('');
  const [last, setLast] = useState(null);

  const download = async (fmt) => {
    if (!start || !end) return;
    if (end < start) { showToast("'Se' date 'Tak' date se pehle honi chahiye", 'error'); return; }
    setBusy(fmt);
    try {
      const r = await api(`/api/report/range?start=${start}&end=${end}&fmt=${fmt}`, { timeout: 180000 });
      setLast(r);
      if (window.native?.openFile) {
        const res = await window.native.openFile(r.path);
        if (res && res !== '') showToast(`File nahi khuli (${res}) — folder: ${r.folder}`, 'error');
        else showToast(`${fmt === 'xlsx' ? 'Excel' : 'PDF'} ban gayi — ${r.summary.events} events (${r.period})`, 'success');
      } else {
        window.open(`${BASE}${fmt === 'xlsx' ? r.xlsx_url : r.pdf_url}&t=${getToken()}`, '_blank');
        showToast(`${fmt === 'xlsx' ? 'Excel' : 'PDF'} download ho rahi hai — ${r.summary.events} events`, 'success');
      }
    } catch (e) {
      showToast(e.message, 'error');
    } finally {
      setBusy('');
    }
  };

  return (
    <div className="card p-5 space-y-3" data-testid="report-range-card">
      <div className="flex items-center gap-2 text-sm font-semibold text-slate-800">
        <CalendarRange size={16} className="text-[#1f6feb]" /> Report Download — koi bhi date range
      </div>
      <div className="flex flex-wrap items-end gap-3">
        <div className="flex flex-wrap gap-1.5">
          {PRESETS.map(([label, fn]) => (
            <button key={label} type="button" onClick={() => setRange(fn())}
              className="rounded-full border border-slate-200 bg-white px-3 py-1.5 text-xs font-semibold text-slate-600 hover:bg-slate-100 transition-colors"
              data-testid={`report-preset-${label.replace(/\s+/g, '-').toLowerCase()}`}>{label}</button>
          ))}
        </div>
        <label className="text-xs text-slate-500">Se
          <input type="date" className="input !w-auto mt-1" value={start} max={end || undefined} onChange={(e) => setRange([e.target.value, end])} data-testid="report-range-start" />
        </label>
        <label className="text-xs text-slate-500">Tak
          <input type="date" className="input !w-auto mt-1" value={end} min={start || undefined} onChange={(e) => setRange([start, e.target.value])} data-testid="report-range-end" />
        </label>
        <div className="flex gap-2 ml-auto">
          <button type="button" className="btn-primary" onClick={() => download('pdf')} disabled={!!busy} data-testid="report-range-pdf-btn">
            <FileText size={14} /> {busy === 'pdf' ? 'Ban rahi…' : 'PDF'}
          </button>
          <button type="button" className="btn-ghost" onClick={() => download('xlsx')} disabled={!!busy} data-testid="report-range-xlsx-btn">
            <FileSpreadsheet size={14} /> {busy === 'xlsx' ? 'Ban rahi…' : 'Excel'}
          </button>
        </div>
      </div>
      {last && (
        <div className="text-xs text-slate-500" data-testid="report-range-result">
          {last.period}: <b>{last.summary.entry}</b> Entry / <b>{last.summary.exit}</b> Exit · {last.summary.events} events
          {last.summary.total?.truck && ` · Truck ${last.summary.total.truck.Entry}/${last.summary.total.truck.Exit}`}
        </div>
      )}
      <p className="text-[11px] text-slate-400">Gate-wise aur din-wise ginti + har gaadi ki list (time, gate, number). Excel me photo ka path bhi hai.</p>
    </div>
  );
}
