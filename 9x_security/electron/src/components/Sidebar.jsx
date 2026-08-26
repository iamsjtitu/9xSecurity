import React from 'react';
import { LayoutDashboard, Settings, LogOut, ShieldCheck, Cctv } from 'lucide-react';

export default function Sidebar({ page, setPage, version, connected, onLogout }) {
  const Item = ({ id, icon: Icon, label, testid }) => (
    <button
      onClick={() => setPage(id)}
      data-testid={testid}
      className={`w-full flex items-center gap-3 rounded-lg px-4 py-3 text-sm font-medium transition-colors duration-200 ${
        page === id ? 'bg-[#1f6feb] text-white' : 'text-slate-400 hover:bg-slate-800 hover:text-slate-100'
      }`}
    >
      <Icon size={18} />
      {label}
    </button>
  );

  return (
    <aside className="w-60 bg-[#0f172a] flex flex-col shrink-0 text-slate-50" data-testid="sidebar">
      <div className="flex items-center gap-3 px-5 h-16 border-b border-slate-800">
        <div className="h-9 w-9 rounded-lg bg-[#1f6feb] flex items-center justify-center">
          <ShieldCheck size={20} />
        </div>
        <div>
          <div className="text-sm font-extrabold tracking-wider">9X SECURITY</div>
          <div className="text-[10px] text-slate-400 -mt-0.5">Gate Vehicle Monitor</div>
        </div>
      </div>
      <nav className="flex-1 p-3 space-y-1.5">
        <Item id="dashboard" icon={LayoutDashboard} label="Dashboard" testid="nav-dashboard" />
        <Item id="settings" icon={Settings} label="Settings" testid="nav-settings" />
      </nav>
      <div className="p-4 border-t border-slate-800 space-y-3">
        <div className="flex items-center gap-2 text-xs">
          <Cctv size={14} className={connected ? 'text-emerald-400' : 'text-slate-500'} />
          <span className={connected ? 'text-emerald-400' : 'text-slate-500'} data-testid="sidebar-cam-status">
            {connected ? 'Camera LIVE' : 'Camera offline'}
          </span>
          {connected && <span className="h-2 w-2 rounded-full bg-emerald-400 animate-pulse" />}
        </div>
        <button
          onClick={onLogout}
          data-testid="logout-btn"
          className="w-full flex items-center gap-2 rounded-lg px-3 py-2 text-xs font-medium text-slate-400 hover:bg-slate-800 hover:text-slate-100 transition-colors duration-200"
        >
          <LogOut size={14} /> Logout
        </button>
        <div className="text-[10px] text-slate-600 font-mono">v{version}</div>
      </div>
    </aside>
  );
}
