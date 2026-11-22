// Build a self-contained, relocatable runtime; never package a Homebrew venv.
const fs = require('node:fs');
const path = require('node:path');
const { spawnSync } = require('node:child_process');
const { createHash } = require('node:crypto');
if (process.platform !== 'darwin') throw new Error('Mac runtime must be prepared on macOS.');
const root = path.resolve(__dirname, '..');
const version = '3.12.13';
const arch = process.arch === 'arm64' ? 'aarch64' : process.arch === 'x64' ? 'x86_64' : null;
if (!arch) throw new Error('Unsupported Mac architecture: ' + process.arch);
const cache = path.join(root, '.cache', 'uv');
const installs = path.join(root, '.cache', 'managed-python');
const distribution = `cpython-${version}-macos-${arch}-none`;
const target = path.join(root, '.python-runtime-mac');
const uv = process.env.TM_UV || 'uv';
function run(command, args, options = {}) {
  const result = spawnSync(command, args, {cwd: root, stdio: 'inherit', ...options});
  if (result.error) throw new Error(`Cannot run ${command}: ${result.error.message}`);
  if (result.status !== 0) throw new Error(`${command} failed (${result.status}).`);
  return result;
}
// uv verifies its distribution checksum. Install only inside this project.
run(uv, ['--cache-dir', cache, '--no-config', 'python', 'install', '--install-dir', installs, '--no-bin', distribution]);
const source = path.join(installs, distribution);
if (!fs.existsSync(path.join(source, 'bin', 'python3'))) throw new Error('Standalone runtime not found.');
fs.rmSync(target, {recursive: true, force: true});
fs.cpSync(source, target, {recursive: true, verbatimSymlinks: true});
const python = path.join(target, 'bin', 'python3');
const requirements = path.join(root, 'backend', 'requirements-mac.txt');
run(uv, ['--cache-dir', cache, '--no-config', 'pip', 'install', '--python', python,
  '--target', path.join(target, 'lib', 'python3.12', 'site-packages'), '--only-binary', ':all:', '--no-deps', '-r', requirements]);
const hashes = {};
for (const file of ['requirements.txt', 'requirements-mac.txt', 'requirements-agent.txt']) {
  hashes[file] = createHash('sha256').update(fs.readFileSync(path.join(root, 'backend', file))).digest('hex');
}
fs.writeFileSync(path.join(target, 'runtime-manifest.json'), JSON.stringify({
  version, architecture: process.arch, distribution: 'astral-sh/python-build-standalone',
  release: fs.readFileSync(path.join(source, 'BUILD'), 'utf8').trim(), requirementsSha256: hashes,
}, null, 2) + '\n');
run(python, ['-I', '-c', 'import pypdf,reportlab,PIL,fontTools;import sqlite3,ssl,ctypes,icalendar,imapclient,tzlocal,dateutil,cryptography; import sys; print("MAC_RUNTIME_READY", sys.version)']);
run(python, ['-B', '-I', path.join(root, 'scripts', 'verify-runtime-deps.py'), '--requirements', requirements, '--smoke']);
