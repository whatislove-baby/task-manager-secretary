import { createHash } from 'node:crypto';
import { execFile, spawn } from 'node:child_process';
import { access, mkdir, readFile, rename, rm, writeFile } from 'node:fs/promises';
import { homedir } from 'node:os';
import { join, resolve } from 'node:path';
import type { MailAutomationBackgroundStatus } from '../shared/mail-automation';

export interface AutomationHost {
  status(): Promise<MailAutomationBackgroundStatus>;
  enable(): Promise<void>;
  disable(): Promise<void>;
  startOnce(): Promise<void>;
}
export interface AutomationRuntime { python: string; backendDirectory: string }
interface HostOptions {
  platform?: string;
  uid?: number;
  home?: string;
  execute?: (file: string, args: string[]) => Promise<void>;
  query?: (file: string, args: string[]) => Promise<string>;
  start?: (file: string, args: string[], cwd: string) => Promise<void>;
}

const xml = (value: string) => value.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;').replace(/'/g, '&apos;');
const execute = (file: string, args: string[]) => new Promise<void>((done, fail) => {
  execFile(file, args, {timeout: 15000, maxBuffer: 128 * 1024, windowsHide: true}, error => error ? fail(new Error('系统后台服务操作失败。')) : done());
});
const query = (file: string, args: string[]) => new Promise<string>((done, fail) => {
  execFile(file, args, {timeout: 20000, maxBuffer: 128 * 1024, windowsHide: true, encoding: 'utf8'}, (error, stdout) => error
    ? fail(new Error('Windows 任务计划程序操作失败，请检查当前用户的任务计划权限后重试。')) : done(stdout.trim()));
});

// Windows CommandLineToArgvW quoting; these arguments are passed directly to
// Python by Task Scheduler, never through cmd.exe or a shell.
const windowsArgument = (value: string) => '"' + value.replace(/(\\*)"/g, '$1$1\\"').replace(/(\\+)$/g, '$1$1') + '"';

/** The official Task Scheduler COM API works without installing a service.
 * Only current-user interactive-token tasks are registered, at normal privilege.
 * The JSON payload contains executable paths and argv, never account secrets.
 */
function windowsCommand(payload: Record<string, unknown>): string[] {
  const encoded = Buffer.from(JSON.stringify(payload), 'utf8').toString('base64');
  const script = `$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
$p = [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('${encoded}')) | ConvertFrom-Json
$sid = [Security.Principal.WindowsIdentity]::GetCurrent().User.Value
$name = [string]$p.label + '.' + $sid
$service = New-Object -ComObject 'Schedule.Service'
$service.Connect()
$folder = $service.GetFolder('\\')
$task = $null
try { $task = $folder.GetTask($name) } catch {
  $errorCode = $_.Exception.HResult
  if ($_.Exception.InnerException) { $errorCode = $_.Exception.InnerException.HResult }
  if ($errorCode -ne -2147024894) { throw }
}
$marker = 'Task Manager mail automation v1: ' + [string]$p.label
if ($null -ne $task) {
  if ($task.Definition.Principal.UserId -ne $sid -or $task.Definition.RegistrationInfo.Description -ne $marker) {
    throw 'Existing task does not belong to this Task Manager user and data directory.'
  }
}
if ($p.operation -eq 'disable') {
  if ($null -ne $task) { $folder.DeleteTask($name, 0) }
  @{installed=$false;active=$false} | ConvertTo-Json -Compress
  exit 0
}
$definitionMatches = $false
if ($null -ne $task) {
  $d = $task.Definition
  if ($d.Actions.Count -eq 1 -and $d.Triggers.Count -eq 2) {
    $a = $d.Actions.Item(1)
    $repeat = $d.Triggers.Item(1)
    $atLogon = $d.Triggers.Item(2)
    $definitionMatches = $a.Type -eq 0 -and $a.Path -eq $p.python -and $a.Arguments -ceq $p.arguments -and $a.WorkingDirectory -eq $p.cwd -and $d.Principal.LogonType -eq 3 -and $d.Principal.RunLevel -eq 0 -and $d.Settings.MultipleInstances -eq 2 -and $d.Settings.StartWhenAvailable -and $repeat.Type -eq 1 -and $repeat.Enabled -and $repeat.Repetition.Interval -eq 'PT1M' -and [string]::IsNullOrEmpty($repeat.Repetition.Duration) -and $atLogon.Type -eq 9 -and $atLogon.Enabled -and $atLogon.UserId -eq $sid
  }
}
$active = $null -ne $task -and $task.Enabled -and $task.State -ge 2 -and $definitionMatches
if ($p.operation -eq 'status' -or ($p.operation -eq 'enable' -and $active)) {
  @{installed=($null -ne $task);active=[bool]$active} | ConvertTo-Json -Compress
  exit 0
}
if ($p.operation -ne 'enable') { throw 'Unknown task operation.' }
$definition = $service.NewTask(0)
$definition.RegistrationInfo.Description = $marker
$definition.Principal.UserId = $sid
$definition.Principal.LogonType = 3
$definition.Principal.RunLevel = 0
$definition.Settings.Enabled = $true
$definition.Settings.StartWhenAvailable = $true
$definition.Settings.MultipleInstances = 2
$definition.Settings.ExecutionTimeLimit = 'PT30M'
$definition.Settings.DisallowStartIfOnBatteries = $false
$definition.Settings.StopIfGoingOnBatteries = $false
$trigger = $definition.Triggers.Create(1)
$trigger.StartBoundary = (Get-Date).AddSeconds(5).ToString('yyyy-MM-ddTHH:mm:ss')
$trigger.Repetition.Interval = 'PT1M'
$trigger.Enabled = $true
$logon = $definition.Triggers.Create(9)
$logon.UserId = $sid
$logon.Enabled = $true
$action = $definition.Actions.Create(0)
$action.Path = [string]$p.python
$action.Arguments = [string]$p.arguments
$action.WorkingDirectory = [string]$p.cwd
$registered = $folder.RegisterTaskDefinition($name, $definition, 6, $sid, $null, 3, $null)
@{installed=$true;active=[bool]($registered.Enabled -and $registered.State -ge 2)} | ConvertTo-Json -Compress
`;
  return ['-NoLogo', '-NoProfile', '-NonInteractive', '-EncodedCommand', Buffer.from(script, 'utf16le').toString('base64')];
}
const start = (file: string, args: string[], cwd: string) => new Promise<void>((done, fail) => {
  const child = spawn(file, args, {cwd, detached: true, stdio: 'ignore', windowsHide: true, env: {...process.env, PYTHONUTF8: '1', PYTHONDONTWRITEBYTECODE: '1'}});
  child.once('error', () => fail(new Error('无法启动邮件整理后台，请检查应用位置和 Python 运行时。')));
  child.once('spawn', () => {child.unref(); done();});
});

/** A user LaunchAgent owns short ticks; no credentials are put in the plist. */
export class MailAutomationHost implements AutomationHost {
  readonly label: string;
  readonly plistPath: string;
  private readonly platform: string;
  private readonly domain: string;
  private readonly execute: NonNullable<HostOptions['execute']>;
  private readonly query: NonNullable<HostOptions['query']>;
  private readonly start: NonNullable<HostOptions['start']>;
  private pending: Promise<unknown> = Promise.resolve();

  constructor(private directory: string, private runtime: AutomationRuntime, options: HostOptions = {}) {
    this.directory = resolve(directory);
    this.runtime = {python: resolve(runtime.python), backendDirectory: resolve(runtime.backendDirectory)};
    this.platform = options.platform ?? process.platform;
    this.domain = 'gui/' + (options.uid ?? process.getuid?.() ?? 0);
    this.label = 'com.gao.taskmanager.mail.' + createHash('sha256').update(this.platform === 'win32' ? this.directory.toLowerCase() : this.directory).digest('hex').slice(0, 16);
    this.plistPath = join(options.home ?? homedir(), 'Library', 'LaunchAgents', this.label + '.plist');
    this.execute = options.execute ?? execute;
    this.query = options.query ?? query;
    this.start = options.start ?? start;
  }

  private args() {return ['-B', '-u', '-m', 'task_backend.automation_worker', '--data-dir', this.directory, '--once'];}
  private async windowsTask(operation: 'status' | 'enable' | 'disable'): Promise<{installed: boolean; active: boolean}> {
    let python = this.runtime.python;
    // pythonw avoids a console flash each minute; the runtime also retains
    // python.exe for the normal IPC bridge and explicit verification commands.
    const hiddenPython = python.replace(/python\.exe$/i, 'pythonw.exe');
    if (hiddenPython !== python) try {await access(hiddenPython); python = hiddenPython;} catch { /* developer runtime */ }
    const args = ['-X', 'utf8', ...this.args()];
    const value = await this.query('powershell.exe', windowsCommand({operation, label: this.label,
      python, arguments: args.map(windowsArgument).join(' '), cwd: this.runtime.backendDirectory}));
    let result: unknown;
    try {result = JSON.parse(value.replace(/^\uFEFF/, ''));} catch {throw new Error('Windows 任务计划程序返回了无效状态。');}
    if (!result || typeof result !== 'object' || !('installed' in result) || !('active' in result)
      || typeof result.installed !== 'boolean' || typeof result.active !== 'boolean') throw new Error('Windows 任务计划程序返回了无效状态。');
    return {installed: result.installed, active: result.active};
  }
  private async loaded() {
    if (this.platform !== 'darwin') return false;
    try {await this.execute('/bin/launchctl', ['print', this.domain + '/' + this.label]); return true;} catch {return false;}
  }
  private serialize<T>(fn: () => Promise<T>): Promise<T> {
    const next = this.pending.then(fn, fn); this.pending = next.catch(() => {}); return next;
  }

  plist(): string {
    const args = [this.runtime.python, ...this.args()].map(value => '<string>' + xml(value) + '</string>').join('');
    return `<?xml version="1.0" encoding="UTF-8"?>\n<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">\n<plist version="1.0"><dict>
<key>Label</key><string>${xml(this.label)}</string>
<key>ProgramArguments</key><array>${args}</array>
<key>WorkingDirectory</key><string>${xml(this.runtime.backendDirectory)}</string>
<key>EnvironmentVariables</key><dict><key>PYTHONUTF8</key><string>1</string><key>PYTHONDONTWRITEBYTECODE</key><string>1</string></dict>
<key>RunAtLoad</key><true/><key>StartInterval</key><integer>60</integer>
<key>ProcessType</key><string>Background</string><key>LimitLoadToSessionType</key><string>Aqua</string>
<key>Umask</key><integer>63</integer>
</dict></plist>\n`;
  }

  async status(): Promise<MailAutomationBackgroundStatus> {
    if (this.platform === 'win32') {
      const state = await this.windowsTask('status');
      return {supported: true, ...state, message: state.active
        ? 'Windows 定时任务已启用；关闭应用后仍会检查计划。需保持当前用户登录，关机或休眠期间不能收信，恢复后补跑。'
        : state.installed ? 'Windows 定时任务尚未就绪或应用位置已改变；请重新保存启用设置。'
        : '尚未启用 Windows 定时任务；保存并启用后自动安装，无需管理员权限。'};
    }
    if (this.platform !== 'darwin') return {supported: false, installed: false, active: false, message: '系统定时后台支持 macOS 与 Windows；仍可立即整理。'};
    let installed = false;
    try {await access(this.plistPath); installed = true;} catch { /* Not yet opted in. */ }
    const active = installed && await this.loaded();
    return {supported: true, installed, active, message: active
      ? '系统后台已加载；关闭应用后仍会检查计划。关机或休眠期间不能收信，恢复运行后补跑；需保持当前用户登录且钥匙串可用。'
      : installed ? '后台配置存在，但系统尚未加载；请重新保存启用设置。' : '尚未启用系统后台；保存并启用后自动安装。'};
  }

  enable(): Promise<void> {return this.serialize(async () => {
    if (this.platform === 'win32') {
      await access(this.runtime.python);
      await access(join(this.runtime.backendDirectory, 'task_backend', 'automation_worker.py'));
      const result = await this.windowsTask('enable');
      if (!result.installed || !result.active) throw new Error('Windows 定时任务未能启用，请检查任务计划程序后重试。');
      return;
    }
    if (this.platform !== 'darwin') throw new Error('定时后台支持 macOS 与 Windows。');
    await access(this.runtime.python);
    await access(join(this.runtime.backendDirectory, 'task_backend', 'automation_worker.py'));
    const contents = this.plist();
    const previous = await readFile(this.plistPath, 'utf8').catch(() => '');
    const loaded = await this.loaded();
    if (loaded && previous === contents) return;
    if (loaded) await this.execute('/bin/launchctl', ['bootout', this.domain + '/' + this.label]);
    await mkdir(join(this.plistPath, '..'), {recursive: true});
    const temporary = this.plistPath + '.tmp';
    await writeFile(temporary, contents, {mode: 0o600});
    await rename(temporary, this.plistPath);
    try {await this.execute('/bin/launchctl', ['bootstrap', this.domain, this.plistPath]);}
    catch {await rm(this.plistPath, {force: true}); throw new Error('macOS 未能加载定时后台，自动整理已停用。请检查应用位置和系统后台权限后重试。');}
  });}

  disable(): Promise<void> {return this.serialize(async () => {
    if (this.platform === 'win32') {await this.windowsTask('disable'); return;}
    if (this.platform !== 'darwin') return;
    if (await this.loaded()) await this.execute('/bin/launchctl', ['bootout', this.domain + '/' + this.label]);
    await rm(this.plistPath, {force: true});
  });}

  async startOnce(): Promise<void> {
    await access(this.runtime.python);
    await access(join(this.runtime.backendDirectory, 'task_backend', 'automation_worker.py'));
    await this.start(this.runtime.python, this.args(), this.runtime.backendDirectory);
  }
}
