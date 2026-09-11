import React, { useEffect, useState } from 'react';
import { X, Wand2 } from 'lucide-react';
import { api } from '../api';

// Camera URL Builder: pick brand -> IP/user/password/channel/stream -> exact RTSP URL
export default function UrlBuilder({ initialUrl, onApply, onClose, showToast }) {
  const [brands, setBrands] = useState([]);
  const [f, setF] = useState(() => ({ brand: 'hikvision', ip: '', port: 554, user: 'admin', password: '', channel: 1, stream: 'main', custom_path: '' }));
  const [preview, setPreview] = useState('');
  const [busy, setBusy] = useState(false);
  const set = (k, v) => setF((s) => ({ ...s, [k]: v }));

  useEffect(() => {
    api('/api/camera/brands').then((r) => setBrands(r.brands || [])).catch((e) => showToast(e.message, 'error'));
    // prefill IP / user / password from the URL already in the box
    const m = /^rtsps?:\/\/(?:([^:@/]*)(?::(.*))?@)?([^:/?#]+)(?::(\d+))?/i.exec(initialUrl || '');
    if (m) setF((s) => ({ ...s, user: m[1] ? decodeURIComponent(m[1]) : s.user, password: m[2] ? decodeURIComponent(m[2]) : '', ip: m[3] || '', port: Number(m[4] || 554) }));
  }, []); // eslint-disable-line

  useEffect(() => {
    if (!f.ip) { setPreview(''); return; }
    const t = setTimeout(() => {
      api('/api/camera/build_url', { method: 'POST', body: JSON.stringify(f) }).then((r) => setPreview(r.url)).catch(() => setPreview(''));
    }, 250);
    return () => clearTimeout(t);
  }, [f]);

  const brand = brands.find((b) => b.id === f.brand);
  const apply = async (test) => {
    if (!preview) { showToast('Camera ka IP address daalein', 'error'); return; }
    setBusy(true);
    try { await onApply(preview, test); } finally { setBusy(false); }
  };

  const field = 'input !bg-white text-sm';
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-6" data-testid="url-builder-modal">
      <div className="card w-full max-w-2xl max-h-[85vh] overflow-y-auto">
        <div className="flex items-center justify-between border-b border-slate-200 px-5 py-4">
          <h3 className="text-lg font-semibold text-slate-900 flex items-center gap-2"><Wand2 size={18} className="text-[#1f6feb]" /> Camera URL Builder</h3>
          <button onClick={onClose} className="text-slate-400 hover:text-slate-700" data-testid="url-builder-close"><X size={18} /></button>
        </div>
        <div className="p-5 space-y-4">
          <p className="text-sm text-slate-500">Camera ka brand chunein, IP/password bharein — sahi RTSP URL apne aap ban jayega.</p>
          <label className="block text-sm text-slate-700">Camera brand
            <select className={`${field} mt-1`} value={f.brand} onChange={(e) => set('brand', e.target.value)} data-testid="url-builder-brand">
              {brands.map((b) => <option key={b.id} value={b.id}>{b.name}</option>)}
            </select>
          </label>
          {brand?.note && <p className="text-xs text-amber-700 bg-amber-50 border border-amber-200 rounded-md px-3 py-2" data-testid="url-builder-note">{brand.note}</p>}
          <div className="grid grid-cols-3 gap-3">
            <label className="col-span-2 block text-sm text-slate-700">Camera / NVR IP address
              <input className={`${field} mt-1`} placeholder="192.168.1.28" value={f.ip} onChange={(e) => set('ip', e.target.value)} data-testid="url-builder-ip" autoFocus />
            </label>
            <label className="block text-sm text-slate-700">Port
              <input className={`${field} mt-1`} type="number" value={f.port} onChange={(e) => set('port', Number(e.target.value) || 554)} data-testid="url-builder-port" />
            </label>
            <label className="block text-sm text-slate-700">Username
              <input className={`${field} mt-1`} value={f.user} onChange={(e) => set('user', e.target.value)} data-testid="url-builder-user" />
            </label>
            <label className="block text-sm text-slate-700">Password
              <input className={`${field} mt-1`} value={f.password} onChange={(e) => set('password', e.target.value)} placeholder="@ ya # ho to bhi chalega" data-testid="url-builder-pass" />
            </label>
            <label className="block text-sm text-slate-700">Channel (NVR/DVR)
              <input className={`${field} mt-1`} type="number" min="1" max="64" value={f.channel} onChange={(e) => set('channel', Number(e.target.value) || 1)} data-testid="url-builder-channel" />
            </label>
          </div>
          {f.brand === 'custom' && (
            <label className="block text-sm text-slate-700">Stream path
              <input className={`${field} mt-1 font-mono`} placeholder="/Streaming/Channels/101" value={f.custom_path} onChange={(e) => set('custom_path', e.target.value)} data-testid="url-builder-custom-path" />
            </label>
          )}
          {f.brand !== 'custom' && f.brand !== 'auto' && (
            <div className="flex items-center gap-4 text-sm text-slate-700">
              <span>Stream:</span>
              {[['main', 'Main (full HD, plate ke liye)'], ['sub', 'Sub (halka, HEVC/slow PC ke liye)']].map(([v, l]) => (
                <label key={v} className="flex items-center gap-1.5 cursor-pointer">
                  <input type="radio" name="stream" className="accent-[#1f6feb]" checked={f.stream === v} onChange={() => set('stream', v)} data-testid={`url-builder-stream-${v}`} /> {l}
                </label>
              ))}
            </div>
          )}
          <div className="rounded-lg border border-slate-200 bg-slate-50 px-3 py-2">
            <div className="text-xs font-semibold text-slate-500 mb-1">RTSP URL</div>
            <div className="font-mono text-sm text-slate-800 break-all min-h-[1.25rem]" data-testid="url-builder-preview">{preview || '— IP address bharein —'}</div>
          </div>
          <div className="flex gap-3 pt-1">
            <button className="btn-primary" disabled={busy || !preview} onClick={() => apply(true)} data-testid="url-builder-test-btn">Use karo + Test</button>
            <button className="btn-ghost" disabled={busy || !preview} onClick={() => apply(false)} data-testid="url-builder-apply-btn">Sirf URL box me daalo</button>
          </div>
        </div>
      </div>
    </div>
  );
}
