"""9x Security — daily Entry/Exit summary (per gate, per vehicle type) as text + PDF."""
import os
from collections import OrderedDict
from datetime import datetime, timedelta

import config

REPORT_DIR = os.path.join(config.BASE_DIR, "reports")
# label -> row shown in the report (everything else is 'Other vehicle')
ROWS = OrderedDict([
    ("truck", "Truck"), ("car", "Car"), ("bus", "Bus"), ("tractor", "Tractor"),
    ("two_wheeler", "Two-wheeler (bike/scooter)"), ("person", "Person (paidal)"), ("other", "Other vehicle"),
])
TWO_WHEELER = ("motorcycle", "bicycle", "two_wheeler")
ICON = {"truck": "🚛", "car": "🚗", "bus": "🚌", "tractor": "🚜", "two_wheeler": "🏍️", "person": "👤", "other": "🚙"}


def row_key(vehicle_type):
    v = str(vehicle_type or "").lower()
    if v in TWO_WHEELER:
        return "two_wheeler"
    return v if v in ROWS else "other"


def _empty():
    return {k: {"Entry": 0, "Exit": 0} for k in ROWS}


def parse_time(value, default=(21, 0)):
    """'HH:MM' -> (hh, mm) or None when not a valid 24h time."""
    try:
        hh, mm = (int(x) for x in str(value if value is not None else "").split(":"))
    except (TypeError, ValueError):
        return None if default is None else default
    if not (0 <= hh <= 23 and 0 <= mm <= 59):
        return None if default is None else default
    return hh, mm


def window_for(cfg, day, now=None):
    """The report for `day` covers the 24 h ending at the report time on that day:
    [day-1 @ T, day @ T). Reports therefore never miss the evening after T, and never count a
    vehicle twice. When day @ T is still in the future the window ends now (partial, 'abhi tak').
    Returns (start, end, partial)."""
    now = now or datetime.now()
    hh, mm = parse_time(cfg.get("daily_report_time"))
    end_full = datetime.strptime(day, "%Y-%m-%d").replace(hour=hh, minute=mm, second=0, microsecond=0)
    start = end_full - timedelta(days=1)
    if end_full > now:
        return start, now.replace(microsecond=0), True
    return start, end_full, False


def summarize(events, day, start=None, end=None, partial=False):
    """events = db rows inside the window -> {date, start, end, partial, gates: {gate: {row: {Entry, Exit}}},
    total: {row: ...}, entry, exit, events}. Person/bike groups count every member (events.count)."""
    gates, total = OrderedDict(), _empty()
    for ev in events:
        gate = str(ev.get("gate") or "").strip() or "Gate"
        d = ev.get("direction") if ev.get("direction") in ("Entry", "Exit") else None
        if d is None:
            continue
        n = int(ev.get("count") or 1)
        k = row_key(ev.get("vehicle_type"))
        gates.setdefault(gate, _empty())[k][d] += n
        total[k][d] += n
    return {
        "date": day,
        "start": start.isoformat(timespec="seconds") if start else "",
        "end": end.isoformat(timespec="seconds") if end else "",
        "partial": bool(partial),
        "gates": gates,
        "total": total,
        "entry": sum(r["Entry"] for r in total.values()),
        "exit": sum(r["Exit"] for r in total.values()),
        "events": len(events),
    }


def pretty_date(day):
    try:
        return datetime.strptime(day, "%Y-%m-%d").strftime("%d-%m-%Y (%A)")
    except ValueError:
        return day


def _fmt(iso):
    try:
        return datetime.fromisoformat(iso).strftime("%d-%m-%Y %I:%M %p")
    except (TypeError, ValueError):
        return str(iso or "")


def period_text(summary, pdf=False):
    """'17-10-2026 09:00 PM se 18-10-2026 09:00 PM tak' / full calendar day when T is 00:00 /
    '... se abhi (10:12 AM) tak' for a partial window."""
    start, end = summary.get("start"), summary.get("end")
    if not start or not end:
        return pretty_date(summary["date"])
    s, e = datetime.fromisoformat(start), datetime.fromisoformat(end)
    if summary.get("partial"):
        return f"{_fmt(start)} se abhi ({e.strftime('%I:%M %p')}) tak" if not pdf else f"{_fmt(start)} - now ({e.strftime('%I:%M %p')})"
    if s.hour == 0 and s.minute == 0 and (e - s) == timedelta(days=1):
        return pretty_date(s.strftime("%Y-%m-%d")) + (" (poora din)" if not pdf else " (full day)")
    return f"{_fmt(start)} se {_fmt(end)} tak" if not pdf else f"{_fmt(start)} - {_fmt(end)}"


def caption(summary):
    """WhatsApp text: totals per type (sent even when the PDF is rejected)."""
    lines = ["📊 9x Security — Daily Report", f"Report: {period_text(summary)}"]
    for gate, rows in summary["gates"].items():
        lines.append(f"\n🚧 {gate}")
        for k, name in ROWS.items():
            r = rows[k]
            if r["Entry"] or r["Exit"]:
                lines.append(f"{ICON[k]} {name}: Entry {r['Entry']} / Exit {r['Exit']}")
    if not summary["gates"]:
        lines.append("\nIs dauran koi Entry/Exit record nahi hua.")
    lines.append(f"\n✅ Total: Entry {summary['entry']} / Exit {summary['exit']}")
    lines.append("Poori detail PDF me hai 📎")
    return "\n".join(lines)


def _pdf_safe(text):
    """Core PDF fonts are Latin-1: drop emoji/other symbols instead of crashing."""
    return "".join(ch if ord(ch) < 256 else "" for ch in str(text)).strip()


def build_pdf(summary, events, path):
    """Write the PDF (summary tables per gate + full event list). Returns path."""
    from fpdf import FPDF

    os.makedirs(os.path.dirname(path), exist_ok=True)
    pdf = FPDF(orientation="P", unit="mm", format="A4")
    pdf.set_auto_page_break(auto=True, margin=14)
    pdf.add_page()
    pdf.set_font("Helvetica", "B", 18)
    pdf.set_text_color(31, 111, 235)
    pdf.cell(0, 10, "9x Security - Daily Report", new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Helvetica", "", 11)
    pdf.set_text_color(60, 60, 60)
    pdf.cell(0, 7, f"Period: {_pdf_safe(period_text(summary, pdf=True))}", new_x="LMARGIN", new_y="NEXT")
    pdf.cell(0, 7, f"Generated: {datetime.now().strftime('%d-%m-%Y %I:%M %p')}", new_x="LMARGIN", new_y="NEXT")
    pdf.ln(2)

    def table(title, rows):
        pdf.set_font("Helvetica", "B", 12)
        pdf.set_text_color(20, 20, 20)
        pdf.cell(0, 8, _pdf_safe(title), new_x="LMARGIN", new_y="NEXT")
        pdf.set_font("Helvetica", "B", 10)
        pdf.set_fill_color(31, 111, 235)
        pdf.set_text_color(255, 255, 255)
        widths = (90, 30, 30, 30)
        for w, h in zip(widths, ("Type", "Entry", "Exit", "Total")):
            pdf.cell(w, 8, h, border=1, fill=True, align="C" if w < 90 else "L")
        pdf.ln()
        pdf.set_font("Helvetica", "", 10)
        pdf.set_text_color(30, 30, 30)
        te = tx = 0
        shade = False
        for k, name in ROWS.items():
            r = rows[k]
            if not (r["Entry"] or r["Exit"]) and k != "truck" and k != "car":
                continue
            pdf.set_fill_color(245, 247, 250)
            pdf.cell(widths[0], 7, name, border=1, fill=shade)
            pdf.cell(widths[1], 7, str(r["Entry"]), border=1, fill=shade, align="C")
            pdf.cell(widths[2], 7, str(r["Exit"]), border=1, fill=shade, align="C")
            pdf.cell(widths[3], 7, str(r["Entry"] + r["Exit"]), border=1, fill=shade, align="C")
            pdf.ln()
            te, tx, shade = te + r["Entry"], tx + r["Exit"], not shade
        pdf.set_font("Helvetica", "B", 10)
        pdf.set_fill_color(226, 232, 240)
        pdf.cell(widths[0], 7, "TOTAL", border=1, fill=True)
        pdf.cell(widths[1], 7, str(te), border=1, fill=True, align="C")
        pdf.cell(widths[2], 7, str(tx), border=1, fill=True, align="C")
        pdf.cell(widths[3], 7, str(te + tx), border=1, fill=True, align="C")
        pdf.ln(10)

    if len(summary["gates"]) != 1:
        table("All gates", summary["total"])
    for gate, rows in summary["gates"].items():
        table(f"Gate: {gate}", rows)
    if not summary["gates"]:
        pdf.set_font("Helvetica", "I", 11)
        pdf.cell(0, 8, "No Entry/Exit recorded on this day.", new_x="LMARGIN", new_y="NEXT")

    if events:
        pdf.add_page()
        pdf.set_font("Helvetica", "B", 12)
        pdf.set_text_color(20, 20, 20)
        pdf.cell(0, 8, f"All events ({len(events)})", new_x="LMARGIN", new_y="NEXT")
        pdf.set_font("Helvetica", "B", 9)
        pdf.set_fill_color(31, 111, 235)
        pdf.set_text_color(255, 255, 255)
        cols = ((22, "Time"), (40, "Gate"), (40, "Type"), (18, "Dir"), (14, "Qty"), (46, "Plate"))
        for w, h in cols:
            pdf.cell(w, 7, h, border=1, fill=True)
        pdf.ln()
        pdf.set_font("Helvetica", "", 9)
        pdf.set_text_color(30, 30, 30)
        pdf.set_fill_color(245, 247, 250)
        shade = False
        for ev in sorted(events, key=lambda e: str(e.get("timestamp", ""))):
            try:
                t = datetime.fromisoformat(str(ev.get("timestamp", ""))).strftime("%I:%M %p")
            except ValueError:
                t = str(ev.get("time", ""))[:8]
            k = row_key(ev.get("vehicle_type"))
            typ = ROWS[k].split(" (")[0] if k != "other" else str(ev.get("vehicle_type", "")).title()
            plate = str(ev.get("plate") or "") or ("-" if k in ("person", "two_wheeler") else "Not detected")
            vals = (t, str(ev.get("gate") or "Gate")[:22], typ, str(ev.get("direction", "")), str(int(ev.get("count") or 1)), plate[:18])
            for (w, _), v in zip(cols, vals):
                pdf.cell(w, 6.5, _pdf_safe(v), border=1, fill=shade)
            pdf.ln()
            shade = not shade
    pdf.output(path)
    return path


def report_path(day):
    return os.path.join(REPORT_DIR, f"9x_Report_{day}.pdf")


def generate(db, day, cfg, now=None):
    """Summary + PDF for the report of `day` (24 h ending at the report time; 'abhi tak' when that
    time is still ahead). Returns (summary, pdf_path, caption_text)."""
    now = now or datetime.now()
    start, end, partial = window_for(cfg, day, now)
    events = db.events_between(start.isoformat(timespec="seconds"), end.isoformat(timespec="seconds"))
    summary = summarize(events, day, start, end, partial)
    path = build_pdf(summary, events, report_path(day))
    return summary, path, caption(summary)


def due_days(cfg, now=None):
    """Days whose report is due and not yet sent: yesterday (PC was off at report time) and today
    (time reached). Nothing is due when the feature is off or the time is invalid."""
    now = now or datetime.now()
    if not cfg.get("daily_report_enabled"):
        return []
    t = parse_time(cfg.get("daily_report_time"), default=None)
    if t is None:
        return []
    hh, mm = t
    last = str(cfg.get("daily_report_last") or "")
    out = []
    for d in (now.date() - timedelta(days=1), now.date()):
        key = d.isoformat()
        if key > last and now >= datetime.combine(d, datetime.min.time()).replace(hour=hh, minute=mm):
            out.append(key)
    return out


def mark_baseline(cfg, now=None):
    """Called when the feature is switched on / time changed: nothing retroactive gets sent — the
    first report goes out at the next scheduled time."""
    now = now or datetime.now()
    hh, mm = parse_time(cfg.get("daily_report_time"))
    today = now.date()
    cfg["daily_report_last"] = (today if now >= now.replace(hour=hh, minute=mm, second=0, microsecond=0)
                                else today - timedelta(days=1)).isoformat()
    return cfg["daily_report_last"]


def next_run(cfg, now=None):
    """'aaj 09:00 PM' / 'kal 09:00 PM' for the settings card."""
    now = now or datetime.now()
    t = parse_time(cfg.get("daily_report_time"), default=None)
    if t is None:
        return ""
    at = now.replace(hour=t[0], minute=t[1], second=0, microsecond=0)
    last = str(cfg.get("daily_report_last") or "")
    if now < at and last < now.date().isoformat():
        return "aaj " + at.strftime("%I:%M %p")
    return "kal " + at.strftime("%I:%M %p")


__all__ = ["generate", "summarize", "caption", "build_pdf", "due_days", "mark_baseline", "next_run",
           "report_path", "window_for", "parse_time", "period_text", "REPORT_DIR"]
