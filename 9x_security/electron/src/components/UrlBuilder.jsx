import React, { useEffect, useState } from 'react';
import { X, Wand2, Radar } from 'lucide-react';
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
  const [scan, setScan] = useState(null);      // null | 'busy' | {subnets, cameras, seconds, error}
  const runScan = async () => {
    setScan('busy');
    try {
      const r = await api('/api/camera/scan', { method: 'POST', body: JSON.stringify({ port: f.port || 554 }), timeout: 90000 });
      setScan(r);
      if (!r.cameras.length) showToast('Network par koi camera nahi mila — camera on hai aur PC ke saath same network par hai?', 'error');
    } catch (e) {
      setScan({ cameras: [], subnets: [], error: e.message });
      showToast(e.message, 'error');
    }
  };
  const pickCamera = (c) => {
    setF((s) => ({ ...s, ip: c.ip, port: c.port || s.port, brand: c.brand || (c.onvif ? 'auto' : s.brand) }));
    showToast(`${c.ip} chuna — ab username/password check karke Test karein`, 'success');
  };
  const brandName = (id) => brands.find((b) => b.id === id)?.name?.split(' /')[0] || '';
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
          <div className="rounded-lg border border-dashed border-[#1f6feb]/40 bg-[#1f6feb]/5 p-3" data-testid="scan-box">
            <div className="flex items-center justify-between gap-3">
              <div className="text-sm text-slate-700">
                <span className="font-semibold">IP nahi pata?</span> Network scan karo — jo camera milega us par click karein.
              </div>
              <button type="button" className="btn-ghost whitespace-nowrap" onClick={runScan} disabled={scan === 'busy'} data-testid="url-builder-scan-btn">
                <Radar size={15} className={scan === 'busy' ? 'animate-spin' : ''} /> {scan === 'busy' ? 'Scan ho raha hai…' : 'Network scan karo'}
              </button>
            </div>
            {scan === 'busy' && <p className="text-xs text-slate-500 mt-2" data-testid="scan-progress">ONVIF discovery + port {f.port || 554} sweep chal raha hai (5–10 sec)…</p>}
            {scan && scan !== 'busy' && (
              <div className="mt-2" data-testid="scan-result">
                <div className="text-xs text-slate-500 mb-1.5" data-testid="scan-summary">
                  {scan.cameras.length} camera mile · subnet {scan.subnets?.map((s) => `${s}.x`).join(', ') || '-'} · {scan.seconds}s
                </div>
                <ul className="divide-y divide-slate-200 rounded-md border border-slate-200 bg-white max-h-44 overflow-y-auto" data-testid="scan-result-list">
                  {scan.cameras.map((c) => (
                    <li key={c.ip}>
                      <button type="button" onClick={() => pickCamera(c)} data-testid={`scan-item-${c.ip.replace(/\./g, '-')}`}
                        className="w-full flex items-center justify-between gap-3 px-3 py-2 text-left hover:bg-slate-50 transition-colors">
                        <div>
                          <div className="font-mono text-sm text-slate-900">{c.ip}<span className="text-slate-400">:{c.port}</span></div>
                          <div className="text-xs text-slate-500 truncate max-w-[22rem]">{[c.name, c.hardware].filter(Boolean).join(' · ') || c.server || (c.auth_needed ? 'Password chahiye (401)' : 'RTSP port open')}</div>
                        </div>
                        <div className="flex items-center gap-1.5 shrink-0">
                          {c.brand && <span className="text-[11px] px-2 py-0.5 rounded-full bg-emerald-50 text-emerald-700 border border-emerald-200">{brandName(c.brand) || c.brand}</span>}
                          {c.onvif && <span className="text-[11px] px-2 py-0.5 rounded-full bg-sky-50 text-sky-700 border border-sky-200">ONVIF</span>}
                        </div>
                      </button>
                    </li>
                  ))}
                  {!scan.cameras.length && <li className="px-3 py-2 text-sm text-slate-500" data-testid="scan-empty">Koi camera nahi mila{scan.error ? ` (${scan.error})` : ''}.</li>}
                </ul>
              </div>
            )}
          </div>
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
