import React, { useEffect, useState } from 'react';
import { Cctv, Plus, Trash2, Check, Save } from 'lucide-react';
import { api } from '../api';

const mask = (u) => (u || '').replace(/\/\/[^@/]*@/, '//***@');

function CameraCard({ cam, onChanged, showToast }) {
  const [f, setF] = useState({ name: cam.name, gate: cam.gate, rtsp_url: cam.rtsp_url });
  const [confirm, setConfirm] = useState(false);
  const [busy, setBusy] = useState(false);
  useEffect(() => { setF({ name: cam.name, gate: cam.gate, rtsp_url: cam.rtsp_url }); }, [cam.name, cam.gate, cam.rtsp_url]);
  const dirty = f.name !== cam.name || f.gate !== cam.gate || f.rtsp_url !== cam.rtsp_url;

  const run = async (fn, okMsg) => {
    setBusy(true);
    try { const r = await fn(); onChanged(r); if (okMsg) showToast(okMsg, 'success'); } catch (e) { showToast(e.message, 'error'); } finally { setBusy(false); }
  };
  const save = () => run(() => api('/api/cameras', { method: 'POST', body: JSON.stringify({ id: cam.id, ...f }) }), `${f.name || 'Camera'} save ho gaya ✔`);
  const activate = () => run(() => api(`/api/cameras/${cam.id}/activate`, { method: 'POST' }), `${cam.name} ab active camera hai — stream switch ho rahi hai`);
  const del = () => {
    if (!confirm) { setConfirm(true); setTimeout(() => setConfirm(false), 4000); return; }
    run(() => api(`/api/cameras/${cam.id}`, { method: 'DELETE' }), `${cam.name} delete ho gaya`);
  };

  return (
    <div className={`rounded-lg border p-4 space-y-3 ${cam.active ? 'border-[#1f6feb] bg-blue-50/40' : 'border-slate-200'}`} data-testid={`camera-card-${cam.id}`}>
      <div className="flex items-center justify-between gap-2">
        <div className="flex items-center gap-2 text-sm font-semibold text-slate-800">
          <Cctv size={16} className={cam.active ? 'text-[#1f6feb]' : 'text-slate-400'} />
          {cam.name}{cam.gate ? <span className="font-normal text-slate-500">· Gate: {cam.gate}</span> : null}
        </div>
        {cam.active ? (
          <span className="inline-flex items-center gap-1 rounded-full bg-emerald-100 px-2.5 py-0.5 text-xs font-semibold text-emerald-700" data-testid={`camera-active-chip-${cam.id}`}>
            <Check size={12} /> Active
          </span>
        ) : (
          <button className="btn-ghost !py-1 text-xs" onClick={activate} disabled={busy} data-testid={`camera-activate-${cam.id}`}>Is camera ko use karo</button>
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
  useEffect(() => { load(); }, []); // eslint-disable-line

  const addCam = async () => {
    setBusy(true);
    try {
      const r = await api('/api/cameras', { method: 'POST', body: JSON.stringify(add) });
      setData(r); setAdd({ name: '', gate: '', rtsp_url: '' });
      showToast('Camera add ho gaya ✔ — dashboard ke dropdown se switch karein', 'success');
    } catch (e) { showToast(e.message, 'error'); } finally { setBusy(false); }
  };

  const cams = data?.cameras || [];
  return (
    <div className="space-y-5" data-testid="cameras-tab">
      <div>
        <h3 className="text-base font-semibold text-slate-800">Cameras</h3>
        <p className="text-xs text-slate-500 mt-1">
          Ek waqt me <b>ek hi camera active</b> rehta hai (PC par halka). Yahan cameras save karein, dashboard ke dropdown se switch karein.
          Har camera ki apni detection line aur ignore zones yaad rehti hain. Gate Name har alert me "Gate: …" ki tarah jaata hai.
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
