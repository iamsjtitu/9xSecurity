"""9x Security - Local SQLite storage for vehicle events."""
import sqlite3
import threading
from datetime import datetime

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
            self.conn.commit()

    def add_event(self, vehicle_type, direction, plate, image_path, ts=None):
        ts = ts or datetime.now()
        with self._lock:
            cur = self.conn.execute(
                """INSERT INTO events
                   (timestamp, date, time, vehicle_type, direction, plate, image_path)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    ts.isoformat(timespec="seconds"),
                    ts.strftime("%Y-%m-%d"),
                    ts.strftime("%H:%M:%S"),
                    vehicle_type,
                    direction,
                    plate or "",
                    image_path,
                ),
            )
            self.conn.commit()
            return cur.lastrowid

    def get_events(self, date_filter=None, direction_filter=None, limit=500):
        q = "SELECT * FROM events"
        clauses, params = [], []
        if date_filter:
            clauses.append("date = ?")
            params.append(date_filter)
        if direction_filter and direction_filter != "All":
            clauses.append("direction = ?")
            params.append(direction_filter)
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

    def close(self):
        try:
            self.conn.close()
        except Exception:
            pass
