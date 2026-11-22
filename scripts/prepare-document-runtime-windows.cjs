// Administratively extract the vendor MSI; do not install it into the host.
const path = require('node:path');
const {spawnSync} = require('node:child_process');
if (process.platform !== 'win32' || process.arch !== 'x64') throw Error('Windows x64 build required.');
const root = path.resolve(__dirname, '..');
const python = path.join(root, '.venv', 'Scripts', 'python.exe');
const result = spawnSync(python, [path.join(root, 'scripts', 'prepare-document-runtime-windows.py')], {
  cwd: root, stdio: 'inherit', windowsHide: true, env: {...process.env, PYTHONUTF8: '1'},
});
if (result.error) throw result.error;
process.exit(result.status ?? 1);
