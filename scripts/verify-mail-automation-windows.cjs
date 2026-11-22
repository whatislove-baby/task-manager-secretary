// Actual Task Scheduler smoke test. Only a new temporary, credential-free profile
// is used; its automation switch stays off and no mail/API request can be queued.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const os = require('node:os');
const {execFileSync} = require('node:child_process');
const {Module} = require('node:module');
const asar = require('@electron/asar');
if (process.platform !== 'win32') throw Error('This check requires Windows Task Scheduler.');
const root = path.resolve(__dirname, '..');
const application = path.resolve(process.env.TM_WINDOWS_APP || path.join(root, 'release-windows', 'win-unpacked'));
const resources = path.join(application, 'resources');
const archive = path.join(resources, 'app.asar');
function packagedModule(name) {
  const filename = path.join(root, 'dist-backend', 'main', name + '.js');
  const loaded = new Module(filename, module); loaded.filename = filename;
  // @electron/asar traverses names with the host's path.sep, including Windows.
  loaded._compile(asar.extractFile(archive, path.join('dist-backend', 'main', name + '.js')).toString('utf8'), filename);
  return loaded.exports;
}
const {MailAutomationHost} = packagedModule('mail-automation-host');
const {PythonBackend} = packagedModule('python-backend');
const temporary = fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(), 'tm-win-scheduler-')));
const directory = path.join(temporary, '中文 isolated data & literal');
const runtime = {python: path.join(resources, 'python', 'python.exe'), backendDirectory: path.join(resources, 'backend')};
const host = new MailAutomationHost(directory, runtime);
let bridge;
function ps(source) {
  source = '[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false); ' + source;
  const out = execFileSync('powershell.exe', ['-NoLogo', '-NoProfile', '-NonInteractive', '-EncodedCommand', Buffer.from(source, 'utf16le').toString('base64')], {encoding: 'utf8', windowsHide: true, timeout: 20000});
  return JSON.parse(out.trim().replace(/^\uFEFF/, ''));
}
function taskDetails(run = false) {
  const label = host.label;
  assert.match(label, /^com\.gao\.taskmanager\.mail\.[a-f0-9]{16}$/);
  return ps(`$ErrorActionPreference='Stop'; $sid=[Security.Principal.WindowsIdentity]::GetCurrent().User.Value; $s=New-Object -ComObject 'Schedule.Service'; $s.Connect(); $t=$s.GetFolder('\\').GetTask('${label}.'+$sid); ${run ? '$null=$t.Run($null);' : ''} $d=$t.Definition; @{user=$d.Principal.UserId;sid=$sid;logon=[int]$d.Principal.LogonType;level=[int]$d.Principal.RunLevel;interval=$d.Triggers.Item(1).Repetition.Interval;multiple=[int]$d.Settings.MultipleInstances;command=$d.Actions.Item(1).Path;arguments=$d.Actions.Item(1).Arguments;state=[int]$t.State;result=[int]$t.LastTaskResult} | ConvertTo-Json -Compress`);
}
(async () => {
  try {
    fs.mkdirSync(directory);
    assert.equal((await host.status()).installed, false);
    await host.enable(); await host.enable();
    assert.equal((await host.status()).active, true);
    const task = taskDetails(true);
    assert.equal(task.user, task.sid); assert.equal(task.logon, 3); assert.equal(task.level, 0);
    assert.equal(task.interval, 'PT1M'); assert.equal(task.multiple, 2);
    assert.equal(task.command.toLowerCase(), path.join(resources, 'python', 'pythonw.exe').toLowerCase());
    assert.ok(task.arguments.includes(directory));
    const end = Date.now() + 45000;
    let complete = false;
    while (Date.now() < end) {
      const current = taskDetails();
      if (fs.existsSync(path.join(directory, 'mail-automation.sqlite')) && current.state !== 4 && current.result === 0) {complete = true; break;}
      await new Promise(done => setTimeout(done, 500));
    }
    assert.ok(complete, 'Task Scheduler must successfully run packaged Python independently of Electron in the current interactive user session');
    bridge = new PythonBackend(directory, runtime);
    const config = await bridge.call('mail-automation:getConfig');
    assert.equal(config.ok, true, config.error); assert.equal(config.data.enabled, false); assert.deepEqual(config.data.bindingIds, []);
    const status = await bridge.call('mail-automation:getStatus');
    assert.equal(status.ok, true, status.error); assert.equal(status.data.lastRun, null);
    const items = await bridge.call('mail-automation:listResults', {});
    assert.equal(items.ok, true, items.error); assert.equal(items.data.total, 0);
    await bridge.dispose(); bridge = null;
    await host.disable(); await host.disable();
    assert.equal((await host.status()).installed, false);
    console.log('WINDOWS_TASK_SCHEDULER_PASS: packaged Python ran a real current-user, non-elevated task; Unicode path, one-minute trigger, empty disabled profile, no external accounts, and cleanup verified.');
  } finally {
    if (bridge) await bridge.dispose();
    await host.disable();
    fs.rmSync(temporary, {recursive: true, force: true});
  }
})().catch(error => {console.error(error.message); process.exitCode = 1;});
