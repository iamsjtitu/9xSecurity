import React, { useState } from 'react';
import { Cctv, PenLine, MessageCircle, Flag, X } from 'lucide-react';
import { api } from '../../api';
import StepCamera from './StepCamera.jsx';
import StepLine from './StepLine.jsx';
import StepWhatsApp from './StepWhatsApp.jsx';
import StepDone from './StepDone.jsx';

const STEPS = [
  { id: 'camera', label: 'Camera', icon: Cctv },
  { id: 'line', label: 'Line', icon: PenLine },
  { id: 'whatsapp', label: 'WhatsApp', icon: MessageCircle },
  { id: 'done', label: 'Live', icon: Flag },
];

// First-run Setup Wizard: scan → camera → line → WhatsApp → live (2 minutes).
export default function SetupWizard({ state, refreshState, showToast, onClose }) {
  const [i, setI] = useState(state.connected ? 1 : 0);
  const next = () => setI((x) => Math.min(x + 1, STEPS.length - 1));
  const back = () => setI((x) => Math.max(x - 1, 0));
  const skipAll = async () => {
    try { await api('/api/options', { method: 'POST', body: JSON.stringify({ setup_done: true }) }); } catch (_) { /* ignore */ }
    onClose();
  };
  const common = { state, refreshState, showToast };

  return (
    <div className="fixed inset-0 z-[60] bg-slate-900/70 backdrop-blur-sm flex items-center justify-center p-6" data-testid="setup-wizard">
      <div className="card w-full max-w-5xl max-h-[92vh] overflow-y-auto">
        <div className="flex items-center justify-between border-b border-slate-200 px-6 py-4">
          <div>
            <h2 className="text-lg font-semibold text-slate-900">Site Setup — 2 minute me live</h2>
            <p className="text-xs text-slate-500">Step {i + 1} / {STEPS.length}: {STEPS[i].label}</p>
          </div>
          <ol className="flex items-center gap-2" data-testid="wizard-steps">
            {STEPS.map((s, idx) => (
              <li key={s.id} className={`flex items-center gap-1.5 rounded-full px-3 py-1 text-xs font-semibold ${idx === i ? 'bg-[#1f6feb] text-white' : idx < i ? 'bg-emerald-50 text-emerald-700' : 'bg-slate-100 text-slate-500'}`} data-testid={`wizard-step-pill-${s.id}`}>
                <s.icon size={13} /> {s.label}
              </li>
            ))}
          </ol>
          <button className="text-slate-400 hover:text-slate-700" onClick={skipAll} title="Baad me karunga" data-testid="wizard-close-btn"><X size={18} /></button>
        </div>
        <div className="p-6">
          {i === 0 && <StepCamera {...common} onNext={next} />}
          {i === 1 && <StepLine {...common} onNext={next} onBack={back} />}
          {i === 2 && <StepWhatsApp showToast={showToast} onNext={next} onBack={back} />}
          {i === 3 && <StepDone {...common} onFinish={onClose} onBack={back} />}
        </div>
      </div>
    </div>
  );
}
