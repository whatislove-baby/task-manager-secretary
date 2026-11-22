// Build a self-contained Windows x64 directory/ZIP; existing user data is untouched.
const fs = require('node:fs');
const path = require('node:path');
const {spawnSync} = require('node:child_process');
const {build, Platform, Arch} = require('electron-builder');
const {getConfig} = require('app-builder-lib/out/util/config/config');
const configPath = path.join(__dirname, 'electron-builder-windows.cjs');
async function effectiveConfig() {
  const config = await getConfig(path.resolve(__dirname, '..'), configPath, null);
  const resources = [...(config.extraResources || []), ...(config.win.extraResources || [])];
  const destinations = resources.map(item => String(item.to).replace(/\\/g, '/').toLowerCase());
  if (new Set(destinations).size !== destinations.length) throw Error('Duplicate Windows resource destinations: ' + destinations.join(', '));
  for (const required of ['python', 'libreoffice', 'backend/task_backend']) {
    if (destinations.filter(item => item === required).length !== 1) throw Error('Expected one resource destination: ' + required);
  }
  return config;
}
function validateZip(zipPath) {
  const size = fs.statSync(zipPath).size;
  if (size < 22) throw Error('Windows ZIP is empty or incomplete: ' + zipPath);
  const fd = fs.openSync(zipPath, 'r');
  try {
    const head = Buffer.alloc(4);
    fs.readSync(fd, head, 0, 4, 0);
    const tail = Buffer.alloc(Math.min(size, 65557));
    fs.readSync(fd, tail, 0, tail.length, size - tail.length);
    if (head.readUInt32LE() !== 0x04034b50 || tail.lastIndexOf(Buffer.from([0x50, 0x4b, 0x05, 0x06])) < 0) {
      throw Error('Windows ZIP lacks a complete ZIP directory: ' + zipPath);
    }
  } finally { fs.closeSync(fd); }
}
async function main() {
if (process.platform !== 'win32' || process.arch !== 'x64') throw Error('Build the Windows x64 package on Windows x64.');
const root = path.resolve(__dirname, '..');
const output = path.resolve(root, process.env.TM_WINDOWS_OUTPUT || 'release-windows');
if (output === root || root.startsWith(output + path.sep)) throw Error('Build output cannot replace the project or an ancestor.');
if (fs.existsSync(path.join(output, 'win-unpacked', 'data'))) throw Error('The output contains user data. Choose a fresh TM_WINDOWS_OUTPUT directory.');
const format = process.argv[2] === 'zip' ? 'zip' : 'dir';
for (const file of ['.python-runtime/python.exe', '.libreoffice-runtime-windows/program/soffice.exe']) {
  if (!fs.existsSync(path.join(root, file))) throw Error('Missing bundled runtime: ' + file);
}
const processCheck = spawnSync('powershell.exe', ['-NoProfile', '-NonInteractive', '-Command',
  "$ErrorActionPreference='Stop'; @(Get-CimInstance Win32_Process -Filter \"Name = 'Task Manager.exe'\" | ForEach-Object { $_.ExecutablePath }) | ConvertTo-Json -Compress"
], {encoding:'utf8', windowsHide:true});
if (processCheck.error || processCheck.status !== 0) throw Error('Cannot confirm whether the previous package is running.');
const found = processCheck.stdout.trim() ? JSON.parse(processCheck.stdout) : [];
for (const exe of Array.isArray(found) ? found : [found]) {
  if (exe && path.resolve(exe).toLowerCase().startsWith(output.toLowerCase() + path.sep)) throw Error('Close the app in the target output before rebuilding.');
}
await effectiveConfig();
const zipPath = path.join(output, 'Task-Manager-Windows-x64.zip');
// A previous successful build must never satisfy this build's artifact check.
if (format === 'zip') fs.rmSync(zipPath, {force: true});
await build({projectDir:root, targets:Platform.WINDOWS.createTarget([format], Arch.x64), publish:'never', config:configPath});
if (process.exitCode) throw Error('electron-builder reported failure: ' + process.exitCode);
for (const file of ['Task Manager.exe', 'resources/app.asar', 'resources/python/python.exe', 'resources/python/pythonw.exe', 'resources/libreoffice/program/soffice.exe', 'resources/backend/task_backend/__main__.py']) {
  const target = path.join(output, 'win-unpacked', file);
  if (!fs.statSync(target).isFile() || fs.statSync(target).size === 0) throw Error('Incomplete Windows package: ' + target);
}
if (format === 'zip') {
  validateZip(zipPath);
  console.log('WINDOWS_PACKAGE_ZIP=' + zipPath);
}
console.log('WINDOWS_PACKAGE_DIRECTORY=' + path.join(output,'win-unpacked'));
}
module.exports = {effectiveConfig, validateZip};
if (require.main === module) {
  const fatal = error => { console.error(error?.stack || error); process.exit(1); };
  process.on('unhandledRejection', fatal);
  process.on('uncaughtException', fatal);
  main().catch(fatal);
}
