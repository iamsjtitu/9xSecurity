import React, { useEffect, useState } from 'react';
import { Send, Loader2 } from 'lucide-react';
import { api } from '../../api';

const parseRecipients = (txt) => txt.split(/[\n,;/]+/).map((x) => x.trim()).filter(Boolean);

// Step 3: WhatsApp alerts (optional) — key + numbers, test, save.
export default function StepWhatsApp({ showToast, onNext, onBack }) {
  const [s, setS] = useState({ wa_enabled: true, wa_api_key: '', wa_send_image: true });
  const [recText, setRecText] = useState('');
  const [busy, setBusy] = useState('');
  const [result, setResult] = useState('');

  useEffect(() => {
    api('/api/settings').then((d) => {
      setS({ wa_enabled: d.wa_recipients?.length ? !!d.wa_enabled : true, wa_api_key: d.wa_api_key || '', wa_send_image: d.wa_send_image !== false, wa_base_url: d.wa_base_url });
      setRecText((d.wa_recipients || []).join('\n'));
    }).catch(() => {});
  }, []);

  const payload = () => ({ ...s, wa_recipients: parseRecipients(recText) });
  const save = async () => {
    setBusy('save');
    try { await api('/api/settings', { method: 'POST', body: JSON.stringify(payload()) }); showToast('WhatsApp settings save ✔', 'success'); onNext(); } catch (e) { showToast(e.message, 'error'); }
    setBusy('');
  };
  const test = async () => {
    setBusy('test'); setResult('Text + photo bhej rahe hain (10–20 sec)…');
    try {
      const r = await api('/api/whatsapp/test', { method: 'POST', body: JSON.stringify(payload()), timeout: 120000 });
      setResult(r.detail);
      showToast(r.ok ? 'Test message gaya ✔ — WhatsApp check karein' : 'Test me kuch FAILED — neeche result dekhein', r.ok ? 'success' : 'error');
    } catch (e) { setResult(`Error: ${e.message}`); }
    setBusy('');
  };
  const skip = async () => {
    try { await api('/api/settings', { method: 'POST', body: JSON.stringify({ wa_enabled: false }) }); } catch (_) { /* ignore */ }
    onNext();
  };

  const field = 'input !bg-white text-sm mt-1';
  return (
    <div className="grid grid-cols-5 gap-6" data-testid="wizard-step-whatsapp">
      <div className="col-span-3 space-y-4">
        <label className="flex items-center gap-3 text-sm text-slate-700 cursor-pointer">
          <input type="checkbox" className="h-4 w-4 accent-[#1f6feb]" checked={!!s.wa_enabled} onChange={(e) => setS((x) => ({ ...x, wa_enabled: e.target.checked }))} data-testid="wizard-wa-enabled" />
          Har Entry/Exit par WhatsApp alert bhejo (photo ke saath)
        </label>
        <label className="block text-sm text-slate-700">wa.9x.design API key
          <input className={`${field} font-mono`} value={s.wa_api_key} onChange={(e) => setS((x) => ({ ...x, wa_api_key: e.target.value }))} placeholder="API key yahan paste karein" data-testid="wizard-wa-key" />
          <span className="text-xs text-slate-500">Key <b>wa.9x.design</b> par login karke Dashboard se milti hai.</span>
        </label>
        <label className="block text-sm text-slate-700">WhatsApp numbers (country code ke saath, ek line me ek)
          <textarea className={`${field} font-mono h-24`} value={recText} onChange={(e) => setRecText(e.target.value)} placeholder={'919876543210\n919123456789'} data-testid="wizard-wa-recipients" />
        </label>
        <div className="flex gap-3">
          <button className="btn-ghost" onClick={test} disabled={!!busy || !s.wa_api_key || !parseRecipients(recText).length} data-testid="wizard-wa-test-btn">
            {busy === 'test' ? <Loader2 size={14} className="animate-spin" /> : <Send size={14} />} Test message bhejo
          </button>
        </div>
        {result && <pre className="text-xs text-slate-700 whitespace-pre-wrap break-all font-mono rounded-md border border-slate-200 bg-slate-50 p-3" data-testid="wizard-wa-result">{result}</pre>}
      </div>
      <div className="col-span-2 space-y-4">
        <div className="rounded-lg border border-slate-200 bg-slate-50 p-4 text-sm text-slate-700 space-y-2">
          <div className="font-semibold text-slate-900">Alert aisa jayega</div>
          <p className="font-mono text-xs bg-white border border-slate-200 rounded p-2">🚗 ENTRY — truck<br />Time: 06-09-2026 03:14:09 PM<br />[photo]</p>
          <p>Internet band ho to alerts queue me rehte hain aur baad me apne aap jate hain.</p>
        </div>
        <div className="flex flex-col gap-2 pt-2">
          <button className="btn-primary justify-center" onClick={save} disabled={!!busy || (s.wa_enabled && (!s.wa_api_key || !parseRecipients(recText).length))} data-testid="wizard-next-btn">Save + Aage →</button>
          <button className="btn-ghost justify-center" onClick={skip} disabled={!!busy} data-testid="wizard-skip-btn">Abhi nahi — baad me Settings me</button>
          <button className="btn-ghost justify-center" onClick={onBack} data-testid="wizard-back-btn">← Line</button>
        </div>
      </div>
    </div>
  );
}
