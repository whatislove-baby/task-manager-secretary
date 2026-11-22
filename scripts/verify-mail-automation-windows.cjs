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
const sourceMode = process.argv.includes('--source');
const application = path.resolve(process.env.TM_WINDOWS_APP || path.join(root, 'release-windows', 'win-unpacked'));
const resources = path.join(application, 'resources');
const archive = path.join(resources, 'app.asar');
function packagedModule(name) {
  const filename = path.join(root, 'dist-backend', 'main', name + '.js');
  const loaded = new Module(filename, module); loaded.filename = filename;
  // @electron/asar traverses names with the host's path.sep, including Windows.
  const code = sourceMode
    ? require('typescript').transpileModule(fs.readFileSync(path.join(root, 'src', 'main', name + '.ts'), 'utf8'), {
      compilerOptions: {module: require('typescript').ModuleKind.CommonJS, target: require('typescript').ScriptTarget.ES2022},
    }).outputText
    : asar.extractFile(archive, path.join('dist-backend', 'main', name + '.js')).toString('utf8');
  loaded._compile(code, filename);
  return loaded.exports;
}
const {MailAutomationHost} = packagedModule('mail-automation-host');
const {PythonBackend} = packagedModule('python-backend');
const temporary = fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(), 'tm-win-scheduler-')));
const directory = path.join(temporary, '中文 isolated data & literal');
const runtime = sourceMode
  ? {python: path.join(root, '.venv', 'Scripts', 'python.exe'), backendDirectory: path.join(root, 'backend')}
  : {python: path.join(resources, 'python', 'python.exe'), backendDirectory: path.join(resources, 'backend')};
// This diagnostic runner is only used with the empty temporary profile above.
// Production never prints subprocess errors, which could contain private paths.
const host = new MailAutomationHost(directory, runtime, {query: async (file, args) => {
  assert.equal(file, 'powershell.exe');
  const source = Buffer.from(args.at(-1), 'base64').toString('utf16le');
  const encodedPayload = /FromBase64String\('([A-Za-z0-9+/=]+)'\)/.exec(source)?.[1];
  assert.ok(encodedPayload, 'host must use the structured, credential-free scheduler payload');
  const payload = JSON.parse(Buffer.from(encodedPayload, 'base64').toString('utf8'));
  assert.ok(['status', 'enable', 'disable'].includes(payload.operation));
  console.log('WINDOWS_TASK_SCHEDULER_PHASE: ' + payload.operation);
  const diagnostic = `try {\n${source}\n} catch {
  $record = $_
  $exception = $record.Exception
  $chain = @()
  while ($null -ne $exception) {
    $chain += @{type=$exception.GetType().FullName;hresult=$exception.HResult;message=$exception.Message}
    $exception = $exception.InnerException
  }
  $details = @{line=$record.InvocationInfo.ScriptLineNumber;category=[string]$record.CategoryInfo.Category;id=$record.FullyQualifiedErrorId;exceptions=$chain} | ConvertTo-Json -Depth 5 -Compress
  [Console]::Error.WriteLine('WINDOWS_TASK_SCHEDULER_DIAGNOSTIC: ' + $details)
  exit 1
}`;
  try {
    return execFileSync(file, [...args.slice(0, -1), Buffer.from(diagnostic, 'utf16le').toString('base64')], {
      encoding: 'utf8', windowsHide: true, timeout: 20000, stdio: ['ignore', 'pipe', 'pipe'], maxBuffer: 128 * 1024,
    }).trim();
  } catch (error) {
    // Only the fixed synthetic scheduler probe reaches this branch.
    console.error(String(error.stderr || '').slice(0, 12000));
    throw Error('Windows scheduler probe failed during ' + payload.operation + ' (exit ' + error.status + ').');
  }
}});
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
  let failure;
  try {
    console.log('WINDOWS_TASK_SCHEDULER_MODE: ' + (sourceMode ? 'source' : 'packaged'));
    fs.mkdirSync(directory);
    assert.equal((await host.status()).installed, false);
    await host.enable(); await host.enable();
    assert.equal((await host.status()).active, true);
    const task = taskDetails(true);
    assert.equal(task.user, task.sid); assert.equal(task.logon, 3); assert.equal(task.level, 0);
    assert.equal(task.interval, 'PT1M'); assert.equal(task.multiple, 2);
    assert.equal(task.command.toLowerCase(), runtime.python.replace(/python\.exe$/i, 'pythonw.exe').toLowerCase());
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
  } catch (error) {
    failure = error;
  } finally {
    for (const cleanup of [async () => {if (bridge) await bridge.dispose();}, () => host.disable(), async () => fs.rmSync(temporary, {recursive: true, force: true})]) {
      try {await cleanup();} catch (error) {console.error('WINDOWS_TASK_SCHEDULER_CLEANUP: ' + error.message); failure ||= error;}
    }
  }
  if (failure) throw failure;
  console.log('WINDOWS_TASK_SCHEDULER_PASS: ' + (sourceMode ? 'source runtime' : 'packaged Python') + ' ran a real current-user, non-elevated task; Unicode path, one-minute trigger, empty disabled profile, no external accounts, and cleanup verified.');
})().catch(error => {console.error(error.message); process.exitCode = 1;});
