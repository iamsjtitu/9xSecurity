import React from 'react';
import { Gauge } from 'lucide-react';

const DOT = { ok: 'bg-emerald-500', warn: 'bg-amber-500', bad: 'bg-rose-500', idle: 'bg-slate-300', warming: 'bg-sky-400', connecting: 'bg-sky-300' };
const PILL = {
  ok: 'bg-emerald-50 text-emerald-700 border-emerald-200',
  warn: 'bg-amber-50 text-amber-800 border-amber-200',
  bad: 'bg-rose-50 text-rose-700 border-rose-200',
};
const barColor = (p) => (p >= 85 ? 'bg-rose-500' : p >= 70 ? 'bg-amber-500' : 'bg-emerald-500');
const numColor = (level) => (level === 'bad' ? 'text-rose-600 font-semibold' : level === 'warn' ? 'text-amber-600 font-semibold' : 'text-slate-700');

function Bar({ label, pct, extra, testid }) {
  const v = pct == null ? null : Math.max(0, Math.min(100, pct));
  return (
    <div data-testid={testid}>
      <div className="flex items-center justify-between text-xs">
        <span className="font-semibold uppercase tracking-wider text-slate-500">{label}</span>
        <span className={v == null ? 'text-slate-400' : numColor(v >= 85 ? 'bad' : v >= 70 ? 'warn' : 'ok')} data-testid={`${testid}-value`}>
          {v == null ? 'sirf Windows build me' : `${v}%`}{extra ? <span className="text-slate-400 font-normal"> · {extra}</span> : null}
        </span>
      </div>
      <div className="mt-1 h-2 w-full rounded-full bg-slate-100 overflow-hidden">
        <div className={`h-2 rounded-full transition-[width] duration-500 ${v == null ? 'bg-slate-200' : barColor(v)}`} style={{ width: `${v == null ? 0 : v}%` }} />
      </div>
    </div>
  );
}

function camText(c) {
  if (c.monitor === false) return 'OFF';
  if (c.standby) return 'Standby (time window ke bahar)';
  if (!c.connected) return 'connect ho raha hai…';
  if (c.load === 'idle') return c.capture_paused ? 'live view, capture paused' : 'AI load ho raha hai…';
  if (c.load === 'connecting') return 'stream ka intezaar…';
  if (c.load === 'warming') return 'naap raha hai…';
  return null;
}

export default function LoadMeter({ state }) {
  const load = state.load || {};
  const cams = state.cameras || [];
  const level = load.level || 'ok';
  return (
    <div className="card p-5" data-testid="load-meter">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2 text-xs font-semibold uppercase tracking-wider text-slate-500"><Gauge size={14} /> PC Load</div>
        <span className={`rounded-full border px-2.5 py-0.5 text-xs ${PILL[level] || PILL.ok}`} data-testid="load-level">
          {level === 'bad' ? 'OVERLOAD' : level === 'warn' ? 'Zyada load' : 'Theek hai'}{load.running != null ? ` · ${load.running} camera AI` : ''}
        </span>
      </div>
      <div className="mt-3 space-y-2.5">
        <Bar label="CPU" pct={load.cpu_pct} extra={load.cores ? `${load.cores} cores` : ''} testid="load-cpu" />
        <Bar label="RAM" pct={load.ram_pct} extra={load.app_ram_mb ? `app ${load.app_ram_mb} MB` : ''} testid="load-ram" />
      </div>
      {cams.length > 0 && (
        <div className="mt-4 divide-y divide-slate-100 border-t border-slate-100" data-testid="load-cameras">
          {cams.map((c) => {
            const txt = camText(c);
            return (
              <div key={c.id} className="flex items-center gap-2 py-2 text-xs" data-testid={`load-cam-${c.id}`}>
                <span className={`h-2 w-2 shrink-0 rounded-full ${DOT[c.load] || 'bg-slate-300'}`} />
                <span className="truncate font-medium text-slate-800 flex-1" title={c.name}>{c.name}</span>
                {txt ? (
                  <span className="text-slate-400" data-testid={`load-cam-${c.id}-text`}>{txt}</span>
                ) : (
                  <>
                    <span className={numColor(c.load)} data-testid={`load-cam-${c.id}-fps`} title="AI kitne frame/sec dekh raha hai">{c.fps} fps</span>
                    <span className={`w-16 text-right ${numColor(c.ai_ms > 400 ? 'bad' : c.ai_ms > 250 ? 'warn' : 'ok')}`} data-testid={`load-cam-${c.id}-ai`} title="AI ka time per frame">{c.ai_ms ?? '—'} ms</span>
                    <span className="w-16 text-right text-slate-500" data-testid={`load-cam-${c.id}-drop`} title="camera ke kitne frame AI busy hone se skip hue">{c.drop_pct ?? 0}% skip</span>
                  </>
                )}
              </div>
            );
          })}
        </div>
      )}
      {load.hint ? (
        <div className={`mt-3 rounded-lg border px-3 py-2 text-xs ${PILL[level]}`} data-testid="load-hint">{load.hint}</div>
      ) : (
        <div className="mt-3 text-[11px] text-slate-400" data-testid="load-legend">FPS = AI har second kitne frame dekhta hai (≥ 8 accha · &lt; 4 tez gaadi miss ho sakti hai). Skip % normal hai — camera AI se tez hai.</div>
      )}
    </div>
  );
}
