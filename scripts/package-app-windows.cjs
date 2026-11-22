// Build a self-contained Windows x64 directory/ZIP; existing user data is untouched.
const fs = require('node:fs');
const path = require('node:path');
const {spawnSync} = require('node:child_process');
const {build, Platform, Arch} = require('electron-builder');
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
build({projectDir:root, targets:Platform.WINDOWS.createTarget([format], Arch.x64), publish:'never', config:{
  directories:{output},
  artifactName:'Task-Manager-Windows-x64.${ext}',
  win:{target:format, signAndEditExecutable:false, extraResources:[
    {from:'.python-runtime', to:'python'},
    {from:'.libreoffice-runtime-windows', to:'libreoffice'},
    {from:'backend/task_backend', to:'backend/task_backend', filter:['**/*.py','assets/**/*']},
  ]},
  extraFiles:[
    {from:'docs/WINDOWS.md',to:'WINDOWS-START.md'},
    {from:'docs/product-kit/user-manual.pdf',to:'User-Manual.pdf'},
  ],
}}).then(() => console.log('WINDOWS_PACKAGE_DIRECTORY=' + path.join(output,'win-unpacked')))
  .catch(error => {console.error(error.message);process.exitCode=1;});
