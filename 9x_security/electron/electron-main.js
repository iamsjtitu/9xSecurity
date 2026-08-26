const { app, BrowserWindow, ipcMain, shell } = require('electron');
const path = require('path');
const { spawn } = require('child_process');
const http = require('http');

const PORT = process.env.ENGINE_PORT || '8971';
let engineProc = null;
let win = null;

function startEngine() {
  if (!app.isPackaged) return; // dev mode: run `python service.py` yourself
  const exe = path.join(process.resourcesPath, 'engine', '9xEngine.exe');
  engineProc = spawn(exe, [], {
    cwd: path.dirname(exe),
    windowsHide: true,
    env: { ...process.env, ENGINE_PORT: PORT },
  });
  engineProc.on('exit', () => { engineProc = null; });
}

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

function createWindow() {
  win = new BrowserWindow({
    width: 1420,
    height: 880,
    minWidth: 1180,
    minHeight: 700,
    backgroundColor: '#f8fafc',
    autoHideMenuBar: true,
    title: '9x Security',
    webPreferences: {
      preload: path.join(__dirname, 'preload.js'),
      contextIsolation: true,
      nodeIntegration: false,
    },
  });
  win.loadFile(path.join(__dirname, 'dist', 'index.html'));
}

const gotLock = app.requestSingleInstanceLock();
if (!gotLock) {
  app.quit();
} else {
  app.on('second-instance', () => {
    if (win) { if (win.isMinimized()) win.restore(); win.focus(); }
  });
  app.whenReady().then(() => {
    startEngine();
    waitEngine(() => createWindow());
  });
}

ipcMain.handle('open-path', (_e, p) => shell.openPath(p));
ipcMain.handle('quit-app', () => app.quit());

function killEngine() {
  if (engineProc) {
    try { engineProc.kill(); } catch (_) { /* noop */ }
    engineProc = null;
  }
}
app.on('before-quit', killEngine);
app.on('window-all-closed', () => { killEngine(); app.quit(); });
