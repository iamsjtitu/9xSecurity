import React, { useCallback, useEffect, useState } from 'react';
import { api, getToken, setToken, logout } from './api';
import Login from './components/Login.jsx';
import Sidebar from './components/Sidebar.jsx';
import Dashboard from './components/Dashboard.jsx';
import SettingsPage from './components/SettingsPage.jsx';

export default function App() {
  const [authed, setAuthed] = useState(!!getToken());
  const [page, setPage] = useState('dashboard');
  const [state, setState] = useState({ connected: false, status: '', version: '' });
  const [toast, setToast] = useState(null);

  const showToast = useCallback((msg, type = 'info') => {
    setToast({ msg, type });
    setTimeout(() => setToast(null), 4500);
  }, []);

  const refreshState = useCallback(async () => {
    try {
      const s = await api('/api/state');
      setState(s);
    } catch (e) {
      if (String(e.message).includes('401')) {
        setToken('');
        setAuthed(false);
      }
    }
  }, []);

  useEffect(() => {
    if (!authed) return;
    refreshState();
    const id = setInterval(refreshState, 2500);
    return () => clearInterval(id);
  }, [authed, refreshState]);

  if (!authed) {
    return <Login onLogin={() => { setAuthed(true); }} />;
  }

  return (
    <div className="h-screen flex w-full overflow-hidden bg-[#f8fafc]" data-testid="app-shell">
      <Sidebar
        page={page}
        setPage={setPage}
        version={state.version}
        connected={state.connected}
        onLogout={() => { logout(); setAuthed(false); }}
      />
      <div className="flex-1 flex flex-col h-screen overflow-hidden">
        <div className="flex-1 overflow-y-auto p-6">
          {page === 'dashboard' ? (
            <Dashboard state={state} refreshState={refreshState} showToast={showToast} />
          ) : (
            <SettingsPage showToast={showToast} />
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
