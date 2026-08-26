import React, { useState } from 'react';
import { ShieldCheck, Lock, User } from 'lucide-react';
import { api, setToken } from '../api';

export default function Login({ onLogin }) {
  const [username, setUsername] = useState('admin');
  const [password, setPassword] = useState('');
  const [err, setErr] = useState('');
  const [busy, setBusy] = useState(false);

  const submit = async (e) => {
    e.preventDefault();
    setBusy(true);
    setErr('');
    try {
      const r = await api('/api/login', { method: 'POST', body: JSON.stringify({ username, password }) });
      setToken(r.token);
      onLogin();
    } catch (ex) {
      setErr(ex.message || 'Login fail');
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
        <div className="text-xs text-slate-500">© {new Date().getFullYear()} 9x Security</div>
      </div>
      <div className="flex-1 flex items-center justify-center p-8">
        <form onSubmit={submit} className="w-full max-w-sm" data-testid="login-form">
          <h2 className="text-2xl font-semibold tracking-tight text-slate-900">Sign in</h2>
          <p className="text-sm text-slate-500 mt-1 mb-8">Apna username aur password daalein</p>
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
      </div>
    </div>
  );
}
