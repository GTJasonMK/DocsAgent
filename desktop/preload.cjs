const { contextBridge, ipcRenderer } = require('electron');

const leaveListeners = new Set();

ipcRenderer.on('docsagent:leave-request', (_event, request) => {
  if (!request || typeof request.id !== 'string' || typeof request.reason !== 'string') return;
  for (const listener of leaveListeners) listener(request);
});

contextBridge.exposeInMainWorld('docsAgentDesktop', {
  chooseBindingDirectory: (currentPath) => ipcRenderer.invoke(
    'docsagent:choose-binding-directory',
    typeof currentPath === 'string' ? currentPath : '',
  ),
  onLeaveRequest(listener) {
    if (typeof listener !== 'function') throw new TypeError('listener must be a function');
    leaveListeners.add(listener);
    return () => leaveListeners.delete(listener);
  },
  respondToLeave(id, allow) {
    if (typeof id === 'string') ipcRenderer.send('docsagent:leave-response', { id, allow: allow === true });
  },
});
