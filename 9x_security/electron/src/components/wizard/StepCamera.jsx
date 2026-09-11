import React, { useEffect, useState } from 'react';
import { Radar, CheckCircle2, XCircle, Loader2 } from 'lucide-react';
import { api } from '../../api';
import LivePreview from './LivePreview.jsx';

// Step 1: find the camera (scan or type IP), credentials, Test+Connect until live.
export default function StepCamera({ state, refreshState, showToast, onNext }) {
  const [brands, setBrands] = useState([]);
  const [scan, setScan] = useState(null); // null | 'busy' | {cameras,...}
  const [f, setF] = useState({ brand: 'auto', ip: '', port: 554, user: 'admin', password: '', channel: 1, stream: 'main' });
  const [rawUrl, setRawUrl] = useState('');
  const [advanced, setAdvanced] = useState(false);
  const [phase, setPhase] = useState(state.connected ? 'live' : 'idle'); // idle | testing | connecting | live | failed
  const [steps, setSteps] = useState([]);
  const set = (k, v) => setF((s) => ({ ...s, [k]: v }));

  useEffect(() => {
    api('/api/camera/brands').then((r) => setBrands(r.brands || [])).catch(() => {});
    runScan();
  }, []); // eslint-disable-line

  useEffect(() => { if (state.connected && phase === 'connecting') setPhase('live'); }, [state.connected, phase]);

  const runScan = async () => {
    setScan('busy');
    try { setScan(await api('/api/camera/scan', { method: 'POST', body: JSON.stringify({}), timeout: 90000 })); } catch (e) { setScan({ cameras: [], error: e.message }); }
  };
  const pick = (c) => { setF((s) => ({ ...s, ip: c.ip, port: c.port || 554, brand: c.brand || 'auto' })); setAdvanced(false); };

  const connect = async () => {
    setPhase('testing'); setSteps([]);
    try {
      const url = advanced && rawUrl.trim() ? rawUrl.trim() : (await api('/api/camera/build_url', { method: 'POST', body: JSON.stringify(f) })).url;
      const t = await api('/api/camera/test', { method: 'POST', body: JSON.stringify({ url }), timeout: 180000 });
      setSteps(t.steps || []);
      if (!t.ok) { setPhase('failed'); return; }
      setPhase('connecting');
      await api('/api/camera/connect', { method: 'POST', body: JSON.stringify({ url: t.url || url }) });
      for (let i = 0; i < 40; i++) { // wait up to ~40 s for the first frame
        await new Promise((r) => setTimeout(r, 1000));
        const s = await api('/api/state');
        if (s.connected) { refreshState(); setPhase('live'); showToast('Camera connect ho gaya ✔', 'success'); return; }
        if (String(s.status || '').startsWith('ERROR')) { setSteps([{ name: 'Connect', ok: false, detail: s.status }]); setPhase('failed'); return; }
      }
      setSteps([{ name: 'Connect', ok: false, detail: 'Camera se pehla frame nahi aaya (40 s) — sub-stream try karein ya Test dobara chalayein' }]);
      setPhase('failed');
    } catch (e) { setSteps([{ name: 'Error', ok: false, detail: e.message }]); setPhase('failed'); }
  };

  const busy = phase === 'testing' || phase === 'connecting';
  const field = 'input !bg-white text-sm mt-1';
  return (
    <div className="grid grid-cols-5 gap-6" data-testid="wizard-step-camera">
      <div className="col-span-3 space-y-4">
        <div className="rounded-lg border border-dashed border-[#1f6feb]/40 bg-[#1f6feb]/5 p-3" data-testid="wizard-scan-box">
          <div className="flex items-center justify-between">
            <span className="text-sm font-semibold text-slate-800">1. Network par camera dhundo</span>
            <button className="btn-ghost" onClick={runScan} disabled={scan === 'busy'} data-testid="wizard-scan-btn">
              <Radar size={14} className={scan === 'busy' ? 'animate-spin' : ''} /> {scan === 'busy' ? 'Scan…' : 'Dobara scan'}
            </button>
          </div>
          {scan === 'busy' && <p className="text-xs text-slate-500 mt-2" data-testid="wizard-scan-progress">Scan chal raha hai (5–10 sec)…</p>}
          {scan && scan !== 'busy' && (
            <ul className="mt-2 divide-y divide-slate-200 rounded-md border border-slate-200 bg-white max-h-36 overflow-y-auto" data-testid="wizard-scan-list">
              {scan.cameras.map((c) => (
                <li key={c.ip}>
                  <button type="button" onClick={() => pick(c)} data-testid={`wizard-scan-item-${c.ip.replace(/\./g, '-')}`}
                    className={`w-full flex items-center justify-between px-3 py-2 text-left hover:bg-slate-50 ${f.ip === c.ip ? 'bg-emerald-50' : ''}`}>
                    <span className="font-mono text-sm">{c.ip}</span>
                    <span className="text-xs text-slate-500 truncate max-w-[14rem]">{[c.name, c.hardware].filter(Boolean).join(' · ') || c.server || (c.onvif ? 'ONVIF' : 'RTSP')}</span>
                  </button>
                </li>
              ))}
              {!scan.cameras.length && <li className="px-3 py-2 text-sm text-slate-500" data-testid="wizard-scan-empty">Koi camera nahi mila — neeche IP khud likhein.</li>}
            </ul>
          )}
        </div>
        <div className="text-sm font-semibold text-slate-800">2. Camera ki details</div>
        {!advanced ? (
          <div className="grid grid-cols-2 gap-3">
            <label className="text-sm text-slate-700">Brand
              <select className={field} value={f.brand} onChange={(e) => set('brand', e.target.value)} data-testid="wizard-brand">
                {brands.map((b) => <option key={b.id} value={b.id}>{b.name}</option>)}
              </select>
            </label>
            <label className="text-sm text-slate-700">Camera IP
              <input className={field} placeholder="192.168.1.28" value={f.ip} onChange={(e) => set('ip', e.target.value)} data-testid="wizard-ip" />
            </label>
            <label className="text-sm text-slate-700">Username
              <input className={field} value={f.user} onChange={(e) => set('user', e.target.value)} data-testid="wizard-user" />
            </label>
            <label className="text-sm text-slate-700">Password
              <input className={field} value={f.password} onChange={(e) => set('password', e.target.value)} placeholder="camera ka password" data-testid="wizard-pass" />
            </label>
          </div>
        ) : (
          <input className={field} placeholder="rtsp://user:pass@192.168.1.10:554/stream1" value={rawUrl} onChange={(e) => setRawUrl(e.target.value)} data-testid="wizard-raw-url" />
        )}
        <button type="button" className="text-xs text-[#1f6feb] hover:underline" onClick={() => setAdvanced((a) => !a)} data-testid="wizard-advanced-toggle">
          {advanced ? '← Brand/IP se banao' : 'Poora RTSP URL khud likhna hai?'}
        </button>
        <div className="flex items-center gap-3 pt-1">
          <button className="btn-primary" onClick={connect} disabled={busy || (!advanced && !f.ip) || (advanced && !rawUrl)} data-testid="wizard-connect-btn">
            {busy ? <Loader2 size={15} className="animate-spin" /> : null} {phase === 'testing' ? 'Test ho raha hai…' : phase === 'connecting' ? 'Connect ho raha hai…' : phase === 'live' ? 'Dobara connect' : 'Test + Connect'}
          </button>
          {phase === 'live' && <span className="text-sm text-emerald-700 flex items-center gap-1" data-testid="wizard-camera-ok"><CheckCircle2 size={16} /> Camera live hai</span>}
        </div>
        {steps.length > 0 && phase === 'failed' && (
          <ul className="rounded-md border border-rose-200 bg-rose-50 p-3 space-y-1 text-xs" data-testid="wizard-test-steps">
            {steps.map((s, i) => (
              <li key={i} className="flex gap-2"><span className="shrink-0">{s.ok ? <CheckCircle2 size={14} className="text-emerald-600" /> : <XCircle size={14} className="text-rose-600" />}</span><span><b>{s.name}:</b> {s.detail}</span></li>
            ))}
          </ul>
        )}
      </div>
      <div className="col-span-2 space-y-3">
        <LivePreview connected={state.connected} />
        <button className="btn-primary w-full justify-center" disabled={!state.connected} onClick={onNext} data-testid="wizard-next-btn">Aage: Line draw karo →</button>
      </div>
    </div>
  );
}
