import React, { useState } from 'react';
import { BellRing, Volume2, Play } from 'lucide-react';
import { api } from '../api';
import TimeField, { describeWindow } from './TimeField.jsx';

const CATS = [
  { id: 'person', key: 'person_chime_enabled', label: '👤 Akela paidal person', tone: 'soft ding-dong' },
  { id: 'vehicle', key: 'vehicle_chime_enabled', label: '🚗 Vehicle (car/truck/bus)', tone: 'deep bong-bong' },
  { id: 'two_wheeler', key: 'two_wheeler_chime_enabled', label: '🏍️ Two-wheeler', tone: 'quick ti-ti' },
];
const tid = (id) => (id === 'two_wheeler' ? 'two-wheeler' : id);

export default function PersonChimeCard({ s, set, showToast }) {
  const [testing, setTesting] = useState('');
  const enabled = (c) => s[c.key] !== false;
  const on = CATS.some(enabled);
  const sched = s.person_chime_schedule_enabled !== false;
  const start = s.person_chime_start || '18:00';
  const end = s.person_chime_end || '06:00';
  const vol = s.person_chime_volume ?? 70;

  const test = async (category) => {
    setTesting(category);
    try {
      const r = await api('/api/chime/test', { method: 'POST', body: JSON.stringify({ volume: vol, category }) });
      showToast(r.supported ? `${r.detail} 🔔` : r.detail, r.supported ? 'success' : 'info');
    } catch (e) {
      showToast(e.message, 'error');
    } finally {
      setTesting('');
    }
  };

  return (
    <div className="rounded-lg border border-slate-200 p-4 space-y-3" data-testid="person-chime-card">
      <div className="flex items-center gap-2.5 text-sm font-medium text-slate-800">
        <BellRing size={15} className="text-amber-500" /> Gate chime — alert par PC par halki ghanti (har category ki alag awaaz)
      </div>
      <p className="text-xs text-slate-400 -mt-1">
        Awaaz engine se bajti hai — app minimize/tray/Lock ho tab bhi. Gaadi/bike ke saath wala person "person" nahi ginta.
        {s.person_chime_supported === false && <span className="text-amber-600"> (Is computer par sound support nahi — Windows PC par bajegi.)</span>}
      </p>
      <div className="space-y-1.5" data-testid="chime-categories">
        {CATS.map((c) => (
          <div key={c.id} className="flex items-center gap-3 text-sm text-slate-700">
            <label className="flex items-center gap-2 cursor-pointer w-64">
              <input type="checkbox" className="h-4 w-4 accent-[#1f6feb]" checked={enabled(c)}
                onChange={(e) => set(c.key, e.target.checked)} data-testid={`${tid(c.id)}-chime-toggle`} />
              {c.label}
            </label>
            <span className="text-xs text-slate-400 w-28">{c.tone}</span>
            <button className="btn-ghost !py-0.5 !px-2 text-xs" onClick={() => test(c.id)} disabled={!!testing}
              title="Is category ki awaaz suno" data-testid={`${tid(c.id)}-chime-test-btn`}>
              <Play size={12} /> {testing === c.id ? 'Baj rahi…' : 'Test'}
            </button>
          </div>
        ))}
      </div>
      <div className={`space-y-2 border-t border-slate-100 pt-2 ${on ? '' : 'opacity-50'}`}>
        <label className="flex items-center gap-2 text-sm text-slate-700 cursor-pointer">
          <input type="checkbox" className="h-4 w-4 accent-[#1f6feb]" checked={sched} disabled={!on}
            onChange={(e) => set('person_chime_schedule_enabled', e.target.checked)} data-testid="person-chime-schedule-toggle" />
          Sirf is time me bajao (OFF = 24 ghante) — sab categories ke liye
        </label>
        <div className="flex items-center flex-wrap gap-3 text-sm text-slate-700">
          <span>Se</span>
          <TimeField value={start} onChange={(v) => set('person_chime_start', v)} disabled={!on || !sched} testid="person-chime-start" />
          <span>Tak</span>
          <TimeField value={end} onChange={(v) => set('person_chime_end', v)} disabled={!on || !sched} testid="person-chime-end" />
        </div>
        <p className="text-xs font-medium text-[#1f6feb]" data-testid="person-chime-desc">
          → {sched ? `ghanti ${describeWindow(start, end)} bajegi` : 'ghanti 24 ghante bajegi'}
        </p>
        <div className="flex items-center gap-3 text-sm text-slate-700">
          <Volume2 size={15} className="text-slate-400" />
          <span>Volume</span>
          <input type="range" min="0" max="100" step="5" value={vol} disabled={!on} className="flex-1 max-w-xs accent-[#1f6feb]"
            onChange={(e) => set('person_chime_volume', Number(e.target.value))} data-testid="person-chime-volume" />
          <span className="w-10 text-right font-mono text-xs" data-testid="person-chime-volume-value">{vol}%</span>
        </div>
        <p className="text-xs text-slate-400">Volume/time badalne ke baad neeche Save dabayein. Test abhi ke slider volume par bajta hai.</p>
      </div>
    </div>
  );
}
