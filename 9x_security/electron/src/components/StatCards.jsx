import React, { useEffect, useState } from 'react';
import { LogIn, LogOut } from 'lucide-react';
import { api, poll } from '../api';

export default function StatCards() {
  const [counts, setCounts] = useState({ Entry: 0, Exit: 0 });

  useEffect(() => {
    let live = true;
    const load = async () => {
      try {
        const c = await api('/api/counts');
        if (live) setCounts(c);
      } catch (_) { /* noop */ }
    };
    const stop = poll(load, 3000);
    return () => { live = false; stop(); };
  }, []);

  const Card = ({ title, value, Icon, iconBg, iconColor, testid }) => (
    <div className="card p-5 flex-1" data-testid={testid}>
      <div className="flex items-start justify-between">
        <div>
          <div className="text-xs font-semibold uppercase tracking-wider text-slate-500">{title}</div>
          <div className="text-4xl font-bold tracking-tight text-slate-900 mt-2">{value}</div>
        </div>
        <div className={`h-10 w-10 rounded-full ${iconBg} flex items-center justify-center`}>
          <Icon size={18} className={iconColor} />
        </div>
      </div>
      <div className="text-xs text-slate-400 mt-2">Aaj ({new Date().toLocaleDateString('en-IN')})</div>
    </div>
  );

  return (
    <div className="grid grid-cols-2 gap-4">
      <Card title="Entries Today" value={counts.Entry} Icon={LogIn} iconBg="bg-emerald-100" iconColor="text-emerald-600" testid="stat-entries-today" />
      <Card title="Exits Today" value={counts.Exit} Icon={LogOut} iconBg="bg-rose-100" iconColor="text-rose-500" testid="stat-exits-today" />
    </div>
  );
}
