import React from 'react';
import { Pencil } from 'lucide-react';

// Plate cell / toast line: number (+ manual tag), "reading…" while OCR runs, or "Not detected".
export default function PlateBadge({ plate, status, source, testid, size = 'sm' }) {
  const txt = size === 'lg' ? 'text-base' : 'text-sm';
  if (plate) {
    return (
      <span className="inline-flex items-center gap-1.5" data-testid={testid}>
        <span className={`font-mono font-semibold tracking-wide text-slate-800 ${txt}`}>{plate}</span>
        {source === 'manual' && (
          <span className="inline-flex items-center gap-0.5 rounded bg-amber-100 px-1.5 py-0.5 text-[10px] font-semibold text-amber-800" data-testid={testid ? `${testid}-manual` : undefined}>
            <Pencil size={9} /> manual
          </span>
        )}
      </span>
    );
  }
  if (status === 'pending') {
    return <span className={`italic text-slate-400 animate-pulse ${txt}`} data-testid={testid}>reading…</span>;
  }
  return <span className={`text-slate-400 ${txt}`} data-testid={testid}>Not detected</span>;
}
