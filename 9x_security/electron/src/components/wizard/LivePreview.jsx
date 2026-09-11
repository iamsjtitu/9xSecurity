import React, { useEffect, useRef, useState } from 'react';
import { BASE, getToken } from '../../api';

// Live JPEG poller for the wizard (canvas + createImageBitmap — CSP forbids blob: images).
// Children = SVG overlay in a 0..100 viewBox (line, arrow, points).
export default function LivePreview({ connected, onClick, children, interval = 400, testid = 'wizard-preview' }) {
  const [size, setSize] = useState({ w: 16, h: 9 });
  const [hasFrame, setHasFrame] = useState(false);
  const boxRef = useRef(null);
  const canvasRef = useRef(null);

  useEffect(() => {
    if (!connected) { setHasFrame(false); return undefined; }
    let live = true;
    let timer;
    const tick = async () => {
      if (!live) return;
      try {
        const res = await fetch(`${BASE}/api/frame?r=${Date.now()}`, { headers: { 'X-Auth-Token': getToken() }, cache: 'no-store' });
        if (res.ok) {
          const bmp = await createImageBitmap(await res.blob());
          const c = canvasRef.current;
          if (live && c) {
            if (c.width !== bmp.width || c.height !== bmp.height) { c.width = bmp.width; c.height = bmp.height; setSize({ w: bmp.width, h: bmp.height }); }
            c.getContext('2d').drawImage(bmp, 0, 0);
            setHasFrame(true);
          }
          bmp.close();
        }
      } catch (_) { /* next tick */ }
      timer = setTimeout(tick, interval);
    };
    tick();
    return () => { live = false; clearTimeout(timer); };
  }, [connected, interval]);

  const handleClick = (e) => {
    if (!onClick || !boxRef.current) return;
    const r = boxRef.current.getBoundingClientRect();
    onClick({ x: Math.min(Math.max((e.clientX - r.left) / r.width, 0), 1), y: Math.min(Math.max((e.clientY - r.top) / r.height, 0), 1) });
  };

  return (
    <div ref={boxRef} onClick={handleClick} data-testid={testid}
      className={`relative w-full bg-black rounded-lg overflow-hidden ${onClick ? 'cursor-crosshair' : ''}`}
      style={{ aspectRatio: `${size.w} / ${size.h}` }}>
      <canvas ref={canvasRef} className={`absolute inset-0 w-full h-full ${hasFrame ? '' : 'opacity-0'}`} data-testid={`${testid}-canvas`} />
      {!hasFrame && (
        <div className="absolute inset-0 flex items-center justify-center text-slate-400 text-sm" data-testid="wizard-preview-empty">
          {connected ? 'Pehla frame aa raha hai…' : 'Camera connect hone par live video yahan dikhega'}
        </div>
      )}
      {children && <svg className="absolute inset-0 w-full h-full pointer-events-none" viewBox="0 0 100 100" preserveAspectRatio="none">{children}</svg>}
    </div>
  );
}
