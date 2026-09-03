import React, { useCallback, useEffect, useRef, useState } from 'react';
import { ImageOff, X, LogIn, LogOut as LogOutIcon, Search } from 'lucide-react';
import { api, snapshotUrl } from '../api';

const iso = (d) =>
  `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
const today = () => iso(new Date());

const fmt12 = (t) => {
  const m = /^(\d{1,2}):(\d{2})(?::(\d{2}))?/.exec(t || '');
  if (!m) return t;
  let h = parseInt(m[1], 10);
  const ap = h >= 12 ? 'PM' : 'AM';
  h = h % 12 || 12;
  return `${h}:${m[2]}${m[3] ? `:${m[3]}` : ''} ${ap}`;
};

const last7Days = () => {
  const out = [];
  for (let i = 0; i < 7; i++) {
    const d = new Date();
    d.setDate(d.getDate() - i);
    out.push({
      value: iso(d),
      label: i === 0 ? 'Aaj' : i === 1 ? 'Kal' : d.toLocaleDateString('en-IN', { day: 'numeric', month: 'short' }),
    });
  }
  return out;
};

export default function EventsTable({ connected }) {
  const [date, setDate] = useState(today());
  const [direction, setDirection] = useState('All');
  const [rows, setRows] = useState([]);
  const [showAll, setShowAll] = useState(false);
  const [preview, setPreview] = useState(null);
  const [plateQ, setPlateQ] = useState('');
  const debounceRef = useRef(null);

  const fetchRows = useCallback(async (o = {}) => {
    const all = o.all !== undefined ? o.all : showAll;
    const d = o.date !== undefined ? o.date : date;
    const dir = o.direction !== undefined ? o.direction : direction;
    const pq = o.plate !== undefined ? o.plate : plateQ;
    try {
      const base = pq.trim() ? `?all=1&direction=${dir}` : all ? `?all=1&direction=${dir}` : `?date=${d}&direction=${dir}`;
      const q = base + (pq.trim() ? `&plate=${encodeURIComponent(pq.trim())}` : '');
      const r = await api(`/api/events${q}`);
      setRows(r.events || []);
    } catch (_) { /* noop */ }
  }, [date, direction, showAll, plateQ]);

  useEffect(() => { fetchRows(); }, []); // eslint-disable-line
  useEffect(() => {
    const id = setInterval(() => fetchRows(), 5000);
    return () => clearInterval(id);
  }, [fetchRows]);

  const pickDate = (d) => { setPlateQ(''); setDate(d); setShowAll(false); fetchRows({ date: d, all: false, plate: '' }); };
  const pickDir = (dir) => { setDirection(dir); fetchRows({ direction: dir }); };

  const onPlateInput = (v) => {
    setPlateQ(v);
    if (debounceRef.current) clearTimeout(debounceRef.current);
    debounceRef.current = setTimeout(() => fetchRows({ plate: v }), 400);
  };

  const entries = rows.filter((r) => r.direction === 'Entry').length;
  const exits = rows.length - entries;

  const DirBtn = ({ v, activeCls }) => (
    <button
      onClick={() => pickDir(v)}
      data-testid={`filter-${v.toLowerCase()}`}
      className={`px-3.5 py-1.5 text-sm font-semibold transition-colors duration-200 ${
        direction === v ? activeCls : 'bg-white text-slate-600 hover:bg-slate-50'
      }`}
    >
      {v}
    </button>
  );

  const Badge = ({ d }) => (
    <span
      className={`inline-flex rounded-full px-2.5 py-1 text-xs font-semibold ${
        d === 'Entry' ? 'bg-emerald-100 text-emerald-800' : 'bg-rose-100 text-rose-700'
      }`}
    >
      {d}
    </span>
  );

  return (
    <div className="card flex flex-col" data-testid="events-panel">
      <div className="flex flex-wrap items-center gap-3 px-5 py-4 border-b border-slate-200">
        <h3 className="text-base font-semibold text-slate-800">Entry / Exit Log</h3>
        <div className="flex items-center gap-2 text-xs font-semibold mr-auto" data-testid="events-summary">
          <span className="inline-flex items-center gap-1 rounded-full bg-emerald-100 px-2.5 py-1 text-emerald-800">
            <LogIn size={12} /> {entries} Entry
          </span>
          <span className="inline-flex items-center gap-1 rounded-full bg-rose-100 px-2.5 py-1 text-rose-700">
            <LogOutIcon size={12} /> {exits} Exit
          </span>
        </div>
        <div className="relative">
          <Search size={15} className="absolute left-3 top-3 text-slate-400" />
          <input
            className="input !w-52 pl-9 font-mono uppercase"
            placeholder="Plate search…"
            value={plateQ}
            onChange={(e) => onPlateInput(e.target.value)}
            data-testid="plate-search-input"
          />
          {plateQ && (
            <button
              onClick={() => { setPlateQ(''); fetchRows({ plate: '' }); }}
              className="absolute right-2.5 top-2.5 text-slate-400 hover:text-slate-700"
              data-testid="plate-search-clear"
            >
              <X size={15} />
            </button>
          )}
        </div>
        <div className="inline-flex rounded-lg border border-slate-300 overflow-hidden" data-testid="direction-filters">
          <DirBtn v="All" activeCls="bg-slate-800 text-white" />
          <DirBtn v="Entry" activeCls="bg-emerald-600 text-white" />
          <DirBtn v="Exit" activeCls="bg-rose-600 text-white" />
        </div>
        <input
          type="date"
          className="input !w-auto"
          value={date}
          onChange={(e) => pickDate(e.target.value)}
          data-testid="events-date-filter"
        />
        <button
          className={showAll ? 'btn-primary' : 'btn-ghost'}
          onClick={() => { setShowAll(true); fetchRows({ all: true }); }}
          data-testid="events-showall-btn"
        >
          Show All
        </button>
      </div>

      <div className="flex flex-wrap items-center gap-2 px-5 py-3 border-b border-slate-100 bg-slate-50/60">
        {plateQ.trim() ? (
          <span className="text-xs font-semibold text-[#1f6feb]" data-testid="plate-search-hint">
            Plate search: saare dino me "{plateQ.trim().toUpperCase()}" ke records dikh rahe hain
          </span>
        ) : (
          <>
            <span className="text-xs font-semibold uppercase tracking-wider text-slate-400 mr-1">Pichhle 7 din:</span>
            {last7Days().map((d) => (
              <button
                key={d.value}
                onClick={() => pickDate(d.value)}
                data-testid={`day-chip-${d.value}`}
                className={`rounded-full px-3 py-1 text-xs font-semibold transition-colors duration-200 ${
                  !showAll && date === d.value
                    ? 'bg-[#1f6feb] text-white'
                    : 'bg-white border border-slate-200 text-slate-600 hover:bg-slate-100'
                }`}
              >
                {d.label}
              </button>
            ))}
          </>
        )}
      </div>

      <div className="overflow-x-auto max-h-[420px] overflow-y-auto">
        <table className="w-full text-sm">
          <thead className="sticky top-0 bg-slate-50 text-left text-xs font-semibold uppercase tracking-wider text-slate-500">
            <tr>
              <th className="px-5 py-3">Snapshot</th>
              <th className="px-5 py-3">Date &amp; Time</th>
              <th className="px-5 py-3">Type</th>
              <th className="px-5 py-3">Direction</th>
              <th className="px-5 py-3">Plate</th>
            </tr>
          </thead>
          <tbody data-testid="events-tbody">
            {rows.length === 0 && (
              <tr>
                <td colSpan={5} className="px-5 py-12 text-center text-slate-400">
                  <ImageOff size={28} className="mx-auto mb-2" strokeWidth={1.2} />
                  Koi event nahi — {connected ? 'gaadi line cross karegi to yahan dikhega' : 'camera connect karein'}
                </td>
              </tr>
            )}
            {rows.map((r) => (
              <tr
                key={r.id}
                className="even:bg-slate-50 hover:bg-blue-50/60 cursor-pointer transition-colors duration-200"
                onClick={() => setPreview(r)}
                data-testid={`event-row-${r.id}`}
              >
                <td className="px-5 py-2">
                  {r.image_path ? (
                    <img
                      src={snapshotUrl(r.image_path)}
                      alt="snap"
                      className="h-14 w-24 rounded-md object-cover bg-slate-200"
                      loading="lazy"
                    />
                  ) : (
                    <div className="h-14 w-24 rounded-md bg-slate-100 text-[10px] text-slate-400 flex items-center justify-center text-center px-1" data-testid="snapshot-missing">
                      photo save nahi hui
                    </div>
                  )}
                </td>
                <td className="px-5 py-2 text-slate-700">{r.date} {fmt12(r.time)}</td>
                <td className="px-5 py-2 font-semibold uppercase text-slate-800">{r.vehicle_type}</td>
                <td className="px-5 py-2"><Badge d={r.direction} /></td>
                <td className="px-5 py-2 font-mono text-slate-700">{r.plate || '—'}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {preview && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-6" onClick={() => setPreview(null)} data-testid="snapshot-modal">
          <div className="card max-w-4xl w-full overflow-hidden" onClick={(e) => e.stopPropagation()}>
            <div className="flex items-center justify-between px-5 py-3 border-b border-slate-200">
              <div className="text-sm font-semibold text-slate-800">
                {preview.direction} — {preview.vehicle_type.toUpperCase()} · {preview.date} {fmt12(preview.time)}
                {preview.plate ? ` · ${preview.plate}` : ''}
              </div>
              <button onClick={() => setPreview(null)} className="text-slate-400 hover:text-slate-700" data-testid="snapshot-modal-close">
                <X size={18} />
              </button>
            </div>
            <img src={snapshotUrl(preview.image_path)} alt="snapshot" className="w-full object-contain max-h-[70vh] bg-black" />
          </div>
        </div>
      )}
    </div>
  );
}
