import React, { useState } from 'react';
import { ArrowLeftRight, RotateCcw } from 'lucide-react';
import { api } from '../../api';
import LivePreview from './LivePreview.jsx';

// Entry side of the line: tracker sign = (bx-ax)(py-ay) - (by-ay)(px-ax) > 0 (positive normal n = (-(by-ay), bx-ax)).
export function entryArrow(line, entryDirection) {
  if (!line) return null;
  const ax = line.x1 * 100, ay = line.y1 * 100, bx = line.x2 * 100, by = line.y2 * 100;
  const mx = (ax + bx) / 2, my = (ay + by) / 2;
  let nx = -(by - ay), ny = bx - ax;
  const len = Math.hypot(nx, ny) || 1;
  nx /= len; ny /= len;
  if (entryDirection !== 'pos') { nx = -nx; ny = -ny; }
  return { mx, my, ex: mx + nx * 14, ey: my + ny * 14, xx: mx - nx * 14, xy: my - ny * 14 };
}

// Step 2: draw the yellow line on the live picture, confirm the Entry side.
export default function StepLine({ state, refreshState, showToast, onNext, onBack }) {
  const [first, setFirst] = useState(null);
  const [busy, setBusy] = useState(false);
  const line = state.line;
  const arrow = entryArrow(line, state.entry_direction);

  const onClick = async (p) => {
    if (busy) return;
    if (!first) { setFirst(p); return; }
    setBusy(true);
    try {
      await api('/api/line', { method: 'POST', body: JSON.stringify({ x1: first.x, y1: first.y, x2: p.x, y2: p.y }) });
      await refreshState();
      showToast('Line set ho gayi ✔ — ab Entry ka direction check karein', 'success');
    } catch (e) { showToast(e.message, 'error'); }
    setFirst(null); setBusy(false);
  };
  const swap = async () => {
    setBusy(true);
    try { await api('/api/swap', { method: 'POST' }); await refreshState(); } catch (e) { showToast(e.message, 'error'); }
    setBusy(false);
  };

  return (
    <div className="grid grid-cols-5 gap-6" data-testid="wizard-step-line">
      <div className="col-span-3">
        <LivePreview connected={state.connected} onClick={onClick} testid="wizard-line-canvas">
          {line && <line x1={line.x1 * 100} y1={line.y1 * 100} x2={line.x2 * 100} y2={line.y2 * 100} stroke="#facc15" strokeWidth="0.8" vectorEffect="non-scaling-stroke" />}
          {arrow && (
            <g>
              <line x1={arrow.mx} y1={arrow.my} x2={arrow.ex} y2={arrow.ey} stroke="#22c55e" strokeWidth="0.7" vectorEffect="non-scaling-stroke" />
              <circle cx={arrow.ex} cy={arrow.ey} r="1.6" fill="#22c55e" />
              <text x={arrow.ex} y={arrow.ey - 2.5} fill="#22c55e" fontSize="4" fontWeight="700" textAnchor="middle">ENTRY</text>
              <text x={arrow.xx} y={arrow.xy + 4.5} fill="#f87171" fontSize="4" fontWeight="700" textAnchor="middle">EXIT</text>
            </g>
          )}
          {first && <circle cx={first.x * 100} cy={first.y * 100} r="1.4" fill="#facc15" />}
        </LivePreview>
        <p className="text-xs text-slate-500 mt-2" data-testid="wizard-line-hint">
          {first ? 'Ab line ka END point click karein' : line ? 'Line badalni hai? Video par do naye point click karein.' : 'Video par gate ke aar-paar 2 point click karein — jahan se har gaadi guzarti hai.'}
        </p>
      </div>
      <div className="col-span-2 space-y-4">
        <div className="rounded-lg border border-slate-200 bg-slate-50 p-4 text-sm text-slate-700 space-y-2">
          <div className="font-semibold text-slate-900">Yellow line = counting line</div>
          <p>Gaadi jab is line ko cross karti hai tab photo + alert jata hai. Line gate ki poori chaudai par ho, kinare ko na chhoo.</p>
          <p><span className="text-emerald-700 font-semibold">ENTRY</span> arrow us taraf hona chahiye jahan gaadi andar aakar jaati hai. Ulta ho to swap karein.</p>
        </div>
        {(state.line_hints || []).map((h, i) => <p key={i} className="text-xs text-amber-700 bg-amber-50 border border-amber-200 rounded-md px-3 py-2" data-testid="wizard-line-warning">{h}</p>)}
        <div className="flex flex-col gap-2">
          <button className="btn-ghost justify-center" onClick={swap} disabled={busy || !line} data-testid="wizard-swap-btn"><ArrowLeftRight size={15} /> Entry/Exit ulta karo</button>
          <button className="btn-ghost justify-center" onClick={() => setFirst(null)} disabled={!first} data-testid="wizard-line-reset-btn"><RotateCcw size={15} /> Point cancel</button>
        </div>
        <div className="flex gap-2 pt-2">
          <button className="btn-ghost" onClick={onBack} data-testid="wizard-back-btn">← Camera</button>
          <button className="btn-primary flex-1 justify-center" disabled={!line} onClick={onNext} data-testid="wizard-next-btn">Aage: WhatsApp →</button>
        </div>
      </div>
    </div>
  );
}
