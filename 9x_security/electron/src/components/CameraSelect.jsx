import React, { useState } from 'react';
import { Cctv, Settings2 } from 'lucide-react';
import { api } from '../api';

export default function CameraSelect({ state, refreshState, showToast, onManage }) {
  const [busy, setBusy] = useState(false);
  const cams = state.cameras || [];
  if (cams.length === 0) return null;

  const switchTo = async (id) => {
    if (!id || id === state.active_camera_id) return;
    setBusy(true);
    try {
      const r = await api(`/api/cameras/${id}/activate`, { method: 'POST' });
      const cam = (r.cameras || []).find((c) => c.id === id);
      showToast(`${cam?.name || 'Camera'} active — ${r.rtsp_url ? 'stream switch ho rahi hai' : 'is camera ka URL khaali hai'}`, r.rtsp_url ? 'success' : 'error');
      await refreshState();
    } catch (e) {
      showToast(e.message, 'error');
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="flex items-center gap-2 px-3 pt-3 text-sm" data-testid="camera-select-row">
      <Cctv size={15} className="text-slate-400 shrink-0" />
      <select
        className="rounded-md bg-slate-800 border border-slate-700 px-2.5 py-1.5 text-sm text-slate-100 outline-none focus:ring-2 focus:ring-[#1f6feb] max-w-[320px]"
        value={state.active_camera_id || ''}
        onChange={(e) => switchTo(e.target.value)}
        disabled={busy}
        data-testid="camera-select"
      >
        {cams.map((c) => (
          <option key={c.id} value={c.id}>{c.name}{c.gate ? ` — ${c.gate}` : ''}{c.rtsp_url ? '' : ' (URL nahi)'}</option>
        ))}
      </select>
      {state.gate && (
        <span className="rounded-full bg-slate-800 border border-slate-700 px-2.5 py-0.5 text-xs text-amber-300" data-testid="camera-gate-badge">Gate: {state.gate}</span>
      )}
      <span className="text-xs text-slate-500">{cams.length} camera{cams.length > 1 ? 's' : ''} saved · ek active</span>
      <button className="ml-auto btn-ghost !bg-slate-800 !border-slate-700 !text-slate-200 hover:!bg-slate-700 !py-1 text-xs" onClick={onManage} data-testid="manage-cameras-btn">
        <Settings2 size={13} /> Cameras manage
      </button>
    </div>
  );
}
