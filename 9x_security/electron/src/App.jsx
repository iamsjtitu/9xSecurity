import React, { useCallback, useEffect, useRef, useState } from 'react';
import { api, getToken, setToken, logout, poll } from './api';
import Login from './components/Login.jsx';
import Sidebar from './components/Sidebar.jsx';
import Dashboard from './components/Dashboard.jsx';
import SettingsPage from './components/SettingsPage.jsx';
import CaptureToast from './components/CaptureToast.jsx';

export default function App() {
  const [authed, setAuthed] = useState(!!getToken());
  const [page, setPage] = useState('dashboard');
  const [settingsTab, setSettingsTab] = useState('whatsapp');
  const [state, setState] = useState({ connected: false, status: '', version: '' });
  const [toast, setToast] = useState(null);
  const [capture, setCapture] = useState(null);
  const [lockNotice, setLockNotice] = useState('');
  const lastActivity = useRef(Date.now());
  const lastEventId = useRef(undefined); // undefined = nothing seen yet (skip stale event on login)

  const showToast = useCallback((msg, type = 'info') => {
    setToast({ msg, type });
    setTimeout(() => setToast(null), 4500);
  }, []);

  const refreshState = useCallback(async () => {
    try {
      const s = await api('/api/state');
      setState(s);
      const ev = s.last_event;
      const id = ev ? ev.id : null;
      if (lastEventId.current !== undefined && id != null && id !== lastEventId.current) setCapture(ev);
      else if (ev) {
        // same event, OCR finished: refresh the number on the visible toast
        setCapture((cur) => (cur && cur.id === id && (cur.plate !== ev.plate || cur.plate_status !== ev.plate_status)
          ? { ...cur, plate: ev.plate, plate_status: ev.plate_status, plate_source: ev.plate_source }
          : cur));
      }
      lastEventId.current = id;
    } catch (e) {
      if (String(e.message).includes('401')) {
        setToken('');
        setAuthed(false);
      }
    }
  }, []);

  useEffect(() => {
    if (!authed) return undefined;
    return poll(refreshState, 2500);
  }, [authed, refreshState]);

  // any 401 anywhere (engine restarted after update/reboot, session expired) -> login screen
  useEffect(() => {
    const onUnauth = () => { setAuthed(false); setLockNotice('Session khatam ho gaya (engine restart/update) — dobara login karein. Monitoring background me chalti rahi.'); };
    window.addEventListener('nx-unauthorized', onUnauth);
    return () => window.removeEventListener('nx-unauthorized', onUnauth);
  }, []);

  const lock = useCallback((why) => {
    logout();
    setAuthed(false);
    setLockNotice(why);
  }, []);

  // auto-lock after N idle minutes (setting auto_lock_minutes, 0 = off); camera + alerts keep running
  useEffect(() => {
    if (!authed) return undefined;
    const mins = Number(state.auto_lock_minutes ?? 10);
    if (!mins) return undefined;
    const bump = () => { lastActivity.current = Date.now(); };
    const evs = ['mousemove', 'mousedown', 'keydown', 'wheel', 'touchstart'];
    evs.forEach((e) => window.addEventListener(e, bump, { passive: true }));
    bump();
    const id = setInterval(() => {
      if (Date.now() - lastActivity.current >= mins * 60 * 1000) lock(`Auto-lock: ${mins} min se koi activity nahi thi. Password daal kar kholein — monitoring chalti rahi.`);
    }, 15000);
    return () => { clearInterval(id); evs.forEach((e) => window.removeEventListener(e, bump)); };
  }, [authed, state.auto_lock_minutes, lock]);

  if (!authed) {
    return <Login onLogin={() => { setLockNotice(''); setAuthed(true); }} notice={lockNotice} />;
  }

  return (
    <div className="h-screen flex w-full overflow-hidden bg-[#f8fafc]" data-testid="app-shell">
      <Sidebar
        page={page}
        setPage={setPage}
        version={state.version}
        connected={state.connected}
        outboxPending={state.outbox_pending || 0}
        updateLatest={state.update_available ? state.update_latest : ''}
        updateJob={state.update_job}
        onUpdateClick={() => { setSettingsTab('updates'); setPage('settings'); }}
        onLock={() => lock('Software lock ho gaya — password daal kar kholein. Monitoring chalti rahegi.')}
        onLogout={() => { logout(); setAuthed(false); }}
      />
      <div className="flex-1 flex flex-col h-screen overflow-hidden">
        <div className="flex-1 overflow-y-auto p-6">
          {page === 'dashboard' ? (
            <Dashboard state={state} refreshState={refreshState} showToast={showToast} />
          ) : (
            <SettingsPage showToast={showToast} tab={settingsTab} setTab={setSettingsTab} />
          )}
        </div>
        <div
          className="h-9 bg-white border-t border-slate-200 flex items-center justify-between px-4 text-xs text-slate-500 shrink-0"
          data-testid="status-bar"
        >
          <span data-testid="status-text" className="truncate">{state.status || 'Ready'}</span>
          <span className="font-mono">v{state.version}</span>
        </div>
      </div>
      <CaptureToast event={capture} onClose={() => setCapture(null)} />
      {toast && (
        <div
          data-testid="toast"
          className={`fixed bottom-12 right-6 z-50 max-w-md rounded-lg px-4 py-3 text-sm font-medium text-white shadow-lg ${
            toast.type === 'error' ? 'bg-rose-600' : toast.type === 'success' ? 'bg-emerald-600' : 'bg-slate-800'
          }`}
        >
          {toast.msg}
        </div>
      )}
    </div>
  );
}
