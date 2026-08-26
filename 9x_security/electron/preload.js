const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('native', {
  openPath: (p) => ipcRenderer.invoke('open-path', p),
  quit: () => ipcRenderer.invoke('quit-app'),
});
