import React, { useEffect, useRef, useState } from 'react';
import { Cctv, Plug, Unplug, Stethoscope, X } from 'lucide-react';
import { api, streamUrl } from '../api';

export default function CameraPanel({ state, refreshState, showToast, drawMode, setDrawMode }) {
  const [url, setUrl] = useState(state.rtsp_url || '');
  const [busy, setBusy] = useState(false);
  const [testing, setTesting] = useState(false);
  const [testResult, setTestResult] = useState(null);
  const [firstPoint, setFirstPoint] = useState(null);
  const boxRef = useRef(null);
  const urlTouched = useRef(false);

  useEffect(() => {
    if (!urlTouched.current && state.rtsp_url && !url) setUrl(state.rtsp_url);
  }, [state.rtsp_url]); // eslint-disable-line

  const connect = async () => {
    setBusy(true);
    try {
      if (state.connected) {
        await api('/api/camera/disconnect', { method: 'POST' });
        showToast('Camera disconnect ho gaya', 'info');
      } else {
        await api('/api/camera/connect', { method: 'POST', body: JSON.stringify({ url }) });
        showToast('Connect ho raha hai…', 'info');
      }
      await refreshState();
    } catch (e) {
      showToast(e.message, 'error');
    } finally {
      setBusy(false);
    }
  };

  const runTest = async () => {
    if (!url.trim()) { showToast('Pehle RTSP URL daalein', 'error'); return; }
    setTesting(true);
    try {
      const r = await api('/api/camera/test', { method: 'POST', body: JSON.stringify({ url }) });
      setTestResult(r);
    } catch (e) {
      showToast(e.message, 'error');
    } finally {
      setTesting(false);
    }
  };

  const onOverlayClick = async (e) => {
    if (!drawMode || !boxRef.current) return;
    const rect = boxRef.current.getBoundingClientRect();
    const nx = Math.min(Math.max((e.clientX - rect.left) / rect.width, 0), 1);
    const ny = Math.min(Math.max((e.clientY - rect.top) / rect.height, 0), 1);
    if (!firstPoint) {
      setFirstPoint({ x: nx, y: ny });
    } else {
      try {
        await api('/api/line', {
          method: 'POST',
          body: JSON.stringify({ x1: firstPoint.x, y1: firstPoint.y, x2: nx, y2: ny }),
        });
        showToast('Detection line set ho gayi ✔', 'success');
      } catch (ex) {
        showToast(ex.message, 'error');
      }
      setFirstPoint(null);
      setDrawMode(false);
    }
  };

  return (
    <div className="bg-[#020617] rounded-xl overflow-hidden shadow-sm flex flex-col" data-testid="camera-panel">
      <div className="flex items-center gap-2 p-3 bg-slate-900/60">
        <input
          className="flex-1 rounded-md bg-slate-800 border border-slate-700 px-3 py-2.5 text-sm text-slate-100 placeholder-slate-500 outline-none focus:ring-2 focus:ring-[#1f6feb]"
          placeholder="rtsp://user:pass@192.168.1.10:554/stream1  (password me @ ho to bhi chalega)"
          value={url}
          onChange={(e) => { urlTouched.current = true; setUrl(e.target.value); }}
          data-testid="rtsp-url-input"
        />
        <button className="btn-ghost !bg-slate-800 !border-slate-700 !text-slate-200 hover:!bg-slate-700" onClick={runTest} disabled={testing} data-testid="camera-test-btn">
          <Stethoscope size={15} /> {testing ? 'Testing…' : 'Test'}
        </button>
        <button className={state.connected ? 'btn-danger' : 'btn-primary'} onClick={connect} disabled={busy} data-testid="camera-connect-btn">
          {state.connected ? <Unplug size={15} /> : <Plug size={15} />}
          {state.connected ? 'Disconnect' : 'Connect'}
        </button>
      </div>

      <div
        ref={boxRef}
        onClick={onOverlayClick}
        className={`relative w-full aspect-video bg-[#020617] ${drawMode ? 'cursor-crosshair' : ''}`}
        data-testid="camera-view"
      >
        {state.connected ? (
          <>
            <img src={streamUrl()} alt="live" className="absolute inset-0 w-full h-full object-contain" draggable={false} />
            <div className="absolute top-3 right-3 flex items-center gap-1.5 rounded-full bg-black/60 px-3 py-1 text-xs font-semibold text-white">
              <span className="h-2 w-2 rounded-full bg-[#ef4444] animate-pulse" /> LIVE
            </div>
          </>
        ) : (
          <div className="absolute inset-0 flex flex-col items-center justify-center text-slate-600 gap-3">
            <Cctv size={44} strokeWidth={1.2} />
            <div className="text-sm">Camera offline — URL daal kar <span className="text-slate-300 font-semibold">Connect</span> dabayein</div>
          </div>
        )}
        {drawMode && (
          <div className="absolute top-3 left-3 rounded-md bg-amber-500/90 px-3 py-1.5 text-xs font-semibold text-black">
            {firstPoint ? 'Ab END point par click karein' : 'Line ka START point click karein'}
          </div>
        )}
        {firstPoint && (
          <div
            className="absolute h-3 w-3 rounded-full bg-amber-400 ring-2 ring-black -translate-x-1/2 -translate-y-1/2"
            style={{ left: `${firstPoint.x * 100}%`, top: `${firstPoint.y * 100}%` }}
          />
        )}
      </div>

      {testResult && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-6" data-testid="test-result-modal">
          <div className="card w-full max-w-xl max-h-[80vh] overflow-y-auto">
            <div className="flex items-center justify-between border-b border-slate-200 px-5 py-4">
              <h3 className={`text-lg font-semibold ${testResult.ok ? 'text-emerald-700' : 'text-rose-700'}`}>
                {testResult.ok ? 'SAB THEEK — camera chal raha hai ✔' : 'PROBLEM MILI ✘'}
              </h3>
              <button onClick={() => setTestResult(null)} className="text-slate-400 hover:text-slate-700" data-testid="test-result-close">
                <X size={18} />
              </button>
            </div>
            <div className="p-5 space-y-4">
              {testResult.steps.map((s, i) => (
                <div key={i} className="flex gap-3">
                  <span className={`mt-0.5 text-sm font-bold ${s.ok ? 'text-emerald-600' : 'text-rose-600'}`}>{s.ok ? '✔' : '✘'}</span>
                  <div>
                    <div className="text-sm font-semibold text-slate-800">{s.name}</div>
                    <div className="text-sm text-slate-500 whitespace-pre-wrap">{s.detail}</div>
                  </div>
                </div>
              ))}
              <p className="text-xs text-slate-400">Pura record camera_log.txt me save hota hai (app folder).</p>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
