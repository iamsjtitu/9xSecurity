import React from 'react';
import { PenLine, ArrowLeftRight, FolderOpen } from 'lucide-react';
import { api } from '../api';

export default function ControlsPanel({ state, refreshState, showToast, drawMode, setDrawMode }) {
  const swap = async () => {
    try {
      const r = await api('/api/swap', { method: 'POST' });
      showToast(`Entry/Exit direction swap ho gayi (${r.entry_direction})`, 'success');
      refreshState();
    } catch (e) { showToast(e.message, 'error'); }
  };

  const setOpts = async (patch) => {
    try {
      await api('/api/options', { method: 'POST', body: JSON.stringify(patch) });
      refreshState();
    } catch (e) { showToast(e.message, 'error'); }
  };

  const toggleClass = (cls) => {
    const cur = new Set(state.vehicle_classes || []);
    if (cur.has(cls)) cur.delete(cls); else cur.add(cls);
    setOpts({ vehicle_classes: [...cur] });
  };

  const openSnapshots = () => {
    if (window.native?.openPath && state.snapshot_dir) window.native.openPath(state.snapshot_dir);
    else showToast(`Snapshots folder: ${state.snapshot_dir}`, 'info');
  };

  return (
    <div className="card p-5" data-testid="controls-panel">
      <h3 className="text-sm font-semibold uppercase tracking-wider text-slate-500 mb-4">Detection Controls</h3>
      <div className="grid grid-cols-1 gap-2.5">
        <button
          className={drawMode ? 'btn-primary justify-center' : 'btn-ghost justify-center'}
          onClick={() => setDrawMode(!drawMode)}
          data-testid="draw-line-btn"
        >
          <PenLine size={15} /> {drawMode ? 'Video par 2 click karein…' : 'Draw Detection Line'}
        </button>
        <button className="btn-ghost justify-center" onClick={swap} data-testid="swap-direction-btn">
          <ArrowLeftRight size={15} /> Swap Entry/Exit
        </button>
        <button className="btn-ghost justify-center" onClick={openSnapshots} data-testid="open-snapshots-btn">
          <FolderOpen size={15} /> Open Snapshots
        </button>
      </div>
      <div className="mt-5 pt-4 border-t border-slate-200 space-y-3">
        <label className="flex items-center gap-2.5 text-sm text-slate-700 cursor-pointer">
          <input
            type="checkbox"
            className="h-4 w-4 accent-[#1f6feb]"
            checked={!!state.enable_plate}
            onChange={(e) => setOpts({ enable_plate: e.target.checked })}
            data-testid="plate-toggle"
          />
          Number Plate (OCR)
        </label>
        <div className="flex items-center gap-4 text-sm text-slate-700">
          <span className="text-slate-500">Detect:</span>
          {['car', 'truck', 'bus'].map((c) => (
            <label key={c} className="flex items-center gap-1.5 cursor-pointer capitalize">
              <input
                type="checkbox"
                className="h-4 w-4 accent-[#1f6feb]"
                checked={(state.vehicle_classes || []).includes(c)}
                onChange={() => toggleClass(c)}
                data-testid={`class-${c}`}
              />
              {c}
            </label>
          ))}
        </div>
      </div>
    </div>
  );
}
