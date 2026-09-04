import React, { useState } from 'react';
import { Users, RefreshCw, Plus, X } from 'lucide-react';
import { api } from '../api';

const normId = (v) => {
  const digits = String(v || '').trim().toLowerCase().split('@')[0].replace(/\D/g, '');
  return digits.length >= 10 ? `${digits}@g.us` : '';
};

export default function WaGroupsPicker({ selected = [], onChange, apiKey, baseUrl, showToast }) {
  const [available, setAvailable] = useState(null);
  const [busy, setBusy] = useState(false);
  const [manual, setManual] = useState('');
  const isSel = (id) => selected.some((g) => g.id === id);

  const load = async () => {
    setBusy(true);
    try {
      const r = await api('/api/whatsapp/groups', { method: 'POST', body: JSON.stringify({ wa_api_key: apiKey, wa_base_url: baseUrl }), timeout: 60000 });
      if (!r.ok) { showToast(r.detail || 'Group list nahi mili', 'error'); setAvailable([]); return; }
      setAvailable(r.groups);
      if (!r.groups.length) showToast('Is WhatsApp number ka koi group nahi mila', 'info');
    } catch (e) { showToast(e.message, 'error'); } finally { setBusy(false); }
  };

  const toggle = (g) => onChange(isSel(g.id) ? selected.filter((x) => x.id !== g.id) : [...selected, { id: g.id, name: g.name || '' }]);
  const addManual = () => {
    const id = normId(manual);
    if (!id) { showToast('Group ID galat hai — 120363XXXXXXXXXXXX ya ...@g.us format', 'error'); return; }
    if (!isSel(id)) onChange([...selected, { id, name: '' }]);
    setManual('');
  };

  return (
    <div className="rounded-lg border border-slate-200 p-4 space-y-3" data-testid="wa-groups-picker">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2 text-sm font-semibold text-slate-800"><Users size={16} /> WhatsApp Group (optional)</div>
        <button type="button" className="btn-ghost" onClick={load} disabled={busy} data-testid="wa-groups-load-btn">
          <RefreshCw size={14} className={busy ? 'animate-spin' : ''} /> {available ? 'Dobara load' : 'Groups load karein'}
        </button>
      </div>
      <p className="text-xs text-slate-500">
        Group select karne par alerts (photo + text) group me bhi jayenge. Sirf group chahiye to upar numbers khaali chhod dein.
        WhatsApp number (jo wa.9x.design se connected hai) us group ka member hona chahiye.
      </p>

      {selected.length > 0 && (
        <div className="flex flex-wrap gap-2" data-testid="wa-groups-selected">
          {selected.map((g) => (
            <span key={g.id} className="inline-flex items-center gap-1 rounded-full bg-emerald-50 text-emerald-800 border border-emerald-200 px-2.5 py-1 text-xs" data-testid={`wa-group-chip-${g.id.split('@')[0]}`}>
              <Users size={11} /> {g.name || g.id.split('@')[0]}
              <button type="button" onClick={() => toggle(g)} className="ml-1 hover:text-rose-600" aria-label="remove" data-testid={`wa-group-remove-${g.id.split('@')[0]}`}><X size={12} /></button>
            </span>
          ))}
        </div>
      )}

      {available && available.length > 0 && (
        <div className="max-h-48 overflow-auto rounded-md border border-slate-100 divide-y divide-slate-100" data-testid="wa-groups-list">
          {available.map((g) => (
            <label key={g.id} className="flex items-center gap-3 px-3 py-2 text-sm hover:bg-slate-50 cursor-pointer">
              <input type="checkbox" checked={isSel(g.id)} onChange={() => toggle(g)} data-testid={`wa-group-option-${g.id.split('@')[0]}`} />
              <span className="flex-1 text-slate-800">{g.name || '(no name)'}</span>
              <span className="text-xs text-slate-400">{g.size} members</span>
            </label>
          ))}
        </div>
      )}

      <div className="flex gap-2">
        <input className="input font-mono text-xs flex-1" placeholder="Ya Group ID manually: 120363XXXXXXXXXXXX@g.us" value={manual}
          onChange={(e) => setManual(e.target.value)} data-testid="wa-group-manual-input" />
        <button type="button" className="btn-ghost" onClick={addManual} data-testid="wa-group-manual-add-btn"><Plus size={14} /> Add</button>
      </div>
    </div>
  );
}
