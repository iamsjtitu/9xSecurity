const { app, BrowserWindow, Tray, Menu, nativeImage, ipcMain, shell, powerSaveBlocker } = require('electron');
const path = require('path');
const os = require('os');
const fs = require('fs');
const { spawn, spawnSync } = require('child_process');
const http = require('http');

const PORT = process.env.ENGINE_PORT || '8971';
let engineProc = null;
let win = null;
let tray = null;
let quitting = false;
let balloonShown = false;

function engineLogFd() {
  // Same folder the Python engine uses for user data (%LOCALAPPDATA%\9xSecurity).
  try {
    const dir = path.join(process.env.LOCALAPPDATA || path.join(os.homedir(), 'AppData', 'Local'), '9xSecurity');
    fs.mkdirSync(dir, { recursive: true });
    const p = path.join(dir, 'engine_out.log');
    try { if (fs.statSync(p).size > 5 * 1024 * 1024) fs.truncateSync(p, 0); } catch (_) { /* new file */ }
    return fs.openSync(p, 'a');
  } catch (_) {
    return 'ignore';
  }
}

function startEngine() {
  if (!app.isPackaged) return; // dev mode: run `python service.py` yourself
  const exe = path.join(process.resourcesPath, 'engine', '9xEngine.exe');
  if (process.platform === 'win32') {
    // We hold the single-instance lock, so any 9xEngine.exe alive now is an orphan
    // (app killed by the installer / Task Manager). It would keep port 8971 + the
    // camera and the UI would talk to the OLD engine.
    try { spawnSync('taskkill', ['/F', '/T', '/IM', '9xEngine.exe'], { windowsHide: true, timeout: 8000 }); } catch (_) { /* none running */ }
  }
  const fd = engineLogFd();
  // stdout/stderr go straight to a file: an unread pipe would fill up and block
  // the engine's threads the moment a library prints too much.
  engineProc = spawn(exe, [], {
    cwd: path.dirname(exe),
    windowsHide: true,
    stdio: ['ignore', fd, fd],
    env: { ...process.env, ENGINE_PORT: PORT, NX_PARENT_PID: String(process.pid) },
  });
  engineStartedAt = Date.now();
  engineProc.on('exit', (code, signal) => {
    engineProc = null;
    supLog(`engine exited code=${code} signal=${signal}`);
    scheduleEngineRestart();
  });
}

// ---- engine supervisor: the engine must never stay dead/hung while the app is open ----
// (user: 'login par fetch failed' next morning; exit + reopen fixed it = engine was gone)
let restartTimes = [];
let restartTimer = null;
let healthFails = 0;
let engineStartedAt = 0;
function supLog(msg) {
  try {
    const dir = path.join(process.env.LOCALAPPDATA || path.join(os.homedir(), 'AppData', 'Local'), '9xSecurity');
    fs.appendFileSync(path.join(dir, 'engine_out.log'), `${new Date().toISOString()} | supervisor: ${msg}\n`);
  } catch (_) { /* noop */ }
}
function scheduleEngineRestart() {
  if (quitting || !app.isPackaged || restartTimer) return;
  const now = Date.now();
  restartTimes = restartTimes.filter((t) => now - t < 10 * 60 * 1000);
  restartTimes.push(now);
  const delay = restartTimes.length > 5 ? 60000 : 3000; // crash loop -> slow down, but never give up
  supLog(`restarting engine in ${delay / 1000}s (${restartTimes.length} restarts in last 10 min)`);
  restartTimer = setTimeout(() => { restartTimer = null; if (!quitting) startEngine(); }, delay);
}
function checkHealth(cb) {
  const req = http.get(`http://127.0.0.1:${PORT}/api/health`, (res) => { res.resume(); cb(res.statusCode === 200); });
  req.on('error', () => cb(false));
  req.setTimeout(5000, () => { req.destroy(); });
}
setInterval(() => {
  if (quitting || !app.isPackaged) return;
  if (engineProc && Date.now() - engineStartedAt < 180000) return; // model load on a slow PC: give it 3 min
  checkHealth((ok) => {
    if (ok) { healthFails = 0; return; }
    healthFails += 1;
    if (healthFails < 6) return; // ~2 min unresponsive
    healthFails = 0;
    if (engineProc) {
      supLog('engine unresponsive (no /api/health for 2 min) -> kill + restart');
      try { engineProc.kill(); } catch (_) { /* exit handler restarts */ }
    } else if (!restartTimer) {
      supLog('engine process missing -> start');
      startEngine();
    }
  });
}, 20000);

function waitEngine(cb, tries = 0) {
  const req = http.get(`http://127.0.0.1:${PORT}/api/health`, (res) => {
    res.resume();
    cb();
  });
  req.on('error', () => {
    if (tries > 240) return cb(); // give up waiting, show UI anyway
    setTimeout(() => waitEngine(cb, tries + 1), 500);
  });
  req.setTimeout(1000, () => req.destroy());
}

function appIcon() {
  return nativeImage.createFromPath(path.join(__dirname, 'assets', 'icon.png'));
}

function showWindow() {
  if (!win) return;
  if (win.isMinimized()) win.restore();
  win.show();
  win.focus();
}

function createTray() {
  const img = appIcon();
  tray = new Tray(img.isEmpty() ? nativeImage.createEmpty() : img.resize({ width: 16, height: 16 }));
  tray.setToolTip('9x Security — background me monitoring chalu hai');
  tray.setContextMenu(Menu.buildFromTemplate([
    { label: 'Open 9x Security', click: showWindow },
    { type: 'separator' },
    { label: 'Exit', click: () => { quitting = true; app.quit(); } },
  ]));
  tray.on('click', showWindow);
  tray.on('double-click', showWindow);
}

function createWindow() {
  win = new BrowserWindow({
    width: 1420,
    height: 880,
    minWidth: 1180,
    minHeight: 700,
    backgroundColor: '#f8fafc',
    autoHideMenuBar: true,
    title: '9x Security',
    icon: appIcon(),
    webPreferences: {
      preload: path.join(__dirname, 'preload.js'),
      contextIsolation: true,
      nodeIntegration: false,
    },
  });
  win.loadFile(path.join(__dirname, 'dist', 'index.html'));
  // Close (X) => minimize to tray; monitoring keeps running in background.
  win.on('close', (e) => {
    if (quitting) return;
    e.preventDefault();
    win.hide();
    if (tray && !balloonShown) {
      balloonShown = true;
      try {
        tray.displayBalloon({
          title: '9x Security chal raha hai',
          content: 'App band nahi hua — camera monitoring background me chalu hai. Kholne ke liye tray icon par click karein. Band karne ke liye tray icon par right-click karke Exit dabayein.',
        });
      } catch (_) { /* balloon not supported on this OS */ }
    }
  });
}

const gotLock = app.requestSingleInstanceLock();
if (!gotLock) {
  app.quit();
} else {
  app.on('second-instance', () => showWindow());
  app.whenReady().then(() => {
    applyAutoStart(readPrefs().autoStart !== false); // PC reboot -> app + engine + camera come back by themselves
    // Gate monitoring must keep running when the window is hidden in the tray:
    // stop Windows from putting the PC to sleep (display may still turn off).
    try { powerSaveBlocker.start('prevent-app-suspension'); } catch (_) { /* optional */ }
    startEngine();
    createTray();
    waitEngine(() => createWindow());
  });
}

ipcMain.handle('open-path', (_e, p) => {
  try {
    const real = path.resolve(String(p || ''));
    const home = path.resolve(os.homedir());
    if (
      (real === home || real.startsWith(home + path.sep)) &&
      fs.existsSync(real) &&
      fs.statSync(real).isDirectory()
    ) {
      return shell.openPath(real);
    }
  } catch (_) { /* noop */ }
  return 'blocked';
});
ipcMain.handle('quit-app', () => { quitting = true; app.quit(); });

// ---- start with Windows (registry Run key via Electron); default ON for the packaged app ----
const prefsPath = () => path.join(app.getPath('userData'), 'prefs.json');
function readPrefs() { try { return JSON.parse(fs.readFileSync(prefsPath(), 'utf8')); } catch (_) { return {}; } }
function writePrefs(p) { try { fs.writeFileSync(prefsPath(), JSON.stringify(p)); } catch (_) { /* noop */ } }
function applyAutoStart(on) {
  if (!app.isPackaged) return;
  try { app.setLoginItemSettings({ openAtLogin: !!on, path: process.execPath, args: [] }); } catch (_) { /* noop */ }
}
ipcMain.handle('get-auto-start', () => {
  if (!app.isPackaged) return { supported: false, enabled: false };
  const p = readPrefs();
  return { supported: true, enabled: p.autoStart !== false };
});
ipcMain.handle('set-auto-start', (_e, on) => {
  writePrefs({ ...readPrefs(), autoStart: !!on });
  applyAutoStart(on);
  return { ok: true, enabled: !!on };
});

function killEngine() {
  if (engineProc) {
    try { engineProc.kill(); } catch (_) { /* noop */ }
    engineProc = null;
  }
}
app.on('before-quit', () => { quitting = true; killEngine(); });
app.on('window-all-closed', () => {
  // Tray keeps the app alive; only exit when user chose Exit / quit-app.
  if (quitting) { killEngine(); app.quit(); }
});
