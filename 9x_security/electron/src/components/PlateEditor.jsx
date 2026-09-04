import React, { useEffect, useState } from 'react';
import { Check, Pencil } from 'lucide-react';
import { api } from '../api';
import PlateBadge from './PlateBadge.jsx';

// Manual plate correction inside the snapshot modal: type the right number -> Save.
export default function PlateEditor({ event, onSaved, showToast }) {
  const [val, setVal] = useState(event.plate || '');
  const [busy, setBusy] = useState(false);

  useEffect(() => { setVal(event.plate || ''); }, [event.id, event.plate]);

  const clean = val.toUpperCase().replace(/[^A-Z0-9]/g, '');
  const changed = clean !== (event.plate || '');

  const save = async () => {
    setBusy(true);
    try {
      const r = await api(`/api/events/${event.id}/plate`, { method: 'POST', body: JSON.stringify({ plate: clean }) });
      onSaved(r.event);
      showToast(clean ? `Number ${clean} save ho gaya ✔ (search me milega)` : 'Number hata diya', 'success');
    } catch (e) {
      showToast(e.message, 'error');
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="flex flex-wrap items-center gap-3 px-5 py-3 border-t border-slate-200 bg-slate-50" data-testid="plate-editor">
      <div className="text-sm font-semibold text-slate-700 flex items-center gap-1.5"><Pencil size={14} /> Number Plate</div>
      <PlateBadge plate={event.plate} status={event.plate_status} source={event.plate_source} testid="plate-editor-current" />
      <input
        className="input !w-48 font-mono uppercase tracking-wider"
        value={val}
        maxLength={12}
        placeholder="MH12AB1234"
        onChange={(e) => setVal(e.target.value.toUpperCase())}
        onKeyDown={(e) => { if (e.key === 'Enter' && changed && !busy) save(); }}
        data-testid="plate-editor-input"
      />
      <button
        type="button"
        className="btn-primary text-sm inline-flex items-center gap-1.5"
        onClick={save}
        disabled={busy || !changed || (clean.length > 0 && clean.length < 4)}
        data-testid="plate-editor-save-btn"
      >
        <Check size={14} /> {busy ? 'Saving…' : 'Save'}
      </button>
      <span className="text-xs text-slate-500">
        {!event.plate ? 'AI number nahi padh paya — sahi number yahan likh kar Save karein.' : 'Galat hai? Sahi number likh kar Save karein.'}
      </span>
    </div>
  );
}
