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
VEHICLE_KEYS = ("truck", "car", "bus", "tractor", "other")  # 4-wheelers: what the reports are about
PEOPLE_KEYS = ("two_wheeler", "person")


def row_key(vehicle_type):
    v = str(vehicle_type or "").lower()
    if v in TWO_WHEELER:
        return "two_wheeler"
    return v if v in ROWS else "other"


def is_vehicle(ev):
    return row_key(ev.get("vehicle_type")) in VEHICLE_KEYS


def vehicles_only(events):
    """Reports cover 4-wheelers only (truck/car/bus/tractor/other): no paidal, no two-wheelers."""
    return [ev for ev in events if is_vehicle(ev)]


def _detail_order(ev):
    """Known plates first, same plate together (Entry then Exit by time), unknown plates by time."""
    plate = str(ev.get("plate") or "")
    return (0 if plate else 1, plate, str(ev.get("timestamp", "")))


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


def summarize(events, day, start=None, end=None, partial=False, vehicles=False):
    """events = db rows inside the window -> {date, start, end, partial, gates: {gate: {row: {Entry, Exit}}},
    total: {row: ...}, entry, exit, events, vehicles_only}. Person/bike groups count every member (events.count)."""
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
        "vehicles_only": bool(vehicles),
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
    if s.hour == 0 and s.minute == 0 and e.hour == 0 and e.minute == 0 and (e - s).days > 1:
        last = (e - timedelta(days=1)).strftime("%d-%m-%Y")
        n = (e - s).days
        return f"{s.strftime('%d-%m-%Y')} se {last} tak ({n} din)" if not pdf else f"{s.strftime('%d-%m-%Y')} - {last} ({n} days)"
    return f"{_fmt(start)} se {_fmt(end)} tak" if not pdf else f"{_fmt(start)} - {_fmt(end)}"


def caption(summary):
    """WhatsApp text: totals per type (sent even when the PDF is rejected)."""
    lines = ["📊 9x Security — Daily Report", f"Report: {period_text(summary)}"]
    if summary.get("vehicles_only"):
        lines.append("Sirf 4-wheeler (truck / car / bus / tractor) — paidal aur 2-wheeler shamil nahi")
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


def _spans_days(summary):
    """True when the report window touches more than one calendar date (e.g. 09:00 PM -> 09:00 PM)."""
    try:
        s, e = datetime.fromisoformat(summary["start"]), datetime.fromisoformat(summary["end"])
    except (KeyError, TypeError, ValueError):
        return False
    return s.date() != (e - timedelta(seconds=1)).date()


def build_pdf(summary, events, path, days=None, title="Daily Report", include_all=False):
    """Write the PDF: summary tables per gate [+ day-wise table for a range] + detail grouped by vehicle
    type (Truck / Car / ...): number plate, Entry/Exit, time, gate — same plate's Entry and Exit together."""
    from fpdf import FPDF

    os.makedirs(os.path.dirname(path), exist_ok=True)
    pdf = FPDF(orientation="P", unit="mm", format="A4")
    pdf.set_auto_page_break(auto=True, margin=14)
    pdf.add_page()
    pdf.set_font("Helvetica", "B", 18)
    pdf.set_text_color(31, 111, 235)
    pdf.cell(0, 10, f"9x Security - {title}", new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Helvetica", "", 11)
    pdf.set_text_color(60, 60, 60)
    pdf.cell(0, 7, f"Period: {_pdf_safe(period_text(summary, pdf=True))}", new_x="LMARGIN", new_y="NEXT")
    pdf.cell(0, 7, f"Generated: {datetime.now().strftime('%d-%m-%Y %I:%M %p')}", new_x="LMARGIN", new_y="NEXT")
    if summary.get("vehicles_only"):
        pdf.cell(0, 7, "Only 4-wheelers (truck / car / bus / tractor) - persons and two-wheelers are not included",
                 new_x="LMARGIN", new_y="NEXT")
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
        pdf.cell(0, 8, "No Entry/Exit recorded in this period.", new_x="LMARGIN", new_y="NEXT")

    if days:
        pdf.add_page()
        pdf.set_font("Helvetica", "B", 12)
        pdf.set_text_color(20, 20, 20)
        pdf.cell(0, 8, f"Day-wise ({len(days)} days)", new_x="LMARGIN", new_y="NEXT")
        pdf.set_font("Helvetica", "B", 9)
        pdf.set_fill_color(31, 111, 235)
        pdf.set_text_color(255, 255, 255)
        dkeys = ["truck", "car", "bus", "tractor"] + (["two_wheeler", "person"] if include_all else [])
        dnames = {"truck": "Truck", "car": "Car", "bus": "Bus", "tractor": "Tractor", "two_wheeler": "2-whl", "person": "Person"}
        dcols = [(24, "Date"), (16, "Entry"), (16, "Exit")] + [(22, f"{dnames[k]} E/X") for k in dkeys]
        for w, h in dcols:
            pdf.cell(w, 7, h, border=1, fill=True, align="C")
        pdf.ln()
        pdf.set_font("Helvetica", "", 9)
        pdf.set_text_color(30, 30, 30)
        pdf.set_fill_color(245, 247, 250)
        shade = False
        for d in days:
            t = d["total"]
            vals = [datetime.strptime(d["date"], "%Y-%m-%d").strftime("%d-%m-%Y"), str(d["entry"]), str(d["exit"])]
            vals += [f"{t[k]['Entry']} / {t[k]['Exit']}" for k in dkeys]
            for (w, _), v in zip(dcols, vals):
                pdf.cell(w, 6.5, v, border=1, fill=shade, align="C")
            pdf.ln()
            shade = not shade
        pdf.ln(4)

    if events:
        pdf.add_page()
        pdf.set_font("Helvetica", "B", 12)
        pdf.set_text_color(20, 20, 20)
        pdf.cell(0, 8, f"Detail ({len(events)} events) - number plate, Entry/Exit and time, vehicle-wise",
                 new_x="LMARGIN", new_y="NEXT")
        multi_day = (bool(days) and len(days) > 1) or _spans_days(summary)
        groups = OrderedDict((k, []) for k in ROWS)
        for ev in events:
            groups[row_key(ev.get("vehicle_type"))].append(ev)
        cols = ((60, "Number plate"), (28, "Entry / Exit"), (40, "Time"), (54, "Gate"))
        for k, evs in groups.items():
            if not evs:
                continue
            if pdf.get_y() > 250:
                pdf.add_page()
            name = ROWS[k].split(" (")[0]
            pdf.ln(3)
            pdf.set_font("Helvetica", "B", 11)
            pdf.set_text_color(31, 111, 235)
            pdf.cell(0, 8, f"{name} ({len(evs)})", new_x="LMARGIN", new_y="NEXT")
            pdf.set_font("Helvetica", "B", 9)
            pdf.set_fill_color(31, 111, 235)
            pdf.set_text_color(255, 255, 255)
            for w, h in cols:
                pdf.cell(w, 7, h, border=1, fill=True)
            pdf.ln()
            pdf.set_font("Helvetica", "", 9)
            pdf.set_text_color(30, 30, 30)
            pdf.set_fill_color(245, 247, 250)
            shade = False
            for ev in sorted(evs, key=_detail_order):
                try:
                    ts = datetime.fromisoformat(str(ev.get("timestamp", "")))
                    t = ts.strftime("%d-%m %I:%M %p" if multi_day else "%I:%M %p")
                except ValueError:
                    t = str(ev.get("time", ""))[:8]
                n = int(ev.get("count") or 1)
                plate = str(ev.get("plate") or "")
                if not plate:
                    plate = "Not detected" if k in VEHICLE_KEYS else (f"{n} together" if n > 1 else "-")
                vals = (plate[:22], str(ev.get("direction", "")), t, str(ev.get("gate") or "Gate")[:28])
                for (w, _), v in zip(cols, vals):
                    pdf.cell(w, 6.5, _pdf_safe(v), border=1, fill=shade)
                pdf.ln()
                shade = not shade
    pdf.output(path)
    return path


def build_xlsx(summary, events, path, days=None, include_all=False):
    """Excel workbook: Summary (per gate), Day-wise, Events (vehicle-wise: same plate's Entry/Exit together)."""
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.utils import get_column_letter

    os.makedirs(os.path.dirname(path), exist_ok=True)
    wb = Workbook()
    head_font, head_fill = Font(bold=True, color="FFFFFF"), PatternFill("solid", fgColor="1F6FEB")
    bold = Font(bold=True)

    def header(ws, cells):
        ws.append(cells)
        for c in ws[ws.max_row]:
            c.font, c.fill, c.alignment = head_font, head_fill, Alignment(horizontal="center")

    def autosize(ws):
        for i, col in enumerate(ws.columns, start=1):
            width = max((len(str(c.value)) for c in col if c.value is not None), default=8)
            ws.column_dimensions[get_column_letter(i)].width = min(max(10, width + 2), 48)

    ws = wb.active
    ws.title = "Summary"
    ws.append(["9x Security - Report"])
    ws["A1"].font = Font(bold=True, size=14, color="1F6FEB")
    ws.append([f"Period: {period_text(summary, pdf=True)}"])
    ws.append([f"Generated: {datetime.now().strftime('%d-%m-%Y %I:%M %p')}"])
    if summary.get("vehicles_only"):
        ws.append(["Only 4-wheelers (truck / car / bus / tractor) - persons and two-wheelers are not included"])
    ws.append([])
    header(ws, ["Gate", "Type", "Entry", "Exit", "Total"])
    blocks = ([("All gates", summary["total"])] if len(summary["gates"]) != 1 else []) + list(summary["gates"].items())
    for gate, rows in blocks:
        te = tx = 0
        for k, name in ROWS.items():
            r = rows[k]
            if not (r["Entry"] or r["Exit"]) and k not in ("truck", "car"):
                continue
            ws.append([gate, name, r["Entry"], r["Exit"], r["Entry"] + r["Exit"]])
            te, tx = te + r["Entry"], tx + r["Exit"]
        ws.append([gate, "TOTAL", te, tx, te + tx])
        for c in ws[ws.max_row]:
            c.font = bold
    autosize(ws)

    if days:
        wd = wb.create_sheet("Day-wise")
        dkeys = ["truck", "car", "bus", "tractor"] + (["two_wheeler", "person"] if include_all else []) + ["other"]
        dnames = {"truck": "Truck", "car": "Car", "bus": "Bus", "tractor": "Tractor", "two_wheeler": "2-wheeler",
                  "person": "Person", "other": "Other"}
        header(wd, ["Date", "Entry", "Exit"] + [f"{dnames[k]} {x}" for k in dkeys for x in ("Entry", "Exit")])
        for d in days:
            t = d["total"]
            wd.append([datetime.strptime(d["date"], "%Y-%m-%d").strftime("%d-%m-%Y"), d["entry"], d["exit"]]
                      + [t[k][x] for k in dkeys for x in ("Entry", "Exit")])
        autosize(wd)

    we = wb.create_sheet("Events")
    header(we, ["Type", "Plate", "Direction", "Date", "Time", "Gate", "Qty", "Plate source", "Photo"])
    order = {k: i for i, k in enumerate(ROWS)}
    for ev in sorted(events, key=lambda e: (order[row_key(e.get("vehicle_type"))],) + _detail_order(e)):
        try:
            ts = datetime.fromisoformat(str(ev.get("timestamp", "")))
            d, t = ts.strftime("%d-%m-%Y"), ts.strftime("%I:%M:%S %p")
        except ValueError:
            d, t = str(ev.get("date", "")), str(ev.get("time", ""))
        k = row_key(ev.get("vehicle_type"))
        typ = ROWS[k].split(" (")[0] if k != "other" else str(ev.get("vehicle_type", "")).title()
        plate = str(ev.get("plate") or "") or ("-" if k in PEOPLE_KEYS else "Not detected")
        we.append([typ, plate, str(ev.get("direction", "")), d, t, str(ev.get("gate") or "Gate"), int(ev.get("count") or 1),
                   str(ev.get("plate_source") or ""), str(ev.get("image_path") or "")])
    we.freeze_panes = "A2"
    autosize(we)
    wb.save(path)
    return path


def range_window(start_day, end_day):
    """Calendar days start..end inclusive -> (start_dt, end_dt_exclusive). Raises ValueError when bad."""
    s = datetime.strptime(start_day, "%Y-%m-%d")
    e = datetime.strptime(end_day, "%Y-%m-%d")
    if e < s:
        raise ValueError("'se' date 'tak' date ke baad hai")
    if (e - s).days >= 366:
        raise ValueError("ek baar me zyada se zyada 1 saal (366 din)")
    return s, e + timedelta(days=1)


def generate_range(db, start_day, end_day, fmt="pdf", include_all=False):
    """Report for whole calendar days start..end: summary, per-day breakdown and a PDF or Excel file.
    4-wheelers only unless include_all. Returns (summary, days, path)."""
    s, e = range_window(start_day, end_day)
    events = db.events_between(s.isoformat(timespec="seconds"), e.isoformat(timespec="seconds"))
    if not include_all:
        events = vehicles_only(events)
    summary = summarize(events, start_day, s, e, partial=False, vehicles=not include_all)
    summary["start_day"], summary["end_day"] = s.strftime("%Y-%m-%d"), (e - timedelta(days=1)).strftime("%Y-%m-%d")
    by_day = {}
    for ev in events:
        by_day.setdefault(str(ev.get("date") or str(ev.get("timestamp", ""))[:10]), []).append(ev)
    days = []
    d = s
    while d < e:
        key = d.strftime("%Y-%m-%d")
        ds = summarize(by_day.get(key, []), key)
        days.append({"date": key, "entry": ds["entry"], "exit": ds["exit"], "events": ds["events"], "total": ds["total"]})
        d += timedelta(days=1)
    name = f"9x_Report_{summary['start_day']}_to_{summary['end_day']}" + ("_all" if include_all else "")
    if fmt == "xlsx":
        path = build_xlsx(summary, events, os.path.join(REPORT_DIR, name + ".xlsx"), days=days, include_all=include_all)
    else:
        path = build_pdf(summary, events, os.path.join(REPORT_DIR, name + ".pdf"), days=days,
                         title="Report" if len(days) > 1 else "Daily Report", include_all=include_all)
    return summary, days, path


def report_path(day):
    return os.path.join(REPORT_DIR, f"9x_Report_{day}.pdf")


def generate(db, day, cfg, now=None):
    """Summary + PDF for the report of `day` (24 h ending at the report time; 'abhi tak' when that
    time is still ahead). 4-wheelers only. Returns (summary, pdf_path, caption_text)."""
    now = now or datetime.now()
    start, end, partial = window_for(cfg, day, now)
    events = vehicles_only(db.events_between(start.isoformat(timespec="seconds"), end.isoformat(timespec="seconds")))
    summary = summarize(events, day, start, end, partial, vehicles=True)
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


__all__ = ["generate", "generate_range", "summarize", "caption", "build_pdf", "build_xlsx", "due_days", "mark_baseline",
           "next_run", "report_path", "window_for", "parse_time", "period_text", "vehicles_only", "is_vehicle", "REPORT_DIR"]
