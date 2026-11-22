// 固定暂存目录构建，成功后更新固定应用目录；data始终留在原处。
const fs = require('node:fs');
const path = require('node:path');
const { spawnSync } = require('node:child_process');
const { createHash } = require('node:crypto');
const root = path.resolve(__dirname, '..');
const release = path.join(root, 'release-v2');
const staging = path.join(release, '.packaging');
const targetApp = path.join(release, 'win-unpacked');
function insideRelease(target) {
  const relative = path.relative(release, path.resolve(target));
  if (!relative || relative.startsWith('..') || path.isAbsolute(relative)) throw new Error('拒绝操作发布目录之外的路径：' + target);
}
function hasData(directory) {
  if (!fs.existsSync(directory)) return false;
  for (const entry of fs.readdirSync(directory, { withFileTypes: true })) {
    if (entry.isSymbolicLink()) throw new Error('发布残留中存在链接，请先人工检查：' + path.join(directory, entry.name));
    if (entry.isDirectory() && (entry.name.toLowerCase() === 'data' || hasData(path.join(directory, entry.name)))) return true;
  }
  return false;
}
function removeArtifact(directory, preserveOldData = false) {
  insideRelease(directory);
  if (fs.existsSync(directory) && fs.lstatSync(directory).isSymbolicLink()) throw new Error('拒绝清理链接目录：' + directory);
  const containsData = hasData(directory);
  if (containsData && !preserveOldData) throw new Error('拒绝删除包含data的目录：' + directory);
  if (containsData) {
    const archiveBase = path.join(targetApp, 'data', 'backups', 'previous-build-data', path.basename(directory));
    function archiveData(current) {
      for (const entry of fs.readdirSync(current, { withFileTypes: true })) {
        if (!entry.isDirectory()) continue;
        const source = path.join(current, entry.name);
        if (entry.name.toLowerCase() !== 'data') { archiveData(source); continue; }
        const destination = path.join(archiveBase, path.relative(directory, source));
        const sourceHashes = dataHashes(source);
        if (!fs.existsSync(destination)) fs.cpSync(source, destination, { recursive: true, force: false, errorOnExist: true });
        const copiedHashes = dataHashes(destination);
        if (sourceHashes.size !== copiedHashes.size || [...sourceHashes].some(([file, hash]) => copiedHashes.get(path.join(destination, path.relative(source, file))) !== hash)) throw new Error('旧数据归档校验失败，保留原目录：' + source);
        console.log('OLD_DATA_ARCHIVED=' + destination);
      }
    }
    archiveData(directory);
  }
  fs.rmSync(directory, { recursive: true, force: true });
}
function dataHashes(directory, result = new Map()) {
  if (!fs.existsSync(directory)) return result;
  for (const entry of fs.readdirSync(directory, { withFileTypes: true })) {
    const file = path.join(directory, entry.name);
    if (entry.isDirectory()) dataHashes(file, result);
    else if (entry.isFile()) result.set(file, createHash('sha256').update(fs.readFileSync(file)).digest('hex'));
  }
  return result;
}
fs.mkdirSync(release, { recursive: true });
// 程序运行时拒绝替换；不会关闭用户的窗口。
const probe = spawnSync('powershell.exe', ['-NoProfile', '-NonInteractive', '-Command', `$ErrorActionPreference="Stop"; @(Get-CimInstance Win32_Process -Filter "Name = 'Task Manager.exe'" | ForEach-Object { $_.ExecutablePath }) | ConvertTo-Json -Compress`], { encoding: 'utf8', windowsHide: true });
if (probe.error || probe.status !== 0) throw new Error('无法确认应用是否已关闭，更新已停止。');
const processes = probe.stdout.trim() ? JSON.parse(probe.stdout) : [];
for (const executable of (Array.isArray(processes) ? processes : [processes])) {
  if (executable && path.dirname(path.resolve(executable)).toLowerCase() === targetApp.toLowerCase()) throw new Error('请先关闭 Task Manager，再重新构建。');
}
removeArtifact(staging);
const format = process.argv[2] === 'zip' ? 'zip' : 'dir';
const result = spawnSync(process.execPath, [require.resolve('electron-builder/cli.js'), '--win', format, '--config.directories.output=' + staging], { cwd: root, stdio: 'inherit', windowsHide: true });
if (result.error) throw result.error;
if (result.status !== 0) process.exit(result.status || 1);
const sourceApp = path.join(staging, 'win-unpacked');
if (!fs.existsSync(path.join(sourceApp, 'Task Manager.exe'))) throw new Error('缺少构建产物。');
const original = dataHashes(path.join(targetApp, 'data'));
fs.mkdirSync(targetApp, { recursive: true });
for (const entry of fs.readdirSync(sourceApp)) {
  if (entry.toLowerCase() === 'data') continue;
  fs.cpSync(path.join(sourceApp, entry), path.join(targetApp, entry), { recursive: true, force: true });
}
const current = dataHashes(path.join(targetApp, 'data'));
if (original.size !== current.size || [...original].some(([file, hash]) => current.get(file) !== hash)) throw new Error('data保留检查失败。');
if (format === 'zip') {
  const archives = fs.readdirSync(staging).filter(name => name.endsWith('.zip'));
  if (archives.length !== 1) throw new Error('未找到唯一ZIP产物。');
  fs.copyFileSync(path.join(staging, archives[0]), path.join(release, 'Task Manager.zip'));
} else if (fs.existsSync(path.join(release, 'Task Manager.zip'))) {
  // 普通构建后移除旧ZIP，避免误用旧程序。
  fs.unlinkSync(path.join(release, 'Task Manager.zip'));
}
removeArtifact(staging);
for (const entry of fs.readdirSync(release, { withFileTypes: true })) {
  if (entry.isDirectory() && (/^build-\d{8}-\d{6}-\d+$/.test(entry.name) || entry.name === 'win-unpacked.tmp')) removeArtifact(path.join(release, entry.name), true);
}
const debug = path.join(release, 'builder-debug.yml');
if (fs.existsSync(debug)) fs.unlinkSync(debug);
console.log('APP_OUTPUT_DIRECTORY=' + targetApp);
console.log('DATA_FILES_PRESERVED=' + original.size);
