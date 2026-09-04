"""9x Security - Local SQLite storage for vehicle events."""
import sqlite3
import threading
from datetime import datetime, timedelta

import config


class EventDB:
    def __init__(self, db_path=None):
        self.db_path = db_path or config.DB_PATH
        self._lock = threading.Lock()
        self.conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self._init()

    def _init(self):
        with self._lock:
            self.conn.execute(
                """
                CREATE TABLE IF NOT EXISTS events (
                    id           INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp    TEXT NOT NULL,
                    date         TEXT NOT NULL,
                    time         TEXT NOT NULL,
                    vehicle_type TEXT NOT NULL,
                    direction    TEXT NOT NULL,
                    plate        TEXT,
                    image_path   TEXT NOT NULL
                )
                """
            )
            self.conn.execute(
                """
                CREATE TABLE IF NOT EXISTS outbox (
                    id           INTEGER PRIMARY KEY AUTOINCREMENT,
                    created_at   TEXT NOT NULL,
                    recipient    TEXT NOT NULL,
                    caption      TEXT NOT NULL,
                    image_path   TEXT,
                    attempts     INTEGER NOT NULL DEFAULT 0,
                    last_error   TEXT,
                    last_attempt TEXT
                )
                """
            )
            self.conn.commit()
            cols = {r["name"] for r in self.conn.execute("PRAGMA table_info(events)").fetchall()}
            if "plate_status" not in cols:  # '' | 'pending' (OCR running) | 'done'
                self.conn.execute("ALTER TABLE events ADD COLUMN plate_status TEXT NOT NULL DEFAULT ''")
            if "plate_source" not in cols:  # '' | 'ocr' | 'manual'
                self.conn.execute("ALTER TABLE events ADD COLUMN plate_source TEXT NOT NULL DEFAULT ''")
            self.conn.commit()

    def add_event(self, vehicle_type, direction, plate, image_path, ts=None, plate_status=""):
        ts = ts or datetime.now()
        with self._lock:
            cur = self.conn.execute(
                """INSERT INTO events
                   (timestamp, date, time, vehicle_type, direction, plate, image_path, plate_status, plate_source)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    ts.isoformat(timespec="seconds"),
                    ts.strftime("%Y-%m-%d"),
                    ts.strftime("%H:%M:%S"),
                    vehicle_type,
                    direction,
                    plate or "",
                    image_path,
                    plate_status,
                    "ocr" if plate else "",
                ),
            )
            self.conn.commit()
            return cur.lastrowid

    def get_events(self, date_filter=None, direction_filter=None, plate_filter=None, limit=500):
        q = "SELECT * FROM events"
        clauses, params = [], []
        if date_filter:
            clauses.append("date = ?")
            params.append(date_filter)
        if direction_filter and direction_filter != "All":
            clauses.append("direction = ?")
            params.append(direction_filter)
        if plate_filter:
            clauses.append("plate LIKE ?")
            params.append(f"%{plate_filter}%")
        if clauses:
            q += " WHERE " + " AND ".join(clauses)
        q += " ORDER BY id DESC LIMIT ?"
        params.append(limit)
        with self._lock:
            rows = self.conn.execute(q, params).fetchall()
        return [dict(r) for r in rows]

    def counts_today(self):
        today = datetime.now().strftime("%Y-%m-%d")
        with self._lock:
            rows = self.conn.execute(
                "SELECT direction, COUNT(*) c FROM events WHERE date=? GROUP BY direction",
                (today,),
            ).fetchall()
        out = {"Entry": 0, "Exit": 0}
        for r in rows:
            out[r["direction"]] = r["c"]
        return out

    def purge_older_than(self, days):
        """Delete events older than `days`. Returns their snapshot paths."""
        cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
        with self._lock:
            rows = self.conn.execute(
                "SELECT image_path FROM events WHERE date < ?", (cutoff,)
            ).fetchall()
            self.conn.execute("DELETE FROM events WHERE date < ?", (cutoff,))
            self.conn.commit()
        return [r["image_path"] for r in rows]

    def update_event_plate(self, eid, plate, source="ocr", status="done"):
        """Set the plate of an event (OCR result or manual correction). Returns the row or None."""
        plate = plate or ""
        with self._lock:
            cur = self.conn.execute(
                "UPDATE events SET plate=?, plate_source=?, plate_status=? WHERE id=?",
                (plate, source if plate else "", status, eid),
            )
            self.conn.commit()
            if cur.rowcount == 0:
                return None
            row = self.conn.execute("SELECT * FROM events WHERE id=?", (eid,)).fetchone()
        return dict(row) if row else None

    def stats(self):
        with self._lock:
            total = self.conn.execute("SELECT COUNT(*) c FROM events").fetchone()["c"]
            last = self.conn.execute("SELECT * FROM events ORDER BY id DESC LIMIT 1").fetchone()
        return {"total": int(total), "last": dict(last) if last else None}

    # ---- WhatsApp outbox (durable offline queue) ---------------------------
    def outbox_add(self, recipient, caption, image_path=""):
        with self._lock:
            cur = self.conn.execute(
                "INSERT INTO outbox (created_at, recipient, caption, image_path) VALUES (?, ?, ?, ?)",
                (datetime.now().isoformat(timespec="seconds"), recipient, caption, image_path or ""),
            )
            self.conn.commit()
            return cur.lastrowid

    def outbox_pending(self, limit=25):
        with self._lock:
            rows = self.conn.execute(
                "SELECT * FROM outbox ORDER BY id ASC LIMIT ?", (limit,)
            ).fetchall()
        return [dict(r) for r in rows]

    def outbox_delete(self, oid):
        with self._lock:
            self.conn.execute("DELETE FROM outbox WHERE id=?", (oid,))
            self.conn.commit()

    def outbox_mark_failed(self, oid, error):
        with self._lock:
            self.conn.execute(
                "UPDATE outbox SET attempts=attempts+1, last_error=?, last_attempt=? WHERE id=?",
                (str(error)[:300], datetime.now().isoformat(timespec="seconds"), oid),
            )
            self.conn.commit()

    def outbox_count(self):
        with self._lock:
            r = self.conn.execute("SELECT COUNT(*) c FROM outbox").fetchone()
        return int(r["c"])

    def outbox_purge_older_than(self, days):
        cutoff = (datetime.now() - timedelta(days=days)).isoformat(timespec="seconds")
        with self._lock:
            cur = self.conn.execute("DELETE FROM outbox WHERE created_at < ?", (cutoff,))
            self.conn.commit()
        return cur.rowcount

    def close(self):
        try:
            self.conn.close()
        except Exception:
            pass
