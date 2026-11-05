const path = require('node:path');
const { spawn } = require('node:child_process');
const { app, BrowserWindow, dialog, ipcMain, Menu } = require('electron');

const projectRoot = path.resolve(__dirname, '..');
const backends = new Set();
let activeBackend = null;
let mainWindow = null;
let switchingProject = false;
let quitting = false;
let leavePromptOpen = false;
let leaveRequestSequence = 0;
const leaveRequests = new Map();

function backendCommand(root) {
  const args = ['serve', '--root', root, '--host', '127.0.0.1', '--port', '0'];
  if (app.isPackaged) {
    const executable = path.join(
      process.resourcesPath,
      'backend',
      process.platform === 'win32' ? 'docsagent-server.exe' : 'docsagent-server',
    );
    return { executable, args, cwd: root, env: process.env };
  }

  const configuredPython = process.env.DOCSAGENT_PYTHON || (process.platform === 'win32' ? 'python' : 'python3');
  const python = /[\\/]/.test(configuredPython) ? path.resolve(__dirname, configuredPython) : configuredPython;
  const pythonPath = [projectRoot, process.env.PYTHONPATH].filter(Boolean).join(path.delimiter);
  return {
    executable: python,
    args: ['-m', 'docsagent', ...args],
    cwd: root,
    env: { ...process.env, PYTHONPATH: pythonPath },
  };
}

function stopBackend(server) {
  if (!server || !server.child.pid || server.child.exitCode !== null || server.child.signalCode !== null) {
    return Promise.resolve();
  }
  if (server.stopPromise) return server.stopPromise;

  server.stopPromise = new Promise((resolve) => {
    const child = server.child;
    const signalTree = (signal, force = false) => {
      if (process.platform === 'win32') {
        const args = ['/pid', String(child.pid), '/t'];
        if (force) args.push('/f');
        const killer = spawn('taskkill', args, { stdio: 'ignore', windowsHide: true });
        killer.unref();
      } else {
        try {
          process.kill(-child.pid, signal);
        } catch (error) {
          if (error.code !== 'ESRCH') child.kill(signal);
        }
      }
    };
    const terminateTimer = setTimeout(() => signalTree('SIGTERM'), 2500);
    const forceTimer = setTimeout(() => signalTree('SIGKILL', true), 5000);
    child.once('exit', () => {
      clearTimeout(terminateTimer);
      clearTimeout(forceTimer);
      backends.delete(server);
      resolve();
    });
    signalTree(process.platform === 'win32' ? 'SIGTERM' : 'SIGINT');
  });
  return server.stopPromise;
}

function launchBackend(root) {
  const command = backendCommand(root);
  const child = spawn(command.executable, command.args, {
    cwd: command.cwd,
    env: command.env,
    stdio: ['ignore', 'pipe', 'pipe'],
    detached: process.platform !== 'win32',
    windowsHide: true,
  });
  const server = { child, root, url: null, origin: null, stopPromise: null };
  backends.add(server);

  return new Promise((resolve, reject) => {
    let settled = false;
    let stdout = '';
    let stderr = '';
    const timeout = setTimeout(() => {
      fail(new Error('DocsAgent backend did not start within 30 seconds.'));
      void stopBackend(server);
    }, 30000);

    function fail(error) {
      if (settled) return;
      settled = true;
      clearTimeout(timeout);
      reject(error);
    }

    child.once('error', (error) => {
      backends.delete(server);
      fail(error);
    });
    child.once('exit', (code, signal) => {
      backends.delete(server);
      if (!settled) {
        fail(new Error(stderr.trim() || `DocsAgent backend exited (${signal || code}).`));
      }
      if (activeBackend === server && !quitting) {
        dialog.showErrorBox('DocsAgent stopped', stderr.trim() || 'The local DocsAgent service stopped unexpectedly.');
        app.quit();
      }
    });
    child.stderr.on('data', (chunk) => {
      stderr = (stderr + chunk.toString()).slice(-8000);
      if (!app.isPackaged) process.stderr.write(chunk);
    });
    child.stdout.on('data', (chunk) => {
      stdout += chunk.toString();
      const lines = stdout.split(/\r?\n/);
      stdout = lines.pop() || '';
      for (const line of lines) {
        if (!app.isPackaged) process.stdout.write(`${line}\n`);
        const ready = line.match(/^DOCSAGENT_READY 127\.0\.0\.1 (\d+)$/);
        if (!ready || settled) continue;
        const port = Number(ready[1]);
        server.url = `http://127.0.0.1:${port}`;
        server.origin = server.url;
        settled = true;
        clearTimeout(timeout);
        resolve(server);
      }
    });
  });
}

async function chooseProject() {
  const options = {
    title: 'Open a project',
    buttonLabel: 'Open project',
    properties: ['openDirectory'],
  };
  const result = mainWindow
    ? await dialog.showOpenDialog(mainWindow, options)
    : await dialog.showOpenDialog(options);
  return result.canceled ? null : result.filePaths[0];
}

function requestRendererApproval(reason) {
  if (!mainWindow || mainWindow.isDestroyed() || !activeBackend) return Promise.resolve(true);
  const id = `leave-${++leaveRequestSequence}`;
  return new Promise((resolve) => {
    const timer = setTimeout(() => {
      leaveRequests.delete(id);
      resolve(false);
    }, 15000);
    leaveRequests.set(id, { resolve, timer });
    mainWindow.webContents.send('docsagent:leave-request', { id, reason });
  });
}

ipcMain.on('docsagent:leave-response', (event, response) => {
  if (event.sender !== mainWindow?.webContents || !response || typeof response.id !== 'string') return;
  const request = leaveRequests.get(response.id);
  if (!request) return;
  clearTimeout(request.timer);
  leaveRequests.delete(response.id);
  request.resolve(response.allow === true);
});

ipcMain.handle('docsagent:choose-binding-directory', async (event, currentPath) => {
  if (event.sender !== mainWindow?.webContents || !activeBackend) return null;
  const root = path.resolve(activeBackend.root);
  let defaultPath = root;
  if (typeof currentPath === 'string' && currentPath.trim()) {
    const candidate = path.resolve(root, currentPath);
    const relative = path.relative(root, candidate);
    if (relative !== '..' && !relative.startsWith(`..${path.sep}`) && !path.isAbsolute(relative)) defaultPath = candidate;
  }
  const result = await dialog.showOpenDialog(mainWindow, {
    title: '选择项目内目录',
    buttonLabel: '选择目录',
    defaultPath,
    properties: ['openDirectory'],
  });
  if (result.canceled || !result.filePaths[0]) return null;
  const selected = path.resolve(result.filePaths[0]);
  const relative = path.relative(root, selected);
  if (relative === '..' || relative.startsWith(`..${path.sep}`) || path.isAbsolute(relative)) {
    throw new Error('绑定目录必须位于当前项目根目录内');
  }
  return relative ? relative.split(path.sep).join('/') : '.';
});

async function createWindow(server) {
  mainWindow = new BrowserWindow({
    width: 1440,
    height: 940,
    minWidth: 960,
    minHeight: 640,
    title: `DocsAgent - ${path.basename(server.root)}`,
    webPreferences: {
      preload: path.join(__dirname, 'preload.cjs'),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
    },
  });
  mainWindow.webContents.setWindowOpenHandler(() => ({ action: 'deny' }));
  mainWindow.webContents.on('will-navigate', (event, url) => {
    if (!activeBackend || new URL(url).origin !== activeBackend.origin) event.preventDefault();
  });
  mainWindow.on('close', (event) => {
    if (quitting) return;
    event.preventDefault();
    if (leavePromptOpen) return;
    leavePromptOpen = true;
    void requestRendererApproval('quit').then((allow) => {
      leavePromptOpen = false;
      if (allow) quitAfterStopping();
    });
  });
  mainWindow.on('closed', () => {
    mainWindow = null;
    for (const request of leaveRequests.values()) {
      clearTimeout(request.timer);
      request.resolve(false);
    }
    leaveRequests.clear();
  });
  await mainWindow.loadURL(server.url);
}

async function openProject() {
  if (switchingProject || quitting) return;
  switchingProject = true;
  let nextBackend = null;
  const previousBackend = activeBackend;
  try {
    if (previousBackend && !(await requestRendererApproval('project-switch'))) return;
    const root = await chooseProject();
    if (!root || quitting) return;
    if (previousBackend && path.resolve(root) === path.resolve(previousBackend.root)) return;
    nextBackend = await launchBackend(root);
    if (quitting) {
      await stopBackend(nextBackend);
      return;
    }
    activeBackend = nextBackend;
    if (mainWindow && !mainWindow.isDestroyed()) {
      await mainWindow.loadURL(nextBackend.url);
      mainWindow.setTitle(`DocsAgent - ${path.basename(root)}`);
    } else {
      await createWindow(nextBackend);
    }
    await stopBackend(previousBackend);
  } catch (error) {
    activeBackend = previousBackend;
    if (nextBackend) await stopBackend(nextBackend);
    let restoreMessage = '';
    if (previousBackend && mainWindow && !mainWindow.isDestroyed()) {
      try {
        await mainWindow.loadURL(previousBackend.url);
      } catch (restoreError) {
        restoreMessage = restoreError instanceof Error ? restoreError.message : String(restoreError);
      }
    }
    const message = error instanceof Error ? error.message : String(error);
    dialog.showErrorBox('Could not open project', [message, restoreMessage].filter(Boolean).join('\n\n'));
  } finally {
    switchingProject = false;
  }
}

function installMenu() {
  Menu.setApplicationMenu(Menu.buildFromTemplate([
    {
      label: 'File',
      submenu: [
        { label: 'Open Project...', accelerator: 'CmdOrCtrl+O', click: () => { void openProject(); } },
        { type: 'separator' },
        { role: 'quit' },
      ],
    },
  ]));
}

function quitAfterStopping() {
  if (quitting) return;
  quitting = true;
  Promise.all([...backends].map(stopBackend)).finally(() => {
    activeBackend = null;
    app.quit();
  });
}

app.whenReady().then(async () => {
  installMenu();
  const root = process.env.DOCSAGENT_PROJECT_ROOT
    ? path.resolve(process.env.DOCSAGENT_PROJECT_ROOT)
    : await chooseProject();
  if (!root) {
    app.quit();
    return;
  }
  activeBackend = await launchBackend(root);
  await createWindow(activeBackend);
}).catch((error) => {
  dialog.showErrorBox('Could not start DocsAgent', error.message || String(error));
  app.quit();
});

app.on('before-quit', (event) => {
  if (quitting) return;
  event.preventDefault();
  if (leavePromptOpen) return;
  leavePromptOpen = true;
  void requestRendererApproval('quit').then((allow) => {
    leavePromptOpen = false;
    if (allow) quitAfterStopping();
  });
});

process.once('SIGINT', quitAfterStopping);
process.once('SIGTERM', quitAfterStopping);

app.on('window-all-closed', () => app.quit());
