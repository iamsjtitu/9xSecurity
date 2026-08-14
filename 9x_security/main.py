"""9x Security - Desktop GUI (PyQt5).

Run:  python main.py
Live RTSP feed with vehicle detection, entry/exit line crossing,
snapshot logging (date+time+type+direction+plate) and an event gallery.
"""
import os
import sys
from datetime import datetime

import cv2
from PyQt5 import QtCore, QtGui, QtWidgets

import config
from database import EventDB
from engine import SecurityEngine
import auth


BASE_STYLE = """
    QMainWindow, QDialog { background:#0b0f14; }
    QWidget { color:#e6edf3; font-family:'Segoe UI'; font-size:13px; }
    QLineEdit, QPlainTextEdit { background:#111826; border:1px solid #263447; border-radius:6px; padding:7px; }
    QPushButton { background:#1f6feb; border:none; border-radius:6px; padding:8px 14px; color:white; font-weight:600; }
    QPushButton:hover { background:#2a7bff; }
    QPushButton#danger { background:#b42318; }
    QPushButton#ghost { background:#1b2430; border:1px solid #2b3b4e; }
    QGroupBox { border:1px solid #1f2a37; border-radius:8px; margin-top:10px; padding-top:8px; }
    QGroupBox::title { subcontrol-origin: margin; left:10px; color:#7d8ea1; }
    QTableWidget { background:#0e141c; gridline-color:#1a2430; border:1px solid #1f2a37; border-radius:8px; }
    QHeaderView::section { background:#111826; color:#9fb0c3; padding:6px; border:none; }
    QComboBox, QDateEdit { background:#111826; border:1px solid #263447; border-radius:6px; padding:5px; }
    QCheckBox { padding:2px; }
    QTabBar::tab { background:#111826; color:#9fb0c3; padding:8px 16px; border:1px solid #1f2a37; }
    QTabBar::tab:selected { background:#1f6feb; color:white; }
"""


# ---------------------------------------------------------------------------
class VideoLabel(QtWidgets.QLabel):
    """Displays frames and lets the user draw the detection line by 2 clicks."""

    line_drawn = QtCore.pyqtSignal(float, float, float, float)  # normalized

    def __init__(self):
        super().__init__()
        self.setMinimumSize(config.DISPLAY_WIDTH, config.DISPLAY_HEIGHT)
        self.setAlignment(QtCore.Qt.AlignCenter)
        self.setStyleSheet("background:#0b0f14; border:1px solid #1f2a37;")
        self.draw_mode = False
        self._first = None

    def mousePressEvent(self, ev):
        if not self.draw_mode or self.pixmap() is None:
            return
        w, h = self.width(), self.height()
        nx, ny = ev.x() / max(1, w), ev.y() / max(1, h)
        nx, ny = min(max(nx, 0.0), 1.0), min(max(ny, 0.0), 1.0)
        if self._first is None:
            self._first = (nx, ny)
        else:
            x1, y1 = self._first
            self.line_drawn.emit(x1, y1, nx, ny)
            self._first = None
            self.draw_mode = False


# ---------------------------------------------------------------------------
class VideoThread(QtCore.QThread):
    frame_ready = QtCore.pyqtSignal(object)
    event_logged = QtCore.pyqtSignal(dict)
    status = QtCore.pyqtSignal(str)

    def __init__(self, cfg):
        super().__init__()
        self.cfg = cfg
        self._running = False
        self.engine = None

    def run(self):
        self._running = True
        self.status.emit("Loading AI model...")
        try:
            self.engine = SecurityEngine(cfg=self.cfg)
            self.engine.on_event = lambda e: self.event_logged.emit(e)
        except Exception as e:
            self.status.emit(f"Model load failed: {e}")
            return

        url = self.cfg.get("rtsp_url", "").strip()
        source = url if url else 0  # fallback to webcam for local testing
        self.status.emit(f"Connecting to source...")
        cap = cv2.VideoCapture(source)
        if not cap.isOpened():
            self.status.emit("ERROR: Could not open camera / RTSP stream.")
            return
        self.status.emit("Connected - monitoring live feed")

        fail = 0
        while self._running:
            ok, frame = cap.read()
            if not ok:
                fail += 1
                if fail > 50:
                    self.status.emit("Stream lost - reconnecting...")
                    cap.release()
                    cap = cv2.VideoCapture(source)
                    fail = 0
                self.msleep(20)
                continue
            fail = 0
            small = cv2.resize(frame, (config.DISPLAY_WIDTH, config.DISPLAY_HEIGHT))
            annotated, _ = self.engine.process_frame(small, original=frame)
            self.frame_ready.emit(annotated)
            self.msleep(10)
        cap.release()

    def stop(self):
        self._running = False
        self.wait(3000)


# ---------------------------------------------------------------------------
class MainWindow(QtWidgets.QMainWindow):
    def __init__(self):
        super().__init__()
        self.cfg = config.load_config()
        self.db = EventDB()
        self.thread = None
        self.setWindowTitle(f"{config.APP_NAME}  -  Gate Vehicle Monitor")
        self.resize(1400, 820)
        self._build_ui()
        self._load_events()
        self._refresh_counts()

    # ---- UI ---------------------------------------------------------------
    def _build_ui(self):
        self.setStyleSheet(BASE_STYLE)
        central = QtWidgets.QWidget()
        self.setCentralWidget(central)
        root = QtWidgets.QHBoxLayout(central)
        root.setContentsMargins(14, 14, 14, 14)
        root.setSpacing(14)

        # ----- LEFT: video + controls -----
        left = QtWidgets.QVBoxLayout()
        left.setSpacing(10)

        header = QtWidgets.QLabel("9X SECURITY")
        header.setStyleSheet("font-size:22px; font-weight:800; color:#1f6feb; letter-spacing:2px;")
        left.addWidget(header)

        conn = QtWidgets.QHBoxLayout()
        self.url_edit = QtWidgets.QLineEdit(self.cfg.get("rtsp_url", ""))
        self.url_edit.setPlaceholderText("rtsp://user:pass@192.168.1.10:554/stream")
        self.url_edit.setObjectName("rtsp-url-input")
        self.btn_connect = QtWidgets.QPushButton("Connect")
        self.btn_connect.setObjectName("connect-btn")
        self.btn_connect.clicked.connect(self.toggle_connect)
        conn.addWidget(self.url_edit, 1)
        conn.addWidget(self.btn_connect)
        left.addLayout(conn)

        self.video = VideoLabel()
        self.video.line_drawn.connect(self.on_line_drawn)
        left.addWidget(self.video, 1)

        ctrl = QtWidgets.QHBoxLayout()
        self.btn_line = QtWidgets.QPushButton("Draw Detection Line")
        self.btn_line.setObjectName("ghost")
        self.btn_line.clicked.connect(self.enable_draw)
        self.btn_swap = QtWidgets.QPushButton("Swap Entry/Exit")
        self.btn_swap.setObjectName("ghost")
        self.btn_swap.clicked.connect(self.swap_direction)
        self.btn_folder = QtWidgets.QPushButton("Open Snapshots")
        self.btn_folder.setObjectName("ghost")
        self.btn_folder.clicked.connect(self.open_folder)
        self.btn_settings = QtWidgets.QPushButton("⚙ Settings")
        self.btn_settings.setObjectName("settings-btn")
        self.btn_settings.clicked.connect(self.open_settings)
        ctrl.addWidget(self.btn_line)
        ctrl.addWidget(self.btn_swap)
        ctrl.addWidget(self.btn_folder)
        ctrl.addWidget(self.btn_settings)
        ctrl.addStretch(1)
        left.addLayout(ctrl)

        opts = QtWidgets.QHBoxLayout()
        self.chk_plate = QtWidgets.QCheckBox("Number Plate (OCR)")
        self.chk_plate.setChecked(self.cfg.get("enable_plate", True))
        self.chk_plate.stateChanged.connect(self._save_opts)
        self.chk_car = QtWidgets.QCheckBox("Car")
        self.chk_truck = QtWidgets.QCheckBox("Truck")
        self.chk_bus = QtWidgets.QCheckBox("Bus")
        vc = self.cfg.get("vehicle_classes", [])
        self.chk_car.setChecked("car" in vc)
        self.chk_truck.setChecked("truck" in vc)
        self.chk_bus.setChecked("bus" in vc)
        for c in (self.chk_car, self.chk_truck, self.chk_bus):
            c.stateChanged.connect(self._save_opts)
        opts.addWidget(self.chk_plate)
        opts.addSpacing(20)
        opts.addWidget(QtWidgets.QLabel("Detect:"))
        opts.addWidget(self.chk_car)
        opts.addWidget(self.chk_truck)
        opts.addWidget(self.chk_bus)
        opts.addStretch(1)
        left.addLayout(opts)

        self.status = QtWidgets.QLabel("Idle. Enter RTSP URL and press Connect.")
        self.status.setStyleSheet("color:#7d8ea1; padding:4px;")
        left.addWidget(self.status)

        # ----- RIGHT: stats + events -----
        right = QtWidgets.QVBoxLayout()
        right.setSpacing(10)

        stats = QtWidgets.QHBoxLayout()
        self.card_entry = self._stat_card("ENTRIES TODAY", "0", "#2ea043")
        self.card_exit = self._stat_card("EXITS TODAY", "0", "#d29922")
        stats.addWidget(self.card_entry)
        stats.addWidget(self.card_exit)
        right.addLayout(stats)

        filt = QtWidgets.QHBoxLayout()
        self.date_edit = QtWidgets.QDateEdit(QtCore.QDate.currentDate())
        self.date_edit.setCalendarPopup(True)
        self.dir_combo = QtWidgets.QComboBox()
        self.dir_combo.addItems(["All", "Entry", "Exit"])
        btn_filter = QtWidgets.QPushButton("Filter")
        btn_filter.clicked.connect(self._load_events)
        btn_all = QtWidgets.QPushButton("Show All")
        btn_all.setObjectName("ghost")
        btn_all.clicked.connect(self._show_all)
        filt.addWidget(QtWidgets.QLabel("Date:"))
        filt.addWidget(self.date_edit)
        filt.addWidget(self.dir_combo)
        filt.addWidget(btn_filter)
        filt.addWidget(btn_all)
        filt.addStretch(1)
        right.addLayout(filt)

        self.table = QtWidgets.QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(["Snapshot", "Date & Time", "Type", "Direction", "Plate"])
        self.table.verticalHeader().setDefaultSectionSize(70)
        self.table.setColumnWidth(0, 120)
        self.table.horizontalHeader().setSectionResizeMode(1, QtWidgets.QHeaderView.Stretch)
        self.table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        self.table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        self.table.cellDoubleClicked.connect(self._open_snapshot)
        right.addWidget(self.table, 1)

        root.addLayout(left, 3)
        root.addLayout(right, 2)

    def _stat_card(self, title, value, color):
        box = QtWidgets.QGroupBox()
        lay = QtWidgets.QVBoxLayout(box)
        t = QtWidgets.QLabel(title)
        t.setStyleSheet("color:#7d8ea1; font-size:11px; font-weight:600;")
        v = QtWidgets.QLabel(value)
        v.setObjectName("stat-value")
        v.setStyleSheet(f"color:{color}; font-size:30px; font-weight:800;")
        lay.addWidget(t)
        lay.addWidget(v)
        box._value = v
        return box

    # ---- actions ----------------------------------------------------------
    def _current_classes(self):
        vc = []
        if self.chk_car.isChecked():
            vc.append("car")
        if self.chk_truck.isChecked():
            vc.append("truck")
        if self.chk_bus.isChecked():
            vc.append("bus")
        return vc or ["car", "truck", "bus"]

    def _save_opts(self):
        self.cfg["enable_plate"] = self.chk_plate.isChecked()
        self.cfg["vehicle_classes"] = self._current_classes()
        config.save_config(self.cfg)

    def toggle_connect(self):
        if self.thread and self.thread.isRunning():
            self.thread.stop()
            self.thread = None
            self.btn_connect.setText("Connect")
            self.status.setText("Disconnected.")
            return
        self.cfg["rtsp_url"] = self.url_edit.text().strip()
        self._save_opts()
        config.save_config(self.cfg)
        self.thread = VideoThread(self.cfg)
        self.thread.frame_ready.connect(self.on_frame)
        self.thread.event_logged.connect(self.on_event)
        self.thread.status.connect(self.status.setText)
        self.thread.start()
        self.btn_connect.setText("Disconnect")

    def enable_draw(self):
        self.video.draw_mode = True
        self.status.setText("Draw line: click START point, then END point on the video.")

    def on_line_drawn(self, x1, y1, x2, y2):
        self.cfg["line"] = {"x1": x1, "y1": y1, "x2": x2, "y2": y2}
        config.save_config(self.cfg)
        if self.thread and self.thread.engine:
            self.thread.engine.cfg["line"] = self.cfg["line"]
        self.status.setText("Detection line updated.")

    def swap_direction(self):
        self.cfg["entry_direction"] = "neg" if self.cfg.get("entry_direction") == "pos" else "pos"
        config.save_config(self.cfg)
        if self.thread and self.thread.engine:
            self.thread.engine.cfg["entry_direction"] = self.cfg["entry_direction"]
        self.status.setText(f"Entry/Exit direction swapped ({self.cfg['entry_direction']}).")

    def open_folder(self):
        path = config.SNAPSHOT_DIR
        if sys.platform.startswith("win"):
            os.startfile(path)  # noqa
        elif sys.platform == "darwin":
            os.system(f'open "{path}"')
        else:
            os.system(f'xdg-open "{path}"')

    def open_settings(self):
        dlg = SettingsDialog(self.cfg, self)
        if dlg.exec_() == QtWidgets.QDialog.Accepted:
            self.cfg = config.load_config()
            if self.thread and getattr(self.thread, "engine", None):
                self.thread.engine.cfg = self.cfg
                self.thread.engine.notifier.update(self.cfg)
            wa = "ON" if self.cfg.get("wa_enabled") else "OFF"
            self.status.setText(f"Settings saved. WhatsApp alerts: {wa}")

    def on_frame(self, frame):
        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        h, w, ch = rgb.shape
        img = QtGui.QImage(rgb.data, w, h, ch * w, QtGui.QImage.Format_RGB888)
        self.video.setPixmap(
            QtGui.QPixmap.fromImage(img).scaled(
                self.video.width(), self.video.height(),
                QtCore.Qt.KeepAspectRatio, QtCore.Qt.SmoothTransformation,
            )
        )

    def on_event(self, ev):
        self._insert_row(ev, top=True)
        self._refresh_counts()

    # ---- events table -----------------------------------------------------
    def _show_all(self):
        rows = self.db.get_events()
        self.table.setRowCount(0)
        for r in rows:
            self._insert_row(r)

    def _load_events(self):
        date = self.date_edit.date().toString("yyyy-MM-dd")
        direction = self.dir_combo.currentText()
        rows = self.db.get_events(date_filter=date, direction_filter=direction)
        self.table.setRowCount(0)
        for r in rows:
            self._insert_row(r)

    def _insert_row(self, ev, top=False):
        row = 0 if top else self.table.rowCount()
        self.table.insertRow(row)

        thumb = QtWidgets.QLabel()
        thumb.setAlignment(QtCore.Qt.AlignCenter)
        img_path = ev.get("image_path", "")
        if img_path and os.path.exists(img_path):
            pm = QtGui.QPixmap(img_path).scaled(110, 62, QtCore.Qt.KeepAspectRatio, QtCore.Qt.SmoothTransformation)
            thumb.setPixmap(pm)
        else:
            thumb.setText("no image")
        self.table.setCellWidget(row, 0, thumb)

        when = ev.get("timestamp", "")
        if "date" in ev and "time" in ev:
            when = f"{ev['date']} {ev['time']}"
        else:
            when = when.replace("T", " ")
        self._set(row, 1, when)
        self._set(row, 2, str(ev.get("vehicle_type", "")).upper())
        d = ev.get("direction", "")
        item = QtWidgets.QTableWidgetItem(d)
        item.setForeground(QtGui.QColor("#2ea043" if d == "Entry" else "#d29922"))
        self.table.setItem(row, 3, item)
        self._set(row, 4, ev.get("plate", "") or "-")
        self.table.setItem(row, 1, self.table.item(row, 1))

    def _set(self, row, col, text):
        self.table.setItem(row, col, QtWidgets.QTableWidgetItem(text))

    def _open_snapshot(self, row, _col):
        rows = self.db.get_events(limit=1000)
        # best-effort: match by displayed time
        when = self.table.item(row, 1).text() if self.table.item(row, 1) else ""
        for r in rows:
            if f"{r['date']} {r['time']}" == when and os.path.exists(r["image_path"]):
                self._preview(r["image_path"])
                return

    def _preview(self, path):
        dlg = QtWidgets.QDialog(self)
        dlg.setWindowTitle("Snapshot")
        lay = QtWidgets.QVBoxLayout(dlg)
        lbl = QtWidgets.QLabel()
        lbl.setPixmap(QtGui.QPixmap(path).scaled(900, 520, QtCore.Qt.KeepAspectRatio, QtCore.Qt.SmoothTransformation))
        lay.addWidget(lbl)
        dlg.exec_()

    def _refresh_counts(self):
        c = self.db.counts_today()
        self.card_entry._value.setText(str(c.get("Entry", 0)))
        self.card_exit._value.setText(str(c.get("Exit", 0)))

    def closeEvent(self, ev):
        if self.thread and self.thread.isRunning():
            self.thread.stop()
        self.db.close()
        super().closeEvent(ev)


class LoginDialog(QtWidgets.QDialog):
    def __init__(self, cfg):
        super().__init__()
        self.cfg = cfg
        self.setWindowTitle("9x Security - Login")
        self.setFixedWidth(360)
        self.setStyleSheet(BASE_STYLE)
        lay = QtWidgets.QVBoxLayout(self)
        lay.setContentsMargins(28, 28, 28, 28)
        lay.setSpacing(12)

        title = QtWidgets.QLabel("9X SECURITY")
        title.setStyleSheet("font-size:24px;font-weight:800;color:#1f6feb;letter-spacing:3px;")
        title.setAlignment(QtCore.Qt.AlignCenter)
        sub = QtWidgets.QLabel("Sign in to continue")
        sub.setStyleSheet("color:#7d8ea1;")
        sub.setAlignment(QtCore.Qt.AlignCenter)
        lay.addWidget(title)
        lay.addWidget(sub)

        self.user = QtWidgets.QLineEdit(cfg.get("auth_user", "admin"))
        self.user.setPlaceholderText("Username")
        self.user.setObjectName("login-username")
        self.pw = QtWidgets.QLineEdit()
        self.pw.setPlaceholderText("Password")
        self.pw.setEchoMode(QtWidgets.QLineEdit.Password)
        self.pw.setObjectName("login-password")
        self.pw.returnPressed.connect(self._try)
        lay.addWidget(self.user)
        lay.addWidget(self.pw)

        self.msg = QtWidgets.QLabel("")
        self.msg.setStyleSheet("color:#f85149;")
        lay.addWidget(self.msg)

        btn = QtWidgets.QPushButton("Login")
        btn.setObjectName("login-btn")
        btn.clicked.connect(self._try)
        lay.addWidget(btn)

    def _try(self):
        u = self.user.text().strip()
        p = self.pw.text()
        if u == self.cfg.get("auth_user", "admin") and auth.verify_password(
            p, self.cfg.get("auth_salt", ""), self.cfg.get("auth_hash", "")
        ):
            self.accept()
        else:
            self.msg.setText("Galat username ya password.")


class SettingsDialog(QtWidgets.QDialog):
    def __init__(self, cfg, parent=None):
        super().__init__(parent)
        self.cfg = cfg
        self.setWindowTitle("Settings")
        self.setMinimumWidth(480)
        self.setStyleSheet(BASE_STYLE)
        lay = QtWidgets.QVBoxLayout(self)
        tabs = QtWidgets.QTabWidget()
        lay.addWidget(tabs)

        # ---- WhatsApp tab ----
        wa = QtWidgets.QWidget()
        wf = QtWidgets.QFormLayout(wa)
        self.wa_enabled = QtWidgets.QCheckBox("Enable WhatsApp alerts on Entry/Exit")
        self.wa_enabled.setChecked(cfg.get("wa_enabled", False))
        self.wa_enabled.setObjectName("wa-enabled-check")
        self.wa_base = QtWidgets.QLineEdit(cfg.get("wa_base_url", "https://wa.9x.design"))
        self.wa_key = QtWidgets.QLineEdit(cfg.get("wa_api_key", ""))
        self.wa_key.setEchoMode(QtWidgets.QLineEdit.Password)
        self.wa_key.setPlaceholderText("wa9x_...  (dashboard se apni API key paste karein)")
        self.wa_key.setObjectName("wa-api-key-input")
        self.wa_recipients = QtWidgets.QPlainTextEdit("\n".join(cfg.get("wa_recipients", [])))
        self.wa_recipients.setPlaceholderText("Ek line me ek number:\n919876543210\n919812345678")
        self.wa_recipients.setFixedHeight(90)
        self.wa_recipients.setObjectName("wa-recipients-input")
        self.wa_img = QtWidgets.QCheckBox("Send photo (image). Uncheck = text-only alert")
        self.wa_img.setChecked(cfg.get("wa_send_image", True))
        wf.addRow(self.wa_enabled)
        wf.addRow("API Base URL", self.wa_base)
        wf.addRow("API Key (Bearer)", self.wa_key)
        wf.addRow("Recipients", self.wa_recipients)
        wf.addRow(self.wa_img)
        self.wa_test_btn = QtWidgets.QPushButton("Send Test Message")
        self.wa_test_btn.setObjectName("wa-test-btn")
        self.wa_test_btn.clicked.connect(self._send_test)
        wf.addRow(self.wa_test_btn)
        tabs.addTab(wa, "WhatsApp")

        # ---- Account tab (wa.9x.design credentials) ----
        ac = QtWidgets.QWidget()
        af = QtWidgets.QFormLayout(ac)
        self.acc_email = QtWidgets.QLineEdit(cfg.get("wa_account_email", ""))
        self.acc_email.setObjectName("wa-account-email")
        self.acc_pass = QtWidgets.QLineEdit(cfg.get("wa_account_password", ""))
        self.acc_pass.setEchoMode(QtWidgets.QLineEdit.Password)
        self.acc_pass.setObjectName("wa-account-password")
        af.addRow("wa.9x.design Email", self.acc_email)
        af.addRow("wa.9x.design Password", self.acc_pass)
        note = QtWidgets.QLabel("Ye credentials sirf reference ke liye store hote hain.\nSending API key (Bearer) se hota hai (WhatsApp tab).")
        note.setStyleSheet("color:#7d8ea1;")
        af.addRow(note)
        tabs.addTab(ac, "Account")

        # ---- Security tab (app login) ----
        sec = QtWidgets.QWidget()
        sf = QtWidgets.QFormLayout(sec)
        self.sec_user = QtWidgets.QLineEdit(cfg.get("auth_user", "admin"))
        self.sec_user.setObjectName("sec-username")
        self.sec_new = QtWidgets.QLineEdit()
        self.sec_new.setEchoMode(QtWidgets.QLineEdit.Password)
        self.sec_new.setPlaceholderText("Khaali chhodein = password na badlein")
        self.sec_new.setObjectName("sec-new-password")
        self.sec_new2 = QtWidgets.QLineEdit()
        self.sec_new2.setEchoMode(QtWidgets.QLineEdit.Password)
        self.sec_new2.setObjectName("sec-confirm-password")
        sf.addRow("Login Username", self.sec_user)
        sf.addRow("New Password", self.sec_new)
        sf.addRow("Confirm Password", self.sec_new2)
        tabs.addTab(sec, "Login / Security")

        # ---- Updates tab ----
        import updater as _upd

        up = QtWidgets.QWidget()
        uf = QtWidgets.QFormLayout(up)
        self.ver_label = QtWidgets.QLabel(f"Current version:  v{_upd.APP_VERSION}")
        self.ver_label.setStyleSheet("font-weight:700;")
        self.gh_repo = QtWidgets.QLineEdit(cfg.get("github_repo", ""))
        self.gh_repo.setPlaceholderText("owner/repository  (jaise yourname/9x-security)")
        self.gh_repo.setObjectName("github-repo-input")
        self.upd_btn = QtWidgets.QPushButton("Check for Updates")
        self.upd_btn.setObjectName("check-update-btn")
        self.upd_btn.clicked.connect(self._check_update)
        uf.addRow(self.ver_label)
        uf.addRow("GitHub Repo", self.gh_repo)
        uf.addRow(self.upd_btn)
        unote = QtWidgets.QLabel(
            "GitHub Release me nayi version publish hone par yahan se seedha\n"
            "download + install ho jaayegi (.exe mode me auto-replace)."
        )
        unote.setStyleSheet("color:#7d8ea1;")
        uf.addRow(unote)
        tabs.addTab(up, "Updates")

        btns = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.Save | QtWidgets.QDialogButtonBox.Cancel
        )
        btns.button(QtWidgets.QDialogButtonBox.Save).setObjectName("settings-save-btn")
        btns.accepted.connect(self._save)
        btns.rejected.connect(self.reject)
        lay.addWidget(btns)

    def _save(self):
        c = self.cfg
        c["wa_enabled"] = self.wa_enabled.isChecked()
        c["wa_base_url"] = self.wa_base.text().strip() or "https://wa.9x.design"
        c["wa_api_key"] = self.wa_key.text().strip()
        c["wa_recipients"] = [
            x.strip() for x in self.wa_recipients.toPlainText().splitlines() if x.strip()
        ]
        c["wa_send_image"] = self.wa_img.isChecked()
        c["wa_account_email"] = self.acc_email.text().strip()
        c["wa_account_password"] = self.acc_pass.text()
        c["auth_user"] = self.sec_user.text().strip() or "admin"
        c["github_repo"] = self.gh_repo.text().strip()
        np1, np2 = self.sec_new.text(), self.sec_new2.text()
        if np1:
            if np1 != np2:
                QtWidgets.QMessageBox.warning(self, "Error", "Passwords match nahi kar rahe.")
                return
            salt, h = auth.hash_password(np1)
            c["auth_salt"], c["auth_hash"] = salt, h
        config.save_config(c)
        self.accept()

    def _send_test(self):
        from whatsapp import WhatsAppNotifier

        cfg = {
            "wa_enabled": True,
            "wa_base_url": self.wa_base.text().strip() or "https://wa.9x.design",
            "wa_api_key": self.wa_key.text().strip(),
            "wa_recipients": [
                x.strip() for x in self.wa_recipients.toPlainText().splitlines() if x.strip()
            ],
            "wa_send_image": self.wa_img.isChecked(),
        }
        if not cfg["wa_api_key"] or not cfg["wa_recipients"]:
            QtWidgets.QMessageBox.warning(
                self, "Info missing", "X-API-Key aur kam se kam ek recipient number daalein."
            )
            return
        QtWidgets.QApplication.setOverrideCursor(QtCore.Qt.WaitCursor)
        try:
            ok, detail = WhatsAppNotifier(cfg).test_connection()
        finally:
            QtWidgets.QApplication.restoreOverrideCursor()
        if ok:
            QtWidgets.QMessageBox.information(
                self, "Success", "Test bhej diya! WhatsApp check karein.\n\n" + detail
            )
        else:
            QtWidgets.QMessageBox.critical(self, "Failed", "Test fail:\n\n" + detail)

    def _check_update(self):
        import os
        import tempfile

        import updater

        repo = self.gh_repo.text().strip()
        if not repo or "/" not in repo:
            QtWidgets.QMessageBox.warning(
                self, "Repo needed", "GitHub repo 'owner/name' format me daalein."
            )
            return
        QtWidgets.QApplication.setOverrideCursor(QtCore.Qt.WaitCursor)
        try:
            tag, asset, url = updater.check_latest(repo)
        except Exception as e:
            QtWidgets.QApplication.restoreOverrideCursor()
            QtWidgets.QMessageBox.critical(self, "Error", f"Update check fail:\n{e}")
            return
        QtWidgets.QApplication.restoreOverrideCursor()

        if not tag:
            QtWidgets.QMessageBox.information(self, "No release", "Koi release nahi mila.")
            return
        if not updater.is_newer(tag):
            QtWidgets.QMessageBox.information(
                self, "Up to date", f"Aap latest version par hain (v{updater.APP_VERSION})."
            )
            return
        if not asset:
            QtWidgets.QMessageBox.information(
                self, "Update available",
                f"Nayi version v{tag} hai par .exe asset nahi mila.\nRelease page:\n{url}",
            )
            return
        if QtWidgets.QMessageBox.question(
            self, "Update available",
            f"Nayi version v{tag} available hai. Abhi download + install karein?",
        ) != QtWidgets.QMessageBox.Yes:
            return

        dest = os.path.join(tempfile.gettempdir(), "9xSecurity_new.exe")
        QtWidgets.QApplication.setOverrideCursor(QtCore.Qt.WaitCursor)
        try:
            updater.download(asset, dest)
        except Exception as e:
            QtWidgets.QApplication.restoreOverrideCursor()
            QtWidgets.QMessageBox.critical(self, "Download failed", str(e))
            return
        QtWidgets.QApplication.restoreOverrideCursor()

        if updater.apply_and_restart(dest):
            QtWidgets.QMessageBox.information(
                self, "Updating", "Update download ho gaya. App band hoke nayi version ke saath khulega."
            )
            QtWidgets.QApplication.quit()
        else:
            QtWidgets.QMessageBox.information(
                self, "Downloaded",
                f"Nayi .exe yahan save hui:\n{dest}\n\n"
                "(Source/python mode me auto-replace nahi hota; .exe build me hota hai.)",
            )


def main():
    app = QtWidgets.QApplication(sys.argv)
    app.setStyleSheet(BASE_STYLE)

    cfg = config.load_config()
    if not cfg.get("auth_hash"):
        salt, h = auth.hash_password("9xsecurity")
        cfg["auth_salt"], cfg["auth_hash"] = salt, h
        cfg["auth_user"] = cfg.get("auth_user", "admin")
        config.save_config(cfg)

    login = LoginDialog(cfg)
    if login.exec_() != QtWidgets.QDialog.Accepted:
        sys.exit(0)

    win = MainWindow()
    win.show()
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
