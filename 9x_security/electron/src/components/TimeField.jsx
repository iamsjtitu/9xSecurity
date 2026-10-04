import React from 'react';

const pad = (n) => String(n).padStart(2, '0');
const HOURS = [12, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11];

export const parse24 = (v) => {
  const m = /^(\d{1,2}):(\d{2})$/.exec(String(v || ''));
  if (!m) return { h: 0, mi: 0 };
  return { h: Math.min(23, Math.max(0, +m[1])), mi: Math.min(59, Math.max(0, +m[2])) };
};

// 'HH:MM' (24h) -> '12:00 AM', '6:30 PM'
export const fmt12 = (v) => {
  const { h, mi } = parse24(v);
  return `${h % 12 === 0 ? 12 : h % 12}:${pad(mi)} ${h >= 12 ? 'PM' : 'AM'}`;
};

// Hindi day-part so 12:00 AM / 12:00 PM can never be confused again
export const dayPart = (v) => {
  const { h } = parse24(v);
  if (h < 4) return 'raat';
  if (h < 12) return 'subah';
  if (h < 16) return 'dopahar';
  if (h < 19) return 'shaam';
  return 'raat';
};

export const describeWindow = (start, end) => {
  if (start === end) return 'poore 24 ghante';
  const s = parse24(start); const e = parse24(end);
  const overnight = s.h * 60 + s.mi > e.h * 60 + e.mi;
  return `${dayPart(start)} ${fmt12(start)} se ${overnight ? 'agle din ' : ''}${dayPart(end)} ${fmt12(end)} tak`;
};

export default function TimeField({ value, onChange, disabled, testid }) {
  const { h, mi } = parse24(value);
  const h12 = h % 12 === 0 ? 12 : h % 12;
  const ampm = h >= 12 ? 'PM' : 'AM';
  const set = (nh12, nmi, nap) => onChange(`${pad((nh12 % 12) + (nap === 'PM' ? 12 : 0))}:${pad(nmi)}`);
  const minutes = Array.from(new Set([...Array.from({ length: 12 }, (_, i) => i * 5), mi])).sort((a, b) => a - b);
  const cls = 'input !w-auto !px-2 !py-1.5 text-sm';
  return (
    <span className="inline-flex items-center gap-1" data-testid={testid} data-value={`${pad(h)}:${pad(mi)}`}>
      <select className={cls} value={h12} disabled={disabled} onChange={(e) => set(+e.target.value, mi, ampm)} data-testid={`${testid}-hour`}>
        {HOURS.map((x) => <option key={x} value={x}>{x}</option>)}
      </select>
      <span className="text-slate-400">:</span>
      <select className={cls} value={mi} disabled={disabled} onChange={(e) => set(h12, +e.target.value, ampm)} data-testid={`${testid}-minute`}>
        {minutes.map((x) => <option key={x} value={x}>{pad(x)}</option>)}
      </select>
      <select className={`${cls} font-semibold`} value={ampm} disabled={disabled} onChange={(e) => set(h12, mi, e.target.value)} data-testid={`${testid}-ampm`}>
        <option value="AM">AM (raat/subah)</option>
        <option value="PM">PM (dopahar/shaam)</option>
      </select>
    </span>
  );
}
