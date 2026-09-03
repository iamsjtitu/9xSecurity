import React, { useEffect, useState } from 'react';
import { MessageCircle, User, Lock, Download, Send, Clock, Activity } from 'lucide-react';
import { api } from '../api';
import DiagnosticsTab from './DiagnosticsTab.jsx';
import UpdateProgress from './UpdateProgress.jsx';

const TABS = [
  { id: 'whatsapp', label: 'WhatsApp Alerts', icon: MessageCircle },
  { id: 'timing', label: 'Timing', icon: Clock },
  { id: 'account', label: 'Account', icon: User },
  { id: 'security', label: 'Login / Security', icon: Lock },
  { id: 'updates', label: 'Updates', icon: Download },
  { id: 'diagnostics', label: 'Diagnostics', icon: Activity },
];

export default function SettingsPage({ showToast, tab = 'whatsapp', setTab }) {
  const [s, setS] = useState(null);
  const [newPass, setNewPass] = useState('');
  const [busy, setBusy] = useState(false);
  const [updInfo, setUpdInfo] = useState(null);
  const [job, setJob] = useState(null);

  useEffect(() => {
    api('/api/settings').then(setS).catch((e) => showToast(e.message, 'error'));
  }, []); // eslint-disable-line

  useEffect(() => {
    if (tab === 'updates' && !updInfo) checkUpdate();
  }, [tab]); // eslint-disable-line

  const save = async () => {
    setBusy(true);
    try {
      const body = { ...s };
      if (newPass) body.new_password = newPass;
      await api('/api/settings', { method: 'POST', body: JSON.stringify(body) });
      setNewPass('');
      showToast('Settings save ho gayi ✔', 'success');
    } catch (e) {
      showToast(e.message, 'error');
    } finally {
      setBusy(false);
    }
  };

  const waTest = async () => {
    setBusy(true);
    try {
      const r = await api('/api/whatsapp/test', { method: 'POST', body: JSON.stringify(s) });
      showToast(r.detail, r.ok ? 'success' : 'error');
    } catch (e) {
      showToast(e.message, 'error');
    } finally {
      setBusy(false);
    }
  };

  const checkUpdate = async () => {
    setBusy(true);
    setUpdInfo(null);
    try {
      const r = await api('/api/update/check');
      setUpdInfo(r);
    } catch (e) {
      showToast(e.message, 'error');
    } finally {
      setBusy(false);
    }
  };

  const applyUpdate = async () => {
    setBusy(true);
    try {
      const j = await api('/api/update/apply', { method: 'POST' });
      setJob(j);
    } catch (e) {
      showToast(e.message, 'error');
      setBusy(false);
    }
  };

  const cancelUpdate = async () => {
    try { await api('/api/update/cancel', { method: 'POST' }); } catch (e) { showToast(e.message, 'error'); }
  };

  // poll download/install progress while a job is active
  useEffect(() => {
    if (!job || !['checking', 'downloading', 'installing'].includes(job.state)) return undefined;
    const id = setInterval(async () => {
      try {
        const j = await api('/api/update/progress');
        setJob(j);
        if (['done', 'error', 'cancelled'].includes(j.state)) {
          setBusy(false);
          showToast(j.message, j.state === 'done' ? 'success' : j.state === 'error' ? 'error' : 'info');
          if (j.state === 'done' && j.ok && window.native?.quit) setTimeout(() => window.native.quit(), 1500);
        }
      } catch (_) { /* engine may be restarting */ }
    }, 1000);
    return () => clearInterval(id);
  }, [job?.state]); // eslint-disable-line

  // resume showing progress if a download is already running (e.g. user navigated away)
  useEffect(() => {
    if (tab !== 'updates') return;
    api('/api/update/progress').then((j) => {
      if (j.state !== 'idle') { setJob(j); if (['checking', 'downloading', 'installing'].includes(j.state)) setBusy(true); }
    }).catch(() => {});
  }, [tab]); // eslint-disable-line

  if (!s) return <div className="text-slate-400 text-sm">Loading…</div>;

  const set = (k, v) => setS({ ...s, [k]: v });

  return (
    <div className="flex gap-6 items-start" data-testid="settings-page">
      <div className="card p-2 w-56 shrink-0">
        {TABS.map(({ id, label, icon: Icon }) => (
          <button
            key={id}
            onClick={() => setTab(id)}
            data-testid={`settings-tab-${id}`}
            className={`w-full flex items-center gap-2.5 rounded-lg px-3.5 py-2.5 text-sm font-medium transition-colors duration-200 ${
              tab === id ? 'bg-[#1f6feb] text-white' : 'text-slate-600 hover:bg-slate-100'
            }`}
          >
            <Icon size={15} /> {label}
          </button>
        ))}
      </div>

      <div className={`card p-6 flex-1 ${tab === 'diagnostics' ? 'max-w-4xl' : 'max-w-2xl'}`}>
        {tab === 'diagnostics' && <DiagnosticsTab showToast={showToast} />}
        {tab === 'whatsapp' && (
          <div className="space-y-4">
            <h3 className="text-lg font-semibold text-slate-900">WhatsApp Alerts (wa.9x.design)</h3>
            <label className="flex items-center gap-2.5 text-sm text-slate-700 cursor-pointer">
              <input type="checkbox" className="h-4 w-4 accent-[#1f6feb]" checked={!!s.wa_enabled}
                onChange={(e) => set('wa_enabled', e.target.checked)} data-testid="wa-enabled-toggle" />
              Entry/Exit par WhatsApp alert bhejo
            </label>
            <div>
              <label className="label">API Key (Bearer)</label>
              <input type="password" className="input" value={s.wa_api_key || ''}
                placeholder={s.wa_api_key_set ? '•••• saved hai — badalne ke liye naya daalein' : 'wa9x_...'}
                onChange={(e) => set('wa_api_key', e.target.value)} data-testid="wa-api-key-input" />
            </div>
            <div>
              <label className="label">Recipients (ek number per line, 91XXXXXXXXXX)</label>
              <textarea className="input h-24 resize-none font-mono" value={(s.wa_recipients || []).join('\n')}
                onChange={(e) => set('wa_recipients', e.target.value.split('\n').map((x) => x.trim()).filter(Boolean))}
                data-testid="wa-recipients-input" />
            </div>
            <label className="flex items-center gap-2.5 text-sm text-slate-700 cursor-pointer">
              <input type="checkbox" className="h-4 w-4 accent-[#1f6feb]" checked={s.wa_send_image !== false}
                onChange={(e) => set('wa_send_image', e.target.checked)} data-testid="wa-send-image-toggle" />
              Photo ke saath bhejo
            </label>
            <div className="flex gap-3 pt-2">
              <button className="btn-primary" onClick={save} disabled={busy} data-testid="settings-save-btn">Save</button>
              <button className="btn-ghost" onClick={waTest} disabled={busy} data-testid="wa-test-btn">
                <Send size={14} /> Send Test Message
              </button>
            </div>
          </div>
        )}

        {tab === 'timing' && (
          <div className="space-y-6">
            <h3 className="text-lg font-semibold text-slate-900">Timing / Schedule</h3>

            <div className="rounded-lg border border-slate-200 p-4 space-y-3">
              <label className="flex items-center gap-2.5 text-sm font-medium text-slate-800 cursor-pointer">
                <input type="checkbox" className="h-4 w-4 accent-[#1f6feb]" checked={!!s.wa_schedule_enabled}
                  onChange={(e) => set('wa_schedule_enabled', e.target.checked)} data-testid="wa-schedule-toggle" />
                WhatsApp alerts sirf schedule ke time par bhejo
              </label>
              <div className="flex items-center gap-3 text-sm text-slate-700">
                <span>Se</span>
                <input type="time" className="input !w-auto" value={s.wa_start || '18:00'}
                  onChange={(e) => set('wa_start', e.target.value)} disabled={!s.wa_schedule_enabled}
                  data-testid="wa-start-time" />
                <span>Tak</span>
                <input type="time" className="input !w-auto" value={s.wa_end || '06:00'}
                  onChange={(e) => set('wa_end', e.target.value)} disabled={!s.wa_schedule_enabled}
                  data-testid="wa-end-time" />
              </div>
              <p className="text-xs text-slate-400">
                Raat ka window bhi chalega — jaise 18:00 se 06:00 = shaam 6 baje se subah 6 baje tak.
                Baaki time events capture honge par WhatsApp nahi jaayega.
              </p>
            </div>

            <div className="rounded-lg border border-slate-200 p-4 space-y-3">
              <label className="flex items-center gap-2.5 text-sm font-medium text-slate-800 cursor-pointer">
                <input type="checkbox" className="h-4 w-4 accent-[#1f6feb]" checked={!!s.capture_schedule_enabled}
                  onChange={(e) => set('capture_schedule_enabled', e.target.checked)} data-testid="capture-schedule-toggle" />
                Capture/detection bhi sirf schedule ke time par chale
              </label>
              <div className="flex items-center gap-3 text-sm text-slate-700">
                <span>Se</span>
                <input type="time" className="input !w-auto" value={s.capture_start || '18:00'}
                  onChange={(e) => set('capture_start', e.target.value)} disabled={!s.capture_schedule_enabled}
                  data-testid="capture-start-time" />
                <span>Tak</span>
                <input type="time" className="input !w-auto" value={s.capture_end || '06:00'}
                  onChange={(e) => set('capture_end', e.target.value)} disabled={!s.capture_schedule_enabled}
                  data-testid="capture-end-time" />
              </div>
              <p className="text-xs text-slate-400">
                OFF (default) = 24 ghante capture hota rahega. ON = window ke bahar sirf live video
                dikhegi, snapshots/events/alerts pause rahenge.
              </p>
            </div>

            <div className="rounded-lg border border-slate-200 p-4 space-y-3">
              <label className="flex items-center gap-2.5 text-sm font-medium text-slate-800 cursor-pointer">
                <input type="checkbox" className="h-4 w-4 accent-[#1f6feb]" checked={s.auto_delete_enabled !== false}
                  onChange={(e) => set('auto_delete_enabled', e.target.checked)} data-testid="auto-delete-toggle" />
                Purane records apne aap delete karo (Auto Delete)
              </label>
              <div className="flex items-center gap-3 text-sm text-slate-700">
                <span>Records rakho pichhle</span>
                <input type="number" min="1" max="365" className="input !w-24"
                  value={s.retention_days ?? 7}
                  onChange={(e) => set('retention_days', e.target.value)}
                  disabled={s.auto_delete_enabled === false}
                  data-testid="retention-days-input" />
                <span>din ke</span>
              </div>
              <p className="text-xs text-slate-400">
                ON (default): isse purane events + snapshots apne aap delete ho jaayenge (default 7 din)
                taaki disk kabhi full na ho. OFF: kuch delete nahi hoga.
              </p>
            </div>

            <button className="btn-primary" onClick={save} disabled={busy} data-testid="timing-save-btn">Save</button>
          </div>
        )}

        {tab === 'account' && (
          <div className="space-y-4">
            <h3 className="text-lg font-semibold text-slate-900">wa.9x.design Account (reference)</h3>
            <div>
              <label className="label">Email</label>
              <input className="input" value={s.wa_account_email || ''}
                onChange={(e) => set('wa_account_email', e.target.value)} data-testid="wa-email-input" />
            </div>
            <div>
              <label className="label">Password</label>
              <input type="password" className="input" value={s.wa_account_password || ''}
                placeholder={s.wa_account_password_set ? '•••• saved hai' : ''}
                onChange={(e) => set('wa_account_password', e.target.value)} data-testid="wa-password-input" />
            </div>
            <p className="text-xs text-slate-400">Ye sirf yaad rakhne ke liye store hota hai — sending API key se hoti hai.</p>
            <button className="btn-primary" onClick={save} disabled={busy} data-testid="account-save-btn">Save</button>
          </div>
        )}

        {tab === 'security' && (
          <div className="space-y-4">
            <h3 className="text-lg font-semibold text-slate-900">Login / Security</h3>
            <div>
              <label className="label">Username</label>
              <input className="input" value={s.auth_user || 'admin'}
                onChange={(e) => set('auth_user', e.target.value)} data-testid="auth-user-input" />
            </div>
            <div>
              <label className="label">Naya Password (khaali chhoda to nahi badlega)</label>
              <input type="password" className="input" value={newPass}
                onChange={(e) => setNewPass(e.target.value)} data-testid="new-password-input" />
            </div>
            <button className="btn-primary" onClick={save} disabled={busy} data-testid="security-save-btn">Save</button>
          </div>
        )}

        {tab === 'updates' && (
          <div className="space-y-4">
            <h3 className="text-lg font-semibold text-slate-900">Updates</h3>
            {s.gh_token_builtin ? (
              <div className="rounded-lg border border-emerald-200 bg-emerald-50 px-4 py-3 text-sm text-emerald-900"
                data-testid="gh-token-builtin-chip">
                ✔ GitHub token software me <b>inbuilt</b> hai — kisi bhi install me token daalne ki zaroorat nahi.
                Bas "Check for Updates" dabayein.
              </div>
            ) : (
              <p className="text-sm text-slate-500" data-testid="gh-token-missing-hint">
                Koi link/repo daalne ki zaroorat nahi — bas check karein. Repo PRIVATE ho to token permanent
                inbuilt karne ke liye GitHub repo → Settings → Secrets and variables → Actions me
                <code className="mx-1 rounded bg-slate-100 px-1">UPDATE_TOKEN</code> secret add karein
                (Fine-grained token, Contents: Read-only) aur dobara build karein. Ya neeche token daalein.
              </p>
            )}
            <div>
              <label className="label">
                GitHub Token ({s.gh_token_builtin ? 'optional override — inbuilt token ke upar' : 'sirf private repo ke liye, optional'})
              </label>
              <input type="password" className="input" value={s.gh_token || ''}
                placeholder={s.gh_token_set ? '•••• saved hai — badalne ke liye naya daalein' : ''}
                onChange={(e) => set('gh_token', e.target.value)} data-testid="gh-token-input" />
            </div>
            <div className="flex gap-3">
              <button className="btn-ghost" onClick={save} disabled={busy} data-testid="updates-save-btn">Save Token</button>
              <button className="btn-primary" onClick={checkUpdate} disabled={busy} data-testid="check-update-btn">
                <Download size={14} /> Check for Updates
              </button>
            </div>
            {updInfo && (
              <div className={`rounded-lg border p-4 text-sm ${updInfo.available ? 'border-emerald-200 bg-emerald-50 text-emerald-900' : 'border-slate-200 bg-slate-50 text-slate-700'}`}
                data-testid="update-info">
                <div className="font-semibold mb-1">
                  Current: v{updInfo.current}{updInfo.latest ? ` · Latest: v${updInfo.latest}` : ''}
                </div>
                <div>{updInfo.message}</div>
                {updInfo.available && !['checking', 'downloading', 'installing', 'done'].includes(job?.state) && (
                  <button className="btn-primary mt-3" onClick={applyUpdate} disabled={busy} data-testid="apply-update-btn">
                    {job?.state === 'error' || job?.state === 'cancelled' ? 'Dobara Download & Install' : 'Download & Install Now'}
                  </button>
                )}
              </div>
            )}
            <UpdateProgress job={job} onCancel={cancelUpdate} />
          </div>
        )}
      </div>
    </div>
  );
}
