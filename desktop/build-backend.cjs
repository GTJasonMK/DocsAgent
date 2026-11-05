const fs = require('node:fs');
const path = require('node:path');
const { spawnSync } = require('node:child_process');

const desktopRoot = __dirname;
const projectRoot = path.resolve(desktopRoot, '..');
const webRoot = path.join(projectRoot, 'docsagent', 'web', 'dist');
const outputRoot = path.join(desktopRoot, 'build', 'backend');
const workRoot = path.join(desktopRoot, 'build', 'pyinstaller');
const configuredPython = process.env.DOCSAGENT_PYTHON || (process.platform === 'win32' ? 'python' : 'python3');
const python = /[\\/]/.test(configuredPython) ? path.resolve(desktopRoot, configuredPython) : configuredPython;

if (!fs.existsSync(path.join(webRoot, 'index.html'))) {
  process.stderr.write('Frontend bundle is missing; run the frontend build first.\n');
  process.exit(1);
}

fs.mkdirSync(outputRoot, { recursive: true });
fs.mkdirSync(workRoot, { recursive: true });

const data = `${webRoot}${path.delimiter}docsagent/web/dist`;
const result = spawnSync(python, [
  '-m', 'PyInstaller',
  '--clean',
  '--noconfirm',
  '--onefile',
  '--name', 'docsagent-server',
  '--paths', projectRoot,
  '--distpath', outputRoot,
  '--workpath', workRoot,
  '--specpath', workRoot,
  '--add-data', data,
  path.join(desktopRoot, 'backend_entry.py'),
], { cwd: desktopRoot, stdio: 'inherit' });

if (result.error) {
  process.stderr.write(`${result.error.message}\n`);
  process.exit(1);
}
process.exit(result.status ?? 1);
