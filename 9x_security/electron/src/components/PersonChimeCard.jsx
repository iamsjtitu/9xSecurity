import React, { useState } from 'react';
import { BellRing, Volume2 } from 'lucide-react';
import { api } from '../api';
import TimeField, { describeWindow } from './TimeField.jsx';

export default function PersonChimeCard({ s, set, showToast }) {
  const [testing, setTesting] = useState(false);
  const on = s.person_chime_enabled !== false;
  const sched = s.person_chime_schedule_enabled !== false;
  const start = s.person_chime_start || '18:00';
  const end = s.person_chime_end || '06:00';
  const vol = s.person_chime_volume ?? 70;

  const test = async () => {
    setTesting(true);
    try {
      const r = await api('/api/chime/test', { method: 'POST', body: JSON.stringify({ volume: vol }) });
      showToast(r.supported ? `${r.detail} 🔔` : r.detail, r.supported ? 'success' : 'info');
    } catch (e) {
      showToast(e.message, 'error');
    } finally {
      setTesting(false);
    }
  };

  return (
    <div className="rounded-lg border border-slate-200 p-4 space-y-3" data-testid="person-chime-card">
      <label className="flex items-center gap-2.5 text-sm font-medium text-slate-800 cursor-pointer">
        <input type="checkbox" className="h-4 w-4 accent-[#1f6feb]" checked={on}
          onChange={(e) => set('person_chime_enabled', e.target.checked)} data-testid="person-chime-toggle" />
        <BellRing size={15} className="text-amber-500" /> Akela paidal person line cross kare to PC par halki ghanti (chime) bajao
      </label>
      <p className="text-xs text-slate-400 -mt-1">
        Sirf akele person par (gaadi/bike ke saath wale skip). Awaaz engine se bajti hai — app minimize/tray/Lock ho tab bhi.
        {s.person_chime_supported === false && <span className="text-amber-600"> (Is computer par sound support nahi — Windows PC par bajegi.)</span>}
      </p>
      <div className={`space-y-2 ${on ? '' : 'opacity-50'}`}>
        <label className="flex items-center gap-2 text-sm text-slate-700 cursor-pointer">
          <input type="checkbox" className="h-4 w-4 accent-[#1f6feb]" checked={sched} disabled={!on}
            onChange={(e) => set('person_chime_schedule_enabled', e.target.checked)} data-testid="person-chime-schedule-toggle" />
          Sirf is time me bajao (OFF = 24 ghante)
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
          <button className="btn-ghost !py-1 text-xs" onClick={test} disabled={testing} data-testid="person-chime-test-btn">
            <BellRing size={13} /> {testing ? 'Baj rahi…' : 'Test sound'}
          </button>
        </div>
        <p className="text-xs text-slate-400">Volume/time badalne ke baad neeche Save dabayein. Test sound abhi ke slider volume par bajti hai.</p>
      </div>
    </div>
  );
}
