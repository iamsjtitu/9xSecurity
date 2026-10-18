const { app, BrowserWindow, Tray, Menu, nativeImage, ipcMain, shell, powerSaveBlocker, dialog } = require('electron');
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

// ---- the app must never die on its own: a thrown error anywhere in the main process would
// close the whole app (user: 'software apne aap exit ho jata hai'). Log it and carry on.
process.on('uncaughtException', (err) => supLog(`UNCAUGHT ${err && err.stack ? err.stack : err}`));
process.on('unhandledRejection', (err) => supLog(`UNHANDLED PROMISE ${err && err.stack ? err.stack : err}`));
app.on('child-process-gone', (_e, d) => supLog(`child process gone: ${d.type} ${d.reason} ${d.exitCode}`));

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
  // without this handler a spawn failure (exe locked by antivirus / missing after a half update)
  // throws inside the main process and closes the app
  engineProc.on('error', (err) => {
    supLog(`engine spawn error: ${err && err.message}`);
    engineProc = null;
    scheduleEngineRestart();
  });
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

function confirmExit() {
  const r = dialog.showMessageBoxSync(win && !win.isDestroyed() ? win : null, {
    type: 'warning',
    buttons: ['Nahi, chalne do', 'Haan, band karo'],
    defaultId: 0,
    cancelId: 0,
    title: '9x Security band karein?',
    message: 'Software band karne par camera monitoring, capture aur WhatsApp alerts RUK jayenge.',
    detail: 'Window chhupane ke liye bas X dabayein — monitoring background me chalti rahegi.',
    noLink: true,
  });
  if (r === 1) { quitting = true; app.quit(); }
}

function createTray() {
  const img = appIcon();
  tray = new Tray(img.isEmpty() ? nativeImage.createEmpty() : img.resize({ width: 16, height: 16 }));
  tray.setToolTip('9x Security — background me monitoring chalu hai');
  tray.setContextMenu(Menu.buildFromTemplate([
    { label: 'Open 9x Security', click: showWindow },
    { type: 'separator' },
    { label: 'Exit (monitoring band)', click: confirmExit },
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
  // a crashed / hung UI page comes back by itself instead of staying white
  win.webContents.on('render-process-gone', (_e, d) => {
    supLog(`UI renderer gone (${d.reason}) -> reload`);
    setTimeout(() => { try { if (win && !win.isDestroyed()) win.webContents.reload(); } catch (_) { /* noop */ } }, 1000);
  });
  let hungTimer = null;
  win.on('unresponsive', () => {
    supLog('UI unresponsive -> reload in 30s unless it recovers');
    if (hungTimer) clearTimeout(hungTimer);
    hungTimer = setTimeout(() => {
      hungTimer = null;
      try { if (win && !win.isDestroyed()) win.webContents.forcefullyCrashRenderer(); } catch (_) { /* render-process-gone reloads */ }
    }, 30000);
  });
  win.on('responsive', () => { if (hungTimer) { clearTimeout(hungTimer); hungTimer = null; } });
  win.webContents.on('did-fail-load', (_e, code, desc) => {
    supLog(`UI load failed ${code} ${desc} -> retry`);
    setTimeout(() => { try { if (win && !win.isDestroyed()) win.loadFile(path.join(__dirname, 'dist', 'index.html')); } catch (_) { /* noop */ } }, 2000);
  });
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
  app.on('second-instance', (_e, argv) => {
    // the watchdog task's 5-minute probe must not pop the window up; a user double-click should
    if (!(argv || []).includes('--watchdog')) showWindow();
  });
  app.whenReady().then(() => {
    if (process.argv.includes('--watchdog')) supLog('started by the watchdog task (app was not running)');
    // Gate monitoring must keep running when the window is hidden in the tray:
    // stop Windows from putting the PC to sleep (display may still turn off).
    try { powerSaveBlocker.start('prevent-app-suspension'); } catch (_) { /* optional */ }
    startEngine();
    createTray();
    waitEngine(() => {
      createWindow();
      // PC reboot -> app + engine + camera come back by themselves (re-registered on every start)
      setTimeout(() => applyAutoStart(readPrefs().autoStart !== false), 3000);
    });
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

// ---- start with Windows: THREE independent ways (any one is enough) + a watchdog ----
//  1. registry Run key (Electron loginItem)      2. Startup-folder shortcut
//  3. Task Scheduler task "9x Security Watchdog": at logon + every 5 min it runs the app with
//     --watchdog; the single-instance lock makes that a no-op while the app is alive, and a
//     real start when it is not (crash / Task Manager kill / cleanup tool wiped 1 and 2).
//     Registered from XML so there is NO 72-hour execution limit (the plain /SC form would kill
//     an app it had started after 3 days) and no elevation needed (LeastPrivilege). Default ON.
const prefsPath = () => path.join(app.getPath('userData'), 'prefs.json');
function readPrefs() { try { return JSON.parse(fs.readFileSync(prefsPath(), 'utf8')); } catch (_) { return {}; } }
function writePrefs(p) { try { fs.writeFileSync(prefsPath(), JSON.stringify(p)); } catch (_) { /* noop */ } }
const WATCHDOG_TASK = '9x Security Watchdog';
function startupShortcut() {
  return path.join(process.env.APPDATA || path.join(os.homedir(), 'AppData', 'Roaming'),
    'Microsoft', 'Windows', 'Start Menu', 'Programs', 'Startup', '9x Security.lnk');
}
const xmlEsc = (s) => String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
function watchdogTaskXml(withLogon = true) {
  const user = process.env.USERDOMAIN && process.env.USERNAME ? `${process.env.USERDOMAIN}\\${process.env.USERNAME}` : os.userInfo().username;
  const logon = withLogon ? `    <LogonTrigger><Enabled>true</Enabled><UserId>${xmlEsc(user)}</UserId><Delay>PT20S</Delay></LogonTrigger>\n` : '';
  return `<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo><Description>9x Security ko chalu rakhta hai: logon par start, band ho jaye to 5 minute me wapas.</Description></RegistrationInfo>
  <Triggers>
${logon}    <CalendarTrigger>
      <Repetition><Interval>PT5M</Interval><StopAtDurationEnd>false</StopAtDurationEnd></Repetition>
      <StartBoundary>2024-01-01T00:00:00</StartBoundary>
      <Enabled>true</Enabled>
      <ScheduleByDay><DaysInterval>1</DaysInterval></ScheduleByDay>
    </CalendarTrigger>
  </Triggers>
  <Principals><Principal id="Author"><UserId>${xmlEsc(user)}</UserId><LogonType>InteractiveToken</LogonType><RunLevel>LeastPrivilege</RunLevel></Principal></Principals>
  <Settings>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
    <AllowHardTerminate>false</AllowHardTerminate>
    <StartWhenAvailable>true</StartWhenAvailable>
    <RunOnlyIfNetworkAvailable>false</RunOnlyIfNetworkAvailable>
    <IdleSettings><StopOnIdleEnd>false</StopOnIdleEnd><RestartOnIdle>false</RestartOnIdle></IdleSettings>
    <AllowStartOnDemand>true</AllowStartOnDemand>
    <Enabled>true</Enabled>
    <Hidden>false</Hidden>
    <RunOnlyIfIdle>false</RunOnlyIfIdle>
    <WakeToRun>false</WakeToRun>
    <ExecutionTimeLimit>PT0S</ExecutionTimeLimit>
    <Priority>5</Priority>
  </Settings>
  <Actions Context="Author"><Exec><Command>"${xmlEsc(process.execPath)}"</Command><Arguments>--watchdog</Arguments></Exec></Actions>
</Task>
`;
}
function run(cmd, args) {
  try {
    const r = spawnSync(cmd, args, { windowsHide: true, timeout: 20000, encoding: 'utf8' });
    return { ok: r.status === 0, out: `${r.stdout || ''}${r.stderr || ''}`.trim() };
  } catch (e) { return { ok: false, out: String(e && e.message) }; }
}
function applyAutoStart(on) {
  if (!app.isPackaged || process.platform !== 'win32') return;
  try { app.setLoginItemSettings({ openAtLogin: !!on, path: process.execPath, args: [] }); } catch (e) { supLog(`autostart run-key: ${e.message}`); }
  try {
    if (on) {
      fs.mkdirSync(path.dirname(startupShortcut()), { recursive: true });
      const ok = shell.writeShortcutLink(startupShortcut(), 'create', { target: process.execPath, cwd: path.dirname(process.execPath), description: '9x Security gate monitoring' });
      if (!ok) supLog('autostart startup-folder shortcut: writeShortcutLink returned false');
    } else if (fs.existsSync(startupShortcut())) {
      fs.unlinkSync(startupShortcut());
    }
  } catch (e) { supLog(`autostart startup-folder: ${e.message}`); }
  try {
    if (on) {
      const xmlPath = path.join(app.getPath('userData'), 'watchdog_task.xml');
      let r = null;
      for (const withLogon of [true, false]) { // a logon trigger may be denied without admin rights: 5-min probe alone still works
        fs.writeFileSync(xmlPath, '\ufeff' + watchdogTaskXml(withLogon), { encoding: 'utf16le' });
        r = run('schtasks', ['/Create', '/F', '/TN', WATCHDOG_TASK, '/XML', xmlPath]);
        supLog(`watchdog task (${withLogon ? 'logon + 5 min' : '5 min only'}): ${r.ok ? 'OK' : 'FAILED'} ${r.out}`);
        if (r.ok) break;
      }
    } else {
      run('schtasks', ['/Delete', '/F', '/TN', WATCHDOG_TASK]);
    }
  } catch (e) { supLog(`watchdog task: ${e.message}`); }
}
function autoStartStatus() {
  if (!app.isPackaged || process.platform !== 'win32') return { supported: false, enabled: false };
  const p = readPrefs();
  let runKey = false;
  try { runKey = !!app.getLoginItemSettings().openAtLogin; } catch (_) { /* noop */ }
  const task = run('schtasks', ['/Query', '/TN', WATCHDOG_TASK]).ok;
  return { supported: true, enabled: p.autoStart !== false, runKey, shortcut: fs.existsSync(startupShortcut()), watchdog: task };
}
ipcMain.handle('get-auto-start', () => autoStartStatus());
ipcMain.handle('set-auto-start', (_e, on) => {
  writePrefs({ ...readPrefs(), autoStart: !!on });
  applyAutoStart(on);
  return { ok: true, ...autoStartStatus() };
});
ipcMain.handle('open-file', (_e, p) => {
  try {
    const real = path.resolve(String(p || ''));
    const home = path.resolve(os.homedir());
    if ((real === home || real.startsWith(home + path.sep)) && fs.existsSync(real) && fs.statSync(real).isFile()
      && /\.(pdf|xlsx|csv|jpe?g|png|txt)$/i.test(real)) {
      return shell.openPath(real);
    }
  } catch (_) { /* noop */ }
  return 'blocked';
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
