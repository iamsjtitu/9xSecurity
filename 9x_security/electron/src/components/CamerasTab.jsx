import React, { useEffect, useState } from 'react';
import { Cctv, Plus, Trash2, Check, Save, Clock, Eye } from 'lucide-react';
import { api } from '../api';
import TimeField, { describeWindow } from './TimeField.jsx';

const mask = (u) => (u || '').replace(/\/\/[^@/]*@/, '//***@');
const DEF_SCH = { enabled: false, start: '20:00', end: '08:00' };

export function camStatus(cam) {
  if (cam.monitor === false) return { dot: 'bg-slate-300', text: 'Monitoring OFF' };
  if (cam.standby) return { dot: 'bg-amber-400', text: 'Standby (time window ke bahar)' };
  if (cam.connected && cam.capture_paused) return { dot: 'bg-amber-400', text: 'Live view — capture paused' };
  if (cam.connected) return { dot: 'bg-emerald-500', text: 'Chalu — monitoring' };
  return { dot: 'bg-rose-400', text: cam.rtsp_url ? 'Connect nahi hua' : 'URL nahi' };
}

function CameraCard({ cam, onChanged, showToast }) {
  const [f, setF] = useState({ name: cam.name, gate: cam.gate, rtsp_url: cam.rtsp_url, schedule: cam.schedule || DEF_SCH, monitor: cam.monitor !== false });
  const [confirm, setConfirm] = useState(false);
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    setF({ name: cam.name, gate: cam.gate, rtsp_url: cam.rtsp_url, schedule: cam.schedule || DEF_SCH, monitor: cam.monitor !== false });
  }, [cam.name, cam.gate, cam.rtsp_url, JSON.stringify(cam.schedule), cam.monitor]); // eslint-disable-line
  const dirty = f.name !== cam.name || f.gate !== cam.gate || f.rtsp_url !== cam.rtsp_url
    || JSON.stringify(f.schedule) !== JSON.stringify(cam.schedule || DEF_SCH) || f.monitor !== (cam.monitor !== false);
  const st = camStatus(cam);

  const run = async (fn, okMsg) => {
    setBusy(true);
    try { const r = await fn(); onChanged(r); if (okMsg) showToast(okMsg, 'success'); } catch (e) { showToast(e.message, 'error'); } finally { setBusy(false); }
  };
  const save = () => run(() => api('/api/cameras', { method: 'POST', body: JSON.stringify({ id: cam.id, ...f }) }), `${f.name || 'Camera'} save ho gaya ✔`);
  const view = () => run(() => api(`/api/cameras/${cam.id}/activate`, { method: 'POST' }), `Dashboard par ab ${cam.name} dikh raha hai`);
  const del = () => {
    if (!confirm) { setConfirm(true); setTimeout(() => setConfirm(false), 4000); return; }
    run(() => api(`/api/cameras/${cam.id}`, { method: 'DELETE' }), `${cam.name} delete ho gaya`);
  };
  const setSch = (patch) => setF({ ...f, schedule: { ...f.schedule, ...patch } });

  return (
    <div className={`rounded-lg border p-4 space-y-3 ${cam.active ? 'border-[#1f6feb] bg-blue-50/40' : 'border-slate-200'}`} data-testid={`camera-card-${cam.id}`}>
      <div className="flex items-center justify-between gap-2">
        <div className="flex items-center gap-2 text-sm font-semibold text-slate-800">
          <Cctv size={16} className={cam.active ? 'text-[#1f6feb]' : 'text-slate-400'} />
          {cam.name}{cam.gate ? <span className="font-normal text-slate-500">· Gate: {cam.gate}</span> : null}
          <span className="ml-2 inline-flex items-center gap-1.5 text-xs font-normal text-slate-500" data-testid={`camera-status-${cam.id}`}>
            <span className={`h-2 w-2 rounded-full ${st.dot}`} /> {st.text}
          </span>
        </div>
        {cam.active ? (
          <span className="inline-flex items-center gap-1 rounded-full bg-emerald-100 px-2.5 py-0.5 text-xs font-semibold text-emerald-700" data-testid={`camera-active-chip-${cam.id}`}>
            <Eye size={12} /> Dashboard par
          </span>
        ) : (
          <button className="btn-ghost !py-1 text-xs" onClick={view} disabled={busy} data-testid={`camera-activate-${cam.id}`}><Eye size={12} /> Dashboard par dikhao</button>
        )}
      </div>
      <div className="grid grid-cols-1 sm:grid-cols-2 gap-3 text-sm">
        <label className="space-y-1"><span className="text-xs text-slate-500">Camera naam</span>
          <input className="input" value={f.name} onChange={(e) => setF({ ...f, name: e.target.value })} data-testid={`camera-name-${cam.id}`} /></label>
        <label className="space-y-1"><span className="text-xs text-slate-500">Gate Name (WhatsApp me "Gate: …" aayega)</span>
          <input className="input" placeholder="jaise: Main Gate" value={f.gate} onChange={(e) => setF({ ...f, gate: e.target.value })} data-testid={`camera-gate-${cam.id}`} /></label>
      </div>
      <label className="block space-y-1 text-sm"><span className="text-xs text-slate-500">RTSP URL</span>
        <input className="input font-mono text-xs" placeholder="rtsp://user:pass@192.168.1.10:554/stream1" value={f.rtsp_url}
          onChange={(e) => setF({ ...f, rtsp_url: e.target.value })} data-testid={`camera-url-${cam.id}`} /></label>
      <div className="rounded-md bg-slate-50 border border-slate-100 p-3 space-y-2 text-sm">
        <label className="flex items-center gap-2 cursor-pointer text-slate-800 font-medium">
          <input type="checkbox" className="h-4 w-4 accent-[#1f6feb]" checked={f.monitor} onChange={(e) => setF({ ...f, monitor: e.target.checked })} data-testid={`camera-monitor-${cam.id}`} />
          Is camera ki monitoring chalu (stream + AI)
        </label>
        <label className="flex items-center gap-2 cursor-pointer text-slate-700">
          <input type="checkbox" className="h-4 w-4 accent-[#1f6feb]" checked={!!f.schedule.enabled} disabled={!f.monitor}
            onChange={(e) => setSch({ enabled: e.target.checked })} data-testid={`camera-sch-${cam.id}-toggle`} />
          <Clock size={14} className="text-slate-400" /> Sirf is time me snap/WhatsApp (baaki time standby — PC par load nahi)
        </label>
        <div className={`flex items-center flex-wrap gap-3 text-slate-700 ${f.schedule.enabled && f.monitor ? '' : 'opacity-50'}`}>
          <span>Se</span>
          <TimeField value={f.schedule.start || '20:00'} disabled={!f.schedule.enabled || !f.monitor} onChange={(v) => setSch({ start: v })} testid={`camera-sch-${cam.id}-start`} />
          <span>Tak</span>
          <TimeField value={f.schedule.end || '08:00'} disabled={!f.schedule.enabled || !f.monitor} onChange={(v) => setSch({ end: v })} testid={`camera-sch-${cam.id}-end`} />
        </div>
        <p className="text-xs font-medium text-[#1f6feb]" data-testid={`camera-sch-${cam.id}-desc`}>
          → {!f.monitor ? 'monitoring band' : f.schedule.enabled ? `${f.name || 'Camera'} sirf ${describeWindow(f.schedule.start || '20:00', f.schedule.end || '08:00')} snap lega` : `${f.name || 'Camera'} 24 ghante chalega`}
        </p>
      </div>
      <div className="flex items-center justify-between">
        <button className="btn-ghost !py-1 text-xs !text-rose-600 hover:!bg-rose-50" onClick={del} disabled={busy} data-testid={`camera-delete-${cam.id}`}>
          <Trash2 size={13} /> {confirm ? 'Pakka delete? (dobara dabayein)' : 'Delete'}
        </button>
        <button className="btn-primary !py-1.5 text-xs" onClick={save} disabled={!dirty || busy} data-testid={`camera-save-${cam.id}`}><Save size={13} /> Save</button>
      </div>
    </div>
  );
}

export default function CamerasTab({ showToast }) {
  const [data, setData] = useState(null);
  const [add, setAdd] = useState({ name: '', gate: '', rtsp_url: '' });
  const [busy, setBusy] = useState(false);
  const load = () => api('/api/cameras').then(setData).catch((e) => showToast(e.message, 'error'));
  useEffect(() => { load(); const id = setInterval(load, 5000); return () => clearInterval(id); }, []); // eslint-disable-line

  const addCam = async () => {
    setBusy(true);
    try {
      const r = await api('/api/cameras', { method: 'POST', body: JSON.stringify(add) });
      setData(r); setAdd({ name: '', gate: '', rtsp_url: '' });
      showToast('Camera add ho gaya ✔ — ab ye bhi saath me chalega', 'success');
    } catch (e) { showToast(e.message, 'error'); } finally { setBusy(false); }
  };

  const cams = data?.cameras || [];
  const running = cams.filter((c) => c.connected && !c.standby).length;
  return (
    <div className="space-y-5" data-testid="cameras-tab">
      <div>
        <h3 className="text-base font-semibold text-slate-800">Cameras <span className="text-sm font-normal text-slate-500" data-testid="cameras-running">· {running}/{cams.length} chalu</span></h3>
        <p className="text-xs text-slate-500 mt-1">
          <b>Sab cameras ek saath chalte hain</b>, har camera apne time window me (jaise Camera 2: raat 8 → subah 8 — baaki time standby, PC par load nahi).
          Dashboard par ek waqt me ek camera ka video dikhta hai (dropdown se chunein); line/ignore zones har camera ki alag. Gate Name har alert me "Gate: …" ki tarah jaata hai.
          Dhyaan: har chalu camera = 1 stream + 1 AI; slow PC par 2 se zyada cameras bhari pad sakte hain.
        </p>
      </div>
      {data && cams.length === 0 && <div className="text-sm text-slate-400" data-testid="cameras-empty">Abhi koi camera saved nahi — neeche add karein ya dashboard par URL daal kar Connect karein.</div>}
      <div className="space-y-3" data-testid="cameras-list">
        {cams.map((c) => <CameraCard key={c.id} cam={c} onChanged={setData} showToast={showToast} />)}
      </div>
      <div className="rounded-lg border border-dashed border-slate-300 p-4 space-y-3" data-testid="camera-add-card">
        <div className="text-sm font-medium text-slate-800 flex items-center gap-2"><Plus size={15} /> Naya camera add karein</div>
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-3 text-sm">
          <input className="input" placeholder="Camera naam (jaise: Back Camera)" value={add.name} onChange={(e) => setAdd({ ...add, name: e.target.value })} data-testid="camera-add-name" />
          <input className="input" placeholder="Gate Name (jaise: Back Gate)" value={add.gate} onChange={(e) => setAdd({ ...add, gate: e.target.value })} data-testid="camera-add-gate" />
        </div>
        <input className="input font-mono text-xs" placeholder="rtsp://user:pass@192.168.1.11:554/stream1 (baad me bhi daal sakte hain)" value={add.rtsp_url}
          onChange={(e) => setAdd({ ...add, rtsp_url: e.target.value })} data-testid="camera-add-url" />
        <div className="flex items-center justify-between text-xs text-slate-400">
          <span>{cams.length}/8 cameras · URL: {mask(add.rtsp_url) || '—'}</span>
          <button className="btn-primary !py-1.5 text-xs" onClick={addCam} disabled={busy || cams.length >= 8} data-testid="camera-add-btn"><Plus size={13} /> Camera add karein</button>
        </div>
      </div>
    </div>
  );
}
