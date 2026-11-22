import test, {type TestContext} from 'node:test';
import assert from 'node:assert/strict';
import {mkdir, mkdtemp, rm, writeFile} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join, resolve} from 'node:path';
import {MailAutomationHost, type AutomationRuntime} from '../src/main/mail-automation-host';
import {dispatchPython} from '../src/main/python-desktop';
import {defaultMailAutomationConfig} from '../src/shared/mail-automation';
import type {Result} from '../src/shared/contracts';

interface Payload {operation: string; label: string; python: string; arguments: string; cwd: string}
class FakeScheduler {
  calls: Payload[] = [];
  scripts: string[] = [];
  tasks = new Map<string, Payload>();
  failEnable = false;
  failRead = false;
  rawResponse?: string;
  query = async (file: string, args: string[]) => {
    assert.equal(file, 'powershell.exe');
    assert.deepEqual(args.slice(0, 4), ['-NoLogo', '-NoProfile', '-NonInteractive', '-EncodedCommand']);
    const script = Buffer.from(args[4], 'base64').toString('utf16le');
    const encoded = /FromBase64String\('([A-Za-z0-9+/=]+)'\)/.exec(script)?.[1];
    assert.ok(encoded);
    const payload = JSON.parse(Buffer.from(encoded, 'base64').toString('utf8')) as Payload;
    this.calls.push(payload); this.scripts.push(script);
    if (payload.operation === 'enable') {
      if (this.failEnable) throw Error('synthetic registration failure');
      this.tasks.set(payload.label, payload);
    } else if (payload.operation === 'disable') this.tasks.delete(payload.label);
    else if (this.failRead) throw Error('synthetic task service unavailable');
    if (this.rawResponse !== undefined) return this.rawResponse;
    const stored = this.tasks.get(payload.label);
    return JSON.stringify({installed: !!stored, active: !!stored && stored.python === payload.python && stored.arguments === payload.arguments});
  };
}
async function fixture(t: TestContext) {
  const root = await mkdtemp(join(tmpdir(), 'tm-windows-task-test-'));
  t.after(() => rm(root, {recursive: true, force: true}));
  const directory = join(root, '资料 with spaces & literal $(x)');
  const runtime: AutomationRuntime = {python: join(root, 'runtime 中文', 'python.exe'), backendDirectory: join(root, 'backend & resources')};
  await mkdir(join(runtime.python, '..'), {recursive: true});
  await writeFile(runtime.python, 'fixture executable; never run');
  await writeFile(runtime.python.replace('python.exe', 'pythonw.exe'), 'fixture hidden executable; never run');
  await mkdir(join(runtime.backendDirectory, 'task_backend'), {recursive: true});
  await writeFile(join(runtime.backendDirectory, 'task_backend', 'automation_worker.py'), '# fixture; never run');
  const scheduler = new FakeScheduler();
  const starts: {file: string; args: string[]; cwd: string}[] = [];
  const options = {platform: 'win32', query: scheduler.query, start: async (file: string, args: string[], cwd: string) => {starts.push({file, args, cwd});}};
  return {root, directory, runtime, scheduler, starts, options, host: new MailAutomationHost(directory, runtime, options)};
}

test('Windows lifecycle queries actual scheduler and serializes enable/disable without touching Mac', async t => {
  const f = await fixture(t);
  assert.equal((await f.host.status()).installed, false);
  assert.equal(f.scheduler.tasks.size, 0);
  await Promise.all([f.host.enable(), f.host.enable(), f.host.enable()]);
  assert.equal(f.scheduler.tasks.size, 1);
  assert.equal((await f.host.status()).active, true);
  await f.host.disable(); await f.host.disable();
  assert.equal((await f.host.status()).installed, false);
  assert.equal(f.starts.length, 0, 'scheduler owns invocation; enabling does not add a desktop-owned worker');
});

test('Windows task payload preserves Unicode and shell metacharacters as data, using hidden Python', async t => {
  const f = await fixture(t); await f.host.enable();
  const payload = f.scheduler.calls[0];
  assert.equal(payload.python, f.runtime.python.replace('python.exe', 'pythonw.exe'));
  assert.equal(payload.cwd, resolve(f.runtime.backendDirectory));
  assert.equal(payload.arguments, ['-X', 'utf8', '-B', '-u', '-m', 'task_backend.automation_worker', '--data-dir', resolve(f.directory), '--once'].map(a => '"' + a + '"').join(' '));
  const script = f.scheduler.scripts[0];
  assert.ok(!script.includes(f.directory), 'untrusted path never interpolated as PowerShell source');
  assert.match(script, /LogonType = 3/); assert.match(script, /RunLevel = 0/);
  assert.match(script, /Repetition.Interval = 'PT1M'/);
  assert.match(script, /MultipleInstances = 2/);
  assert.match(script, /WindowsIdentity\]::GetCurrent\(\).User.Value/);
  assert.match(script, /\[Security\.Principal\.NTAccount\]::new\(\$identity\)\)\.Translate\(\[Security\.Principal\.SecurityIdentifier\]\)/);
  assert.match(script, /\$ownerSid = Resolve-TaskUserSid \(\[string\]\$task\.Definition\.Principal\.UserId\)/);
  assert.match(script, /\$ownerSid -ne \$sid -or \$task\.Definition\.RegistrationInfo\.Description -ne \$marker/);
  assert.match(script, /\$logonSid = Resolve-TaskUserSid \(\[string\]\$atLogon\.UserId\)/);
  assert.match(script, /\$logonSid -eq \$sid/);
  assert.match(script, /\$p.label \+ '\.' \+ \$sid/);
  assert.doesNotMatch(script, /Invoke-Expression|cmd\.exe|ExecutionPolicy|RunLevel = 1|Credential|Password/);
});

test('Windows profile identity is case-insensitive, isolated by data directory and refreshed after relocation', async t => {
  const f = await fixture(t); await f.host.enable();
  assert.equal(new MailAutomationHost(f.directory.toUpperCase(), f.runtime, f.options).label, f.host.label);
  const other = new MailAutomationHost(join(f.root, 'other data'), f.runtime, f.options);
  await other.enable(); assert.notEqual(other.label, f.host.label);
  const movedPython = join(f.root, 'moved runtime', 'python.exe');
  await mkdir(join(movedPython, '..'), {recursive: true}); await writeFile(movedPython, 'never run');
  const moved = new MailAutomationHost(f.directory, {...f.runtime, python: movedPython}, f.options);
  assert.equal((await moved.status()).installed, true); assert.equal((await moved.status()).active, false);
  await moved.enable(); assert.equal((await moved.status()).active, true);
  await moved.disable(); assert.equal((await other.status()).active, true);
});

test('scheduler errors and malformed replies cannot report a healthy Windows background', async t => {
  const f = await fixture(t); f.scheduler.failRead = true;
  await assert.rejects(f.host.status(), /unavailable/);
  f.scheduler.failRead = false;
  for (const raw of ['not-json', '{}', '{"installed":"yes","active":true}']) {
    f.scheduler.rawResponse = raw;
    await assert.rejects(f.host.status(), /无效状态/);
  }
  f.scheduler.rawResponse = '{"installed":true,"active":false}';
  await assert.rejects(f.host.enable(), /未能启用/);
});

test('failed Windows registration rolls persisted automation back through the existing IPC contract', async t => {
  const f = await fixture(t); f.scheduler.failEnable = true;
  let config = structuredClone(defaultMailAutomationConfig);
  const backend = {call: async (method: string, ...args: unknown[]): Promise<Result<unknown>> => {
    assert.equal(method, 'mail-automation:saveConfig'); config = structuredClone(args[0] as typeof config);
    return {ok: true, data: config};
  }};
  const result = await dispatchPython(backend, true, 'mail-automation:saveConfig', [{...config, enabled: true}], () => {}, {automation: f.host});
  assert.equal(result.ok, false); assert.equal(config.enabled, false); assert.equal(f.scheduler.tasks.size, 0);
});

test('Windows immediate refresh does not register a task or enable background polling', async t => {
  const f = await fixture(t); await f.host.startOnce();
  assert.equal(f.scheduler.calls.length, 0);
  assert.deepEqual(f.starts, [{file: f.runtime.python, args: ['-B', '-u', '-m', 'task_backend.automation_worker', '--data-dir', resolve(f.directory), '--once'], cwd: f.runtime.backendDirectory}]);
});
