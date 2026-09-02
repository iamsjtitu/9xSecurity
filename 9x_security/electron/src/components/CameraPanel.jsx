import React, { useCallback, useEffect, useRef, useState } from 'react';
import { Cctv, Plug, Unplug, Stethoscope, X, ZoomIn, ZoomOut, RotateCcw, Focus } from 'lucide-react';
import { api, BASE, getToken } from '../api';

const FRAME_MS = 150;

export default function CameraPanel({ state, refreshState, showToast, drawMode, setDrawMode }) {
  const [url, setUrl] = useState(state.rtsp_url || '');
  const [busy, setBusy] = useState(false);
  const [testing, setTesting] = useState(false);
  const [testResult, setTestResult] = useState(null);
  const [firstPoint, setFirstPoint] = useState(null);
  const [zoomLevel, setZoomLevel] = useState(1);
  const [stale, setStale] = useState(false);
  const [ptzSupported, setPtzSupported] = useState(true);
  const boxRef = useRef(null);
  const canvasRef = useRef(null);
  const urlTouched = useRef(false);
  const zoomRef = useRef({ z: 1, cx: 0.5, cy: 0.5 });
  const lastBmpRef = useRef(null);
  const lastOkRef = useRef(0);
  const dragRef = useRef(null);

  useEffect(() => {
    if (!urlTouched.current && state.rtsp_url && !url) setUrl(state.rtsp_url);
  }, [state.rtsp_url]); // eslint-disable-line

  // ---- canvas drawing with digital zoom crop ----
  const draw = useCallback((bmp) => {
    const c = canvasRef.current;
    if (!c || !bmp) return;
    const ctx = c.getContext('2d');
    const { z, cx, cy } = zoomRef.current;
    const sw = bmp.width / z;
    const sh = bmp.height / z;
    const sx = Math.max(0, Math.min(bmp.width - sw, cx * bmp.width - sw / 2));
    const sy = Math.max(0, Math.min(bmp.height - sh, cy * bmp.height - sh / 2));
    ctx.drawImage(bmp, sx, sy, sw, sh, 0, 0, c.width, c.height);
  }, []);

  const setZoom = useCallback((nz) => {
    nz = Math.max(1, Math.min(6, nz));
    zoomRef.current.z = nz;
    if (nz === 1) { zoomRef.current.cx = 0.5; zoomRef.current.cy = 0.5; }
    setZoomLevel(nz);
    if (lastBmpRef.current) draw(lastBmpRef.current);
  }, [draw]);

  useEffect(() => { if (drawMode) setZoom(1); }, [drawMode, setZoom]);

  // ---- frame polling loop (memory-safe, replaces long-lived MJPEG <img>) ----
  useEffect(() => {
    if (!state.connected) {
      setStale(false);
      if (lastBmpRef.current) { lastBmpRef.current.close(); lastBmpRef.current = null; }
      return undefined;
    }
    setPtzSupported(true);
    let live = true;
    let timer;
    const tick = async () => {
      if (!live) return;
      try {
        const res = await fetch(`${BASE}/api/frame?r=${Date.now()}`, {
          headers: { 'X-Auth-Token': getToken() },
          cache: 'no-store',
        });
        if (res.ok) {
          const blob = await res.blob();
          const bmp = await createImageBitmap(blob);
          if (!live) { bmp.close(); return; }
          if (lastBmpRef.current) lastBmpRef.current.close();
          lastBmpRef.current = bmp;
          draw(bmp);
          lastOkRef.current = Date.now();
        }
      } catch (_) { /* engine busy/offline: next tick */ }
      timer = setTimeout(tick, FRAME_MS);
    };
    lastOkRef.current = Date.now();
    tick();
    const staleTimer = setInterval(() => {
      setStale(Date.now() - lastOkRef.current > 6000);
    }, 2000);
    return () => {
      live = false;
      clearTimeout(timer);
      clearInterval(staleTimer);
      if (lastBmpRef.current) { lastBmpRef.current.close(); lastBmpRef.current = null; }
    };
  }, [state.connected, draw]);

  // ---- wheel zoom (native listener: preventDefault needs passive:false) ----
  useEffect(() => {
    const el = boxRef.current;
    if (!el) return undefined;
    const onWheel = (e) => {
      if (!state.connected || drawMode) return;
      e.preventDefault();
      setZoom(zoomRef.current.z * (e.deltaY < 0 ? 1.2 : 1 / 1.2));
    };
    el.addEventListener('wheel', onWheel, { passive: false });
    return () => el.removeEventListener('wheel', onWheel);
  }, [state.connected, drawMode, setZoom]);

  // ---- drag to pan when zoomed ----
  const onMouseDown = (e) => {
    if (drawMode || zoomRef.current.z <= 1) return;
    dragRef.current = { x: e.clientX, y: e.clientY, cx: zoomRef.current.cx, cy: zoomRef.current.cy };
  };
  const onMouseMove = (e) => {
    const d = dragRef.current;
    if (!d || !boxRef.current) return;
    const rect = boxRef.current.getBoundingClientRect();
    const { z } = zoomRef.current;
    const half = 0.5 / z;
    zoomRef.current.cx = Math.max(half, Math.min(1 - half, d.cx - (e.clientX - d.x) / rect.width / z));
    zoomRef.current.cy = Math.max(half, Math.min(1 - half, d.cy - (e.clientY - d.y) / rect.height / z));
    if (lastBmpRef.current) draw(lastBmpRef.current);
  };
  const endDrag = () => { dragRef.current = null; };

  // ---- PTZ optical zoom (press & hold) ----
  const ptz = async (dir, action) => {
    try {
      const r = await api('/api/ptz/zoom', { method: 'POST', body: JSON.stringify({ dir, action }) });
      if (r.supported === false) {
        setPtzSupported(false);
        if (action === 'start') showToast(r.detail, 'info');
      } else if (!r.ok && action === 'start') {
        showToast(r.detail, 'error');
      }
    } catch (e) {
      if (action === 'start') showToast(e.message, 'error');
    }
  };

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

  const zoomBtn = 'h-8 w-8 flex items-center justify-center rounded-md bg-black/60 text-white hover:bg-black/80 transition-colors duration-150 disabled:opacity-40';

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
        onMouseDown={onMouseDown}
        onMouseMove={onMouseMove}
        onMouseUp={endDrag}
        onMouseLeave={endDrag}
        className={`relative w-full aspect-video bg-[#020617] select-none ${
          drawMode ? 'cursor-crosshair' : zoomLevel > 1 ? 'cursor-move' : ''
        }`}
        data-testid="camera-view"
      >
        {state.connected ? (
          <>
            <canvas
              ref={canvasRef}
              width={960}
              height={540}
              className="absolute inset-0 w-full h-full object-contain"
              data-testid="video-canvas"
            />
            <div className="absolute top-3 right-3 flex items-center gap-1.5 rounded-full bg-black/60 px-3 py-1 text-xs font-semibold text-white">
              <span className={`h-2 w-2 rounded-full ${stale ? 'bg-amber-400' : 'bg-[#ef4444] animate-pulse'}`} /> LIVE
            </div>
            {stale && (
              <div
                className="absolute inset-x-0 top-1/2 -translate-y-1/2 mx-auto w-fit rounded-lg bg-amber-500/95 px-4 py-2 text-sm font-semibold text-black"
                data-testid="stale-overlay"
              >
                Stream ruk gaya — engine dobara connect kar raha hai…
              </div>
            )}
            <div className="absolute bottom-3 right-3 flex items-center gap-1.5" data-testid="zoom-controls">
              <button className={zoomBtn} onClick={() => setZoom(zoomRef.current.z / 1.2)} disabled={zoomLevel <= 1} title="Digital zoom out" data-testid="digital-zoom-out-btn">
                <ZoomOut size={15} />
              </button>
              <span className="rounded-md bg-black/60 px-2 py-1.5 text-xs font-mono text-white min-w-[46px] text-center" data-testid="zoom-level">
                {zoomLevel.toFixed(1)}x
              </span>
              <button className={zoomBtn} onClick={() => setZoom(zoomRef.current.z * 1.2)} disabled={zoomLevel >= 6} title="Digital zoom in" data-testid="digital-zoom-in-btn">
                <ZoomIn size={15} />
              </button>
              <button className={zoomBtn} onClick={() => setZoom(1)} disabled={zoomLevel <= 1} title="Reset zoom" data-testid="zoom-reset-btn">
                <RotateCcw size={15} />
              </button>
              {ptzSupported && (
                <>
                  <span className="mx-1 h-5 w-px bg-white/25" />
                  <button
                    className={`${zoomBtn} !w-auto px-2 gap-1 text-xs font-semibold`}
                    onMouseDown={() => ptz('out', 'start')}
                    onMouseUp={() => ptz('out', 'stop')}
                    onMouseLeave={() => ptz('out', 'stop')}
                    title="Camera optical zoom out (dabaye rakhein)"
                    data-testid="ptz-zoom-out-btn"
                  >
                    <Focus size={13} /> −
                  </button>
                  <button
                    className={`${zoomBtn} !w-auto px-2 gap-1 text-xs font-semibold`}
                    onMouseDown={() => ptz('in', 'start')}
                    onMouseUp={() => ptz('in', 'stop')}
                    onMouseLeave={() => ptz('in', 'stop')}
                    title="Camera optical zoom in (dabaye rakhein)"
                    data-testid="ptz-zoom-in-btn"
                  >
                    <Focus size={13} /> +
                  </button>
                </>
              )}
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
