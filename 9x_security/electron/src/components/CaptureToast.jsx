import React, { useEffect } from 'react';
import { LogIn, LogOut, X } from 'lucide-react';
import { snapshotUrl } from '../api';
import PlateBadge from './PlateBadge.jsx';

const fmtTime = (iso) => {
  try {
    return new Date(iso).toLocaleTimeString('en-IN', { hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: true }).toUpperCase();
  } catch (_) { return ''; }
};

export default function CaptureToast({ event, onClose }) {
  useEffect(() => {
    if (!event) return undefined;
    // restarts when the OCR result arrives so the number stays visible for a full 8s
    const id = setTimeout(onClose, 8000);
    return () => clearTimeout(id);
  }, [event?.id, event?.plate_status, event?.plate]); // eslint-disable-line

  if (!event) return null;
  const entry = event.direction === 'Entry';
  const Icon = entry ? LogIn : LogOut;
  return (
    <div
      className={`fixed top-5 right-6 z-50 w-[360px] flex items-center gap-3 rounded-xl border-l-4 bg-white p-3 shadow-2xl animate-[slideIn_.3s_ease-out] ${entry ? 'border-emerald-500' : 'border-orange-500'}`}
      data-testid="capture-toast"
      role="status"
    >
      {event.image_path ? (
        <img src={snapshotUrl(event.image_path)} alt="capture" className="h-14 w-24 rounded-md object-cover bg-slate-200 shrink-0" data-testid="capture-toast-img" />
      ) : (
        <div className={`h-14 w-14 rounded-md flex items-center justify-center shrink-0 ${entry ? 'bg-emerald-50 text-emerald-600' : 'bg-orange-50 text-orange-600'}`}>
          <Icon size={24} />
        </div>
      )}
      <div className="min-w-0 flex-1">
        <div className={`flex items-center gap-1.5 text-sm font-bold uppercase ${entry ? 'text-emerald-700' : 'text-orange-700'}`} data-testid="capture-toast-title">
          <Icon size={15} /> {event.direction} — {event.vehicle_type} captured
        </div>
        <div className="text-xs text-slate-500 font-mono mt-0.5" data-testid="capture-toast-meta">
          {fmtTime(event.timestamp)}
        </div>
        {(event.plate || event.plate_status) && (
          <div className="mt-1 flex items-center gap-1.5 text-xs">
            <span className="text-slate-500">Number:</span>
            <PlateBadge plate={event.plate} status={event.plate_status} source={event.plate_source} testid="capture-toast-plate" />
          </div>
        )}
      </div>
      <button onClick={onClose} className="text-slate-400 hover:text-slate-700 shrink-0" aria-label="close" data-testid="capture-toast-close">
        <X size={16} />
      </button>
    </div>
  );
}
