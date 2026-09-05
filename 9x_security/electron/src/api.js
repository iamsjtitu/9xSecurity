const BASE = `http://127.0.0.1:${window.ENGINE_PORT || 8971}`;

let token = sessionStorage.getItem('nx_token') || '';
export const setToken = (t) => {
  token = t;
  sessionStorage.setItem('nx_token', t);
};
export const getToken = () => token;

export async function api(path, opts = {}) {
  // Every call has a deadline: a busy engine must never leave the UI "Loading…" forever.
  const { timeout = 15000, ...init } = opts;
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), timeout);
  let res;
  try {
    res = await fetch(`${BASE}${path}`, {
      ...init,
      signal: ctrl.signal,
      headers: {
        'Content-Type': 'application/json',
        'X-Auth-Token': token,
        ...(init.headers || {}),
      },
    });
  } catch (e) {
    clearTimeout(timer);
    if (e && e.name === 'AbortError') throw new Error('Engine respond nahi kar raha (timeout) — kuch second baad dobara try karein');
    throw e;
  }
  clearTimeout(timer);
  if (res.status === 401) {
    // engine restarted (update/reboot) or session expired: tokens live in engine memory.
    // Go straight back to the login screen instead of 'unauthorized' errors everywhere.
    setToken('');
    window.dispatchEvent(new CustomEvent('nx-unauthorized'));
    throw new Error('Session khatam — dobara login karein');
  }
  if (!res.ok) {
    let msg = `HTTP ${res.status}`;
    try {
      const d = await res.json();
      if (d.detail) msg = typeof d.detail === 'string' ? d.detail : JSON.stringify(d.detail);
    } catch (_) { /* noop */ }
    throw new Error(msg);
  }
  return res.json();
}

// Sequential polling: the next request starts only after the previous one finished,
// so a slow engine can never pile up dozens of pending requests (browser has ~6 sockets/host).
export function poll(fn, ms) {
  let live = true;
  let timer;
  const loop = async () => {
    if (!live) return;
    try { await fn(); } catch (_) { /* caller handles */ }
    if (live) timer = setTimeout(loop, ms);
  };
  loop();
  return () => { live = false; clearTimeout(timer); };
}

export const streamUrl = () => `${BASE}/api/stream?t=${token}&r=${Date.now()}`;
export const snapshotUrl = (p) => `${BASE}/api/snapshot?path=${encodeURIComponent(p)}&t=${token}`;
export const logout = async () => {
  try { await api('/api/logout', { method: 'POST' }); } catch (_) { /* noop */ }
  setToken('');
};
export { BASE };
