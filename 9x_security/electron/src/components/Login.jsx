import React, { useState } from 'react';
import { ShieldCheck, Lock, User } from 'lucide-react';
import { api, setToken } from '../api';
import BrandFooter from './BrandFooter.jsx';

export default function Login({ onLogin, notice = '' }) {
  const [username, setUsername] = useState('admin');
  const [password, setPassword] = useState('');
  const [err, setErr] = useState('');
  const [busy, setBusy] = useState(false);
  const [mustChange, setMustChange] = useState(false);
  const [np1, setNp1] = useState('');
  const [np2, setNp2] = useState('');

  const submit = async (e) => {
    e.preventDefault();
    setBusy(true);
    setErr('');
    try {
      const r = await api('/api/login', { method: 'POST', body: JSON.stringify({ username, password }) });
      setToken(r.token);
      if (r.must_change_password) {
        setMustChange(true);
      } else {
        onLogin();
      }
    } catch (ex) {
      setErr(ex.message || 'Login fail');
    } finally {
      setBusy(false);
    }
  };

  const changePassword = async (e) => {
    e.preventDefault();
    setErr('');
    if (np1.length < 6) { setErr('Password kam se kam 6 characters ka rakhein.'); return; }
    if (np1 !== np2) { setErr('Dono password same nahi hain.'); return; }
    if (np1 === '9xsecurity') { setErr('Default password dobara nahi rakh sakte.'); return; }
    setBusy(true);
    try {
      await api('/api/settings', { method: 'POST', body: JSON.stringify({ new_password: np1 }) });
      onLogin();
    } catch (ex) {
      setErr(ex.message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="h-screen w-full flex">
      <div className="hidden lg:flex w-[44%] bg-[#0f172a] flex-col justify-between p-12 text-slate-50">
        <div className="flex items-center gap-3">
          <div className="h-11 w-11 rounded-xl bg-[#1f6feb] flex items-center justify-center">
            <ShieldCheck size={26} />
          </div>
          <div>
            <div className="text-xl font-extrabold tracking-wide">9X SECURITY</div>
            <div className="text-xs text-slate-400">Gate Vehicle Monitor</div>
          </div>
        </div>
        <div>
          <h1 className="text-3xl font-semibold leading-snug tracking-tight">
            Har gaadi par nazar.<br />Har Entry/Exit ka record.
          </h1>
          <p className="mt-4 text-slate-400 text-sm leading-relaxed">
            AI vehicle detection &middot; Number plate &middot; WhatsApp alerts &middot; Snapshots
          </p>
        </div>
        <BrandFooter />
      </div>
      <div className="flex-1 flex flex-col items-center justify-center p-8 relative">
        <div className="lg:hidden absolute bottom-4 left-0 right-0 flex justify-center"><BrandFooter dark={false} className="text-center" /></div>
        {mustChange ? (
          <form onSubmit={changePassword} className="w-full max-w-sm" data-testid="force-change-form">
            <h2 className="text-2xl font-semibold tracking-tight text-slate-900">Naya password set karein</h2>
            <p className="text-sm text-slate-500 mt-1 mb-8">
              Security ke liye default password badalna zaroori hai (sirf ek baar).
            </p>
            <label className="label">Naya Password</label>
            <input type="password" className="input mb-4" value={np1}
              onChange={(e) => setNp1(e.target.value)} data-testid="force-new-password" autoFocus />
            <label className="label">Dobara likhein</label>
            <input type="password" className="input mb-2" value={np2}
              onChange={(e) => setNp2(e.target.value)} data-testid="force-confirm-password" />
            {err && <div className="text-sm text-rose-600 mb-2" data-testid="force-change-error">{err}</div>}
            <button type="submit" className="btn-primary w-full justify-center mt-4" disabled={busy} data-testid="force-change-btn">
              {busy ? 'Saving…' : 'Password Set Karo'}
            </button>
          </form>
        ) : (
        <form onSubmit={submit} className="w-full max-w-sm" data-testid="login-form">
          <h2 className="text-2xl font-semibold tracking-tight text-slate-900">Sign in</h2>
          <p className="text-sm text-slate-500 mt-1 mb-8">Apna username aur password daalein</p>
          {notice && (
            <div className="mb-4 rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-800" data-testid="login-notice">{notice}</div>
          )}
          <label className="label">Username</label>
          <div className="relative mb-4">
            <User size={16} className="absolute left-3 top-3 text-slate-400" />
            <input
              className="input pl-9"
              value={username}
              onChange={(e) => setUsername(e.target.value)}
              data-testid="login-username"
              autoFocus
            />
          </div>
          <label className="label">Password</label>
          <div className="relative mb-2">
            <Lock size={16} className="absolute left-3 top-3 text-slate-400" />
            <input
              type="password"
              className="input pl-9"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              data-testid="login-password"
            />
          </div>
          {err && (
            <div className="text-sm text-rose-600 mb-2" data-testid="login-error">{err}</div>
          )}
          <button type="submit" className="btn-primary w-full justify-center mt-4" disabled={busy} data-testid="login-btn">
            {busy ? 'Signing in…' : 'Login'}
          </button>
        </form>
        )}
      </div>
    </div>
  );
}
