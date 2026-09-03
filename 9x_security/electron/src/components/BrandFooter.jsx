import React from 'react';
import { Phone } from 'lucide-react';

export const BRAND = { designer: '9x.design', url: 'https://9x.design', phone: '7587922222' };

export default function BrandFooter({ dark = true, className = '' }) {
  const base = dark ? 'text-slate-500' : 'text-slate-500';
  const strong = dark ? 'text-slate-300' : 'text-slate-700';
  return (
    <div className={`space-y-0.5 text-[11px] leading-relaxed ${base} ${className}`} data-testid="brand-footer">
      <div data-testid="brand-copyright">© {new Date().getFullYear()} 9x Security. All rights reserved.</div>
      <div>
        Designed by :{' '}
        <a href={BRAND.url} target="_blank" rel="noreferrer" className={`font-semibold hover:underline ${strong}`} data-testid="brand-designer">
          {BRAND.designer}
        </a>
      </div>
      <div className="flex items-center gap-1">
        <Phone size={11} />
        Mobile No:{' '}
        <a href={`tel:+91${BRAND.phone}`} className={`font-mono font-semibold ${strong}`} data-testid="brand-phone">
          {BRAND.phone}
        </a>
      </div>
    </div>
  );
}
