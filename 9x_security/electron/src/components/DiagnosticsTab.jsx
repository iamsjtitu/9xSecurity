import React, { useEffect, useState } from 'react';
import { RefreshCw, Copy, Check, X } from 'lucide-react';
import { api } from '../api';

const Flag = ({ ok, label }) => (
  <span className={`inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-xs font-medium ${ok ? 'bg-emerald-50 text-emerald-700' : 'bg-rose-50 text-rose-700'}`}>
    {ok ? <Check size={12} /> : <X size={12} />} {label}
  </span>
);

function Row({ k, v }) {
  return (
    <div className="flex justify-between gap-4 py-1 border-b border-slate-100 text-sm">
      <span className="text-slate-500 shrink-0">{k}</span>
      <span className="text-slate-800 font-mono text-right break-all">{String(v)}</span>
    </div>
  );
}

export default function DiagnosticsTab({ showToast }) {
  const [d, setD] = useState(null);
  const [busy, setBusy] = useState(false);

  const load = async () => {
    setBusy(true);
    try { setD(await api('/api/diagnostics')); } catch (e) { showToast(e.message, 'error'); } finally { setBusy(false); }
  };
  useEffect(() => { load(); }, []); // eslint-disable-line

  const copyAll = async () => {
    if (!d) return;
    const { camera_log, wa_log, engine_out_log, app_log, ...rest } = d;
    const text = `=== 9x Security Diagnostics ===\n${JSON.stringify(rest, null, 2)}\n\n=== camera_log.txt ===\n${camera_log}\n=== wa_log.txt ===\n${wa_log}\n=== engine_out.log ===\n${engine_out_log || ''}\n=== app_log.txt ===\n${app_log || ''}`;
    try { await navigator.clipboard.writeText(text); showToast('Diagnostics copy ho gaya — chat me paste karein', 'success'); }
    catch (_) { showToast('Copy nahi hua — text select karke copy karein', 'error'); }
  };

  if (!d) return <div className="text-slate-400 text-sm" data-testid="diag-loading">Loading…</div>;
  const e = d.engine || {}, wa = d.whatsapp || {}, last = d.last_event;

  return (
    <div className="space-y-4" data-testid="diagnostics-tab">
      <div className="flex items-center justify-between">
        <h3 className="text-lg font-semibold text-slate-900">Diagnostics</h3>
        <div className="flex gap-2">
          <button className="btn-ghost" onClick={load} disabled={busy} data-testid="diag-refresh-btn"><RefreshCw size={14} /> Refresh</button>
          <button className="btn-primary" onClick={copyAll} data-testid="diag-copy-btn"><Copy size={14} /> Copy sab kuch</button>
        </div>
      </div>
      <p className="text-xs text-slate-400">Koi problem ho to "Copy sab kuch" dabakar text chat me paste karein — usse turant pata chal jata hai kya galat hai.</p>
      {['hevc', 'hvc1', 'hev1', 'h265'].includes(e.codec) && (
        <div className="rounded-lg border border-amber-200 bg-amber-50 px-4 py-3 text-xs text-amber-900" data-testid="diag-hevc-hint">
          Camera <b>H.265 (HEVC)</b> stream bhej raha hai. Agar video har 1-2 sec me dhundli/tooti dikhe: camera ki
          settings me video encoding <b>H.264</b> karein, ya <b>sub-stream</b> URL use karein
          (Hikvision: <code>/Streaming/Channels/102</code>, Dahua: <code>subtype=1</code>, CP Plus: <code>subtype=1</code>).
        </div>
      )}

      <div className="flex flex-wrap gap-2" data-testid="diag-flags">
        <Flag ok={e.connected} label={e.connected ? 'Camera connected' : 'Camera offline'} />
        <Flag ok={e.ai_loaded && !e.ai_error} label={e.ai_error ? `AI ERROR: ${String(e.ai_error).slice(0, 80)}` : e.ai_loaded ? `AI model loaded${e.ai_ms != null ? ` · ${e.ai_ms} ms/frame` : ''}` : 'AI model NOT loaded'} />
        <Flag ok={!e.capture_paused} label={e.capture_paused ? 'Capture PAUSED (schedule)' : 'Capture active'} />
        <Flag ok={d.snapshot_write_ok} label={d.snapshot_write_ok ? 'Snapshot folder writable' : `Snapshot write FAIL: ${d.snapshot_write_detail}`} />
        <Flag ok={wa.enabled && wa.api_key_set && wa.recipients > 0} label={`WhatsApp ${wa.enabled ? 'ON' : 'OFF'} · key ${wa.api_key_set ? 'set' : 'missing'} · ${wa.recipients} number`} />
        <Flag ok={d.outbox_pending === 0} label={`WA pending: ${d.outbox_pending}`} />
        <Flag ok={!String(d.versions?.easyocr || '').startsWith('IMPORT FAIL')} label={String(d.versions?.easyocr || '').startsWith('IMPORT FAIL') ? 'Number plate reader (EasyOCR) is build me load NAHI hua — Settings > Updates se naya version install karein' : `Number plate reader OK (easyocr ${d.versions?.easyocr || '?'})`} />
      </div>

      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        <div className="rounded-lg border border-slate-200 p-4" data-testid="diag-summary">
          <Row k="Version" v={d.version} />
          <Row k="PC time" v={d.time_now} />
          <Row k="Data folder" v={d.data_dir} />
          <Row k="Disk free" v={d.disk_free_gb == null ? '?' : `${d.disk_free_gb} GB`} />
          <Row k="Events total" v={d.events_total} />
          <Row k="Aaj" v={`${d.events_today?.Entry ?? 0} Entry / ${d.events_today?.Exit ?? 0} Exit`} />
          <Row k="AI status" v={e.status} />
          <Row k="AI frames / errors" v={`${e.ai_frames ?? 0} / ${e.ai_errors ?? 0}`} />
          <Row k="Camera codec" v={e.codec ? `${e.codec.toUpperCase()}${['hevc', 'hvc1', 'hev1', 'h265'].includes(e.codec) ? ' (H.265)' : ''}` : '—'} />
          <Row k="Frames skipped (AI busy)" v={e.frames_dropped ?? 0} />
          <Row k="Libraries" v={Object.entries(d.versions || {}).map(([k, v]) => `${k} ${v}`).join(' · ')} />
          <Row k="Vehicles tracked now" v={`${e.tracks_now} (detections: ${e.detections_now})`} />
          <Row k="Detect classes" v={(e.vehicle_classes || []).join(', ')} />
          <Row k="Plate OCR" v={e.plate_ocr ? 'ON' : 'OFF'} />
        </div>
        <div className="rounded-lg border border-slate-200 p-4" data-testid="diag-last-event">
          <div className="text-xs font-semibold uppercase tracking-wider text-slate-500 mb-2">Last event</div>
          {last ? (
            <>
              <Row k="Kab" v={`${last.date} ${last.time}`} />
              <Row k="Kya" v={`${last.direction} · ${last.vehicle_type}${last.plate ? ` · ${last.plate}` : ''}`} />
              <Row k="Snapshot file" v={last.image_path || '(save fail)'} />
              <div className="mt-2"><Flag ok={last.image_exists} label={last.image_exists ? 'Snapshot file maujood hai' : 'Snapshot file NAHI mili'} /></div>
            </>
          ) : <div className="text-sm text-slate-400">Abhi tak koi event record nahi hua.</div>}
        </div>
      </div>

      {[['camera_log.txt (AI / camera)', d.camera_log, 'diag-camera-log'], ['wa_log.txt (WhatsApp)', d.wa_log, 'diag-wa-log'], ['engine_out.log + app_log.txt (crash / library output)', [d.engine_out_log, d.app_log].filter(Boolean).join('\n---\n'), 'diag-engine-log']].map(([title, body, tid]) => (
        <div key={tid}>
          <div className="text-xs font-semibold uppercase tracking-wider text-slate-500 mb-1">{title}</div>
          <pre className="rounded-lg bg-slate-900 text-slate-100 text-[11px] leading-relaxed p-3 max-h-56 overflow-auto whitespace-pre-wrap" data-testid={tid}>
            {body || '(khaali)'}
          </pre>
        </div>
      ))}
    </div>
  );
}
