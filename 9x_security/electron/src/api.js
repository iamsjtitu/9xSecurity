const BASE = `http://127.0.0.1:${window.ENGINE_PORT || 8971}`;

let token = sessionStorage.getItem('nx_token') || '';
export const setToken = (t) => {
  token = t;
  sessionStorage.setItem('nx_token', t);
};
export const getToken = () => token;

export async function api(path, opts = {}) {
  const res = await fetch(`${BASE}${path}`, {
    ...opts,
    headers: {
      'Content-Type': 'application/json',
      'X-Auth-Token': token,
      ...(opts.headers || {}),
    },
  });
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

export const streamUrl = () => `${BASE}/api/stream?t=${token}&r=${Date.now()}`;
export const snapshotUrl = (p) => `${BASE}/api/snapshot?path=${encodeURIComponent(p)}&t=${token}`;
export { BASE };
