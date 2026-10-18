import React, { useRef, useState } from 'react';
import { Search, X, ImageOff, LogIn, LogOut } from 'lucide-react';
import { api, snapshotUrl } from '../api';
import PlateBadge from './PlateBadge.jsx';

const fmt = (ts) => {
  const d = new Date(ts);
  return Number.isNaN(d.getTime()) ? ts : d.toLocaleString('en-IN', { day: '2-digit', month: 'short', year: 'numeric', hour: 'numeric', minute: '2-digit' });
};

export default function PlateSearchPanel() {
  const [q, setQ] = useState('');
  const [res, setRes] = useState(null);
  const [err, setErr] = useState('');
  const [busy, setBusy] = useState(false);
  const [preview, setPreview] = useState(null);
  const timer = useRef(null);

  const search = async (text) => {
    const clean = (text || '').toUpperCase().replace(/[^A-Z0-9]/g, '');
    if (clean.length < 2) { setRes(null); setErr(''); return; }
    setBusy(true);
    try {
      const r = await api(`/api/plate/search?q=${encodeURIComponent(clean)}`);
      setRes(r);
      setErr('');
    } catch (e) {
      setErr(e.message);
      setRes(null);
    } finally {
      setBusy(false);
    }
  };
  const onInput = (v) => {
    setQ(v);
    if (timer.current) clearTimeout(timer.current);
    timer.current = setTimeout(() => search(v), 350);
  };

  return (
    <div className="card" data-testid="plate-search-panel">
      <div className="flex flex-wrap items-center gap-3 px-5 py-4 border-b border-slate-200">
        <h3 className="text-base font-semibold text-slate-800 mr-auto">Gaadi Number Search</h3>
        <div className="relative w-full sm:w-96">
          <Search size={16} className="absolute left-3 top-3 text-slate-400" />
          <input
            className="input pl-9 font-mono uppercase tracking-wider"
            placeholder="Number ya aakhri 4 digit, jaise MH12AB1234 ya 1234"
            value={q}
            onChange={(e) => onInput(e.target.value)}
            onKeyDown={(e) => e.key === 'Enter' && search(q)}
            data-testid="plate-search-main-input"
          />
          {q && (
            <button type="button" onClick={() => { setQ(''); setRes(null); setErr(''); }} className="absolute right-2.5 top-2.5 text-slate-400 hover:text-slate-700" data-testid="plate-search-main-clear">
              <X size={15} />
            </button>
          )}
        </div>
      </div>

      {err && <div className="px-5 py-3 text-xs font-semibold text-rose-700" data-testid="plate-search-error">{err}</div>}
      {!res && !err && (
        <div className="px-5 py-6 text-sm text-slate-400" data-testid="plate-search-empty">
          Number likhein — us gaadi ki saari Entry/Exit photo ke saath yahan dikhengi (saare din, saare gate).
        </div>
      )}
      {res && (
        <>
          <div className="flex flex-wrap items-center gap-2 px-5 py-3 border-b border-slate-100 bg-slate-50/60 text-xs" data-testid="plate-search-summary">
            <span className="font-mono font-bold text-slate-800 text-sm">{res.q}</span>
            <span className="inline-flex items-center gap-1 rounded-full bg-emerald-100 px-2.5 py-1 font-semibold text-emerald-800"><LogIn size={12} /> {res.entries} Entry</span>
            <span className="inline-flex items-center gap-1 rounded-full bg-rose-100 px-2.5 py-1 font-semibold text-rose-700"><LogOut size={12} /> {res.exits} Exit</span>
            {res.fuzzy && <span className="text-amber-700 font-semibold">· milte-julte number (beech ke letters chhod kar)</span>}
            {res.plates.length > 1 && <span className="text-slate-500">· {res.plates.length} alag number match: {res.plates.slice(0, 6).join(', ')}{res.plates.length > 6 ? '…' : ''}</span>}
            {res.events.length > 0 && <span className="text-slate-400 ml-auto">Pehli baar {fmt(res.first_seen)} · Aakhri {fmt(res.last_seen)}</span>}
            {busy && <span className="text-slate-400">…</span>}
          </div>
          {res.events.length === 0 ? (
            <div className="px-5 py-8 text-center text-sm text-slate-400" data-testid="plate-search-none">
              <ImageOff size={26} className="mx-auto mb-2" strokeWidth={1.2} /> "{res.q}" ka koi record nahi mila
            </div>
          ) : (
            <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-5 xl:grid-cols-6 gap-3 p-5 max-h-[480px] overflow-y-auto" data-testid="plate-search-results">
              {res.events.map((r) => (
                <button type="button" key={r.id} onClick={() => setPreview(r)} className="text-left rounded-lg border border-slate-200 bg-white overflow-hidden hover:shadow-md hover:border-[#1f6feb]/50 transition-all duration-200" data-testid={`plate-result-${r.id}`}>
                  {r.image_path ? (
                    <img src={snapshotUrl(r.image_path)} alt="snap" className="h-28 w-full object-cover bg-slate-200" loading="lazy" />
                  ) : (
                    <div className="h-28 w-full bg-slate-100 text-[10px] text-slate-400 flex items-center justify-center">photo nahi</div>
                  )}
                  <div className="p-2 space-y-1">
                    <div className="flex items-center justify-between">
                      <span className={`rounded-full px-2 py-0.5 text-[10px] font-bold ${r.direction === 'Entry' ? 'bg-emerald-100 text-emerald-800' : 'bg-rose-100 text-rose-700'}`}>{r.direction}</span>
                      <span className="text-[10px] font-semibold uppercase text-slate-500">{r.vehicle_type}</span>
                    </div>
                    <div className="font-mono text-xs font-bold text-slate-800 truncate">{r.plate || '—'}</div>
                    <div className="text-[10px] text-slate-500">{fmt(r.timestamp)}</div>
                    {r.gate && <div className="text-[10px] text-slate-400 truncate">Gate: {r.gate}</div>}
                  </div>
                </button>
              ))}
            </div>
          )}
        </>
      )}

      {preview && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-6" onClick={() => setPreview(null)} data-testid="plate-search-modal">
          <div className="card max-w-4xl w-full overflow-hidden" onClick={(e) => e.stopPropagation()}>
            <div className="flex items-center justify-between px-5 py-3 border-b border-slate-200">
              <div className="text-sm font-semibold text-slate-800 flex items-center gap-2 flex-wrap">
                {preview.direction} — {String(preview.vehicle_type).toUpperCase()} · {fmt(preview.timestamp)}
                {preview.gate && <span className="text-slate-500 font-normal">· Gate: {preview.gate}</span>}
                <PlateBadge plate={preview.plate} status={preview.plate_status} source={preview.plate_source} testid="plate-search-modal-plate" />
              </div>
              <button type="button" onClick={() => setPreview(null)} className="text-slate-400 hover:text-slate-700" data-testid="plate-search-modal-close"><X size={18} /></button>
            </div>
            {preview.image_path ? (
              <img src={snapshotUrl(preview.image_path)} alt="snapshot" className="w-full object-contain max-h-[70vh] bg-black" />
            ) : (
              <div className="h-48 flex items-center justify-center text-slate-400 text-sm bg-slate-100"><ImageOff size={22} className="mr-2" strokeWidth={1.2} /> photo save nahi hui</div>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
