import React, { useEffect, useRef, useState } from 'react';
import { Loader2, XCircle, CheckCircle2, AlertTriangle } from 'lucide-react';

const mb = (b) => (b / 1048576).toFixed(b > 104857600 ? 0 : 1);
const ACTIVE = ['checking', 'downloading', 'installing'];

export default function UpdateProgress({ job, onCancel }) {
  const [speed, setSpeed] = useState(0);
  const prev = useRef({ read: 0, t: Date.now() });

  useEffect(() => {
    if (job?.state !== 'downloading') return;
    const now = Date.now();
    const dt = (now - prev.current.t) / 1000;
    if (dt >= 0.8) {
      setSpeed(Math.max(0, (job.read - prev.current.read) / dt));
      prev.current = { read: job.read, t: now };
    }
  }, [job?.read, job?.state]); // eslint-disable-line

  if (!job || job.state === 'idle') return null;
  const active = ACTIVE.includes(job.state);
  const known = job.total > 0;
  const eta = speed > 0 && known ? Math.round((job.total - job.read) / speed) : null;
  const tone = job.state === 'error' ? 'border-rose-200 bg-rose-50 text-rose-900'
    : job.state === 'done' ? 'border-emerald-200 bg-emerald-50 text-emerald-900'
    : job.state === 'cancelled' ? 'border-slate-200 bg-slate-50 text-slate-700'
    : 'border-blue-200 bg-blue-50 text-blue-900';

  return (
    <div className={`rounded-lg border p-4 text-sm space-y-3 ${tone}`} data-testid="update-progress">
      <div className="flex items-center gap-2 font-semibold">
        {active && <Loader2 size={16} className="animate-spin" />}
        {job.state === 'done' && <CheckCircle2 size={16} />}
        {job.state === 'error' && <AlertTriangle size={16} />}
        <span data-testid="update-progress-message">{job.message}</span>
      </div>
      {(job.state === 'downloading' || job.state === 'installing') && (
        <>
          <div className="h-3 w-full rounded-full bg-white/70 overflow-hidden border border-blue-200">
            <div
              className={`h-full rounded-full bg-[#1f6feb] transition-[width] duration-500 ${!known && job.state === 'downloading' ? 'animate-pulse w-1/3' : ''}`}
              style={known || job.state === 'installing' ? { width: `${job.percent}%` } : undefined}
              data-testid="update-progress-bar"
            />
          </div>
          <div className="flex flex-wrap justify-between gap-2 text-xs font-mono" data-testid="update-progress-stats">
            <span>{known ? `${job.percent}%  ·  ${mb(job.read)} / ${mb(job.total)} MB` : `${mb(job.read)} MB download hua`}</span>
            {job.state === 'downloading' && (
              <span>{speed > 0 ? `${mb(speed)} MB/s` : '…'}{eta != null ? `  ·  ~${eta >= 60 ? `${Math.ceil(eta / 60)} min` : `${eta} sec`} baaki` : ''}</span>
            )}
          </div>
        </>
      )}
      {(job.state === 'checking' || job.state === 'downloading') && (
        <button className="btn-ghost !text-rose-700" onClick={onCancel} data-testid="update-cancel-btn">
          <XCircle size={14} /> Cancel
        </button>
      )}
    </div>
  );
}
