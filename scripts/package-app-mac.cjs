const fs = require('node:fs');
const path = require('node:path');
const { build, Platform, Arch } = require('electron-builder');
const { spawnSync } = require('node:child_process');
if (process.platform !== 'darwin') throw new Error('Mac builds require macOS.');
const root = path.resolve(__dirname, '..');
const output = path.resolve(root, process.env.TM_MAC_OUTPUT || 'release-mac');
if (output === root || root.startsWith(output + path.sep)) throw new Error('Build output must not replace the project or an ancestor directory.');
const format = process.argv[2] === 'zip' ? 'zip' : 'dir';
const arch = process.arch === 'arm64' ? Arch.arm64 : Arch.x64;
// An app opened from this output must be closed before rebuilding it.
const running = spawnSync('/bin/ps', ['-axo', 'command='], {encoding: 'utf8'});
if (running.status !== 0) throw new Error('Cannot check whether the previous Mac build is running.');
const canonicalOutput = fs.existsSync(output) ? fs.realpathSync(output) : output;
if (running.stdout.split('\n').some(value => {
  const line = value.trimStart();
  return [output, canonicalOutput].some(directory => line.startsWith(directory + '/')) && line.includes('.app/Contents/MacOS/');
})) {
  throw new Error('请先退出目标目录中的 Task Manager，再重新打包：' + output);
}
if (!fs.existsSync(path.join(root, '.python-runtime-mac', 'bin', 'python3'))) throw new Error('Run npm run runtime:python:mac first.');
if (!fs.existsSync(path.join(root,'.libreoffice-runtime-mac','LibreOffice.app','Contents','MacOS','soffice'))) throw new Error('Run npm run runtime:documents:mac with an installed LibreOffice first.');
build({projectDir: root, targets: Platform.MAC.createTarget([format], arch), publish: 'never', config: {
  directories: {output},
  electronDist: path.join(root, 'node_modules', 'electron', 'dist'),
  artifactName: 'Task-Manager-Mac-${arch}.${ext}',
  mac: {identity: '-', hardenedRuntime: false, category: 'public.app-category.productivity', notarize: false},
  extraResources: [
    {from: '.python-runtime-mac', to: 'python'},
    {from: '.libreoffice-runtime-mac', to: 'libreoffice'},
    {from: 'backend/task_backend', to: 'backend/task_backend', filter: ['**/*.py', 'assets/**/*']},
  ],
}}).catch(error => {console.error(error.message); process.exitCode = 1;});
