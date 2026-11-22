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
  start?: (file: string, args: string[], cwd: string) => Promise<void>;
}

const xml = (value: string) => value.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;').replace(/'/g, '&apos;');
const execute = (file: string, args: string[]) => new Promise<void>((done, fail) => {
  execFile(file, args, {timeout: 15000, maxBuffer: 128 * 1024}, error => error ? fail(new Error('系统后台服务操作失败。')) : done());
});
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
  private readonly start: NonNullable<HostOptions['start']>;
  private pending: Promise<unknown> = Promise.resolve();

  constructor(private directory: string, private runtime: AutomationRuntime, options: HostOptions = {}) {
    this.directory = resolve(directory);
    this.runtime = {python: resolve(runtime.python), backendDirectory: resolve(runtime.backendDirectory)};
    this.platform = options.platform ?? process.platform;
    this.domain = 'gui/' + (options.uid ?? process.getuid?.() ?? 0);
    this.label = 'com.gao.taskmanager.mail.' + createHash('sha256').update(this.directory).digest('hex').slice(0, 16);
    this.plistPath = join(options.home ?? homedir(), 'Library', 'LaunchAgents', this.label + '.plist');
    this.execute = options.execute ?? execute;
    this.start = options.start ?? start;
  }

  private args() {return ['-B', '-u', '-m', 'task_backend.automation_worker', '--data-dir', this.directory, '--once'];}
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
    if (this.platform !== 'darwin') return {supported: false, installed: false, active: false, message: '系统定时后台目前仅支持 macOS；仍可立即整理。'};
    let installed = false;
    try {await access(this.plistPath); installed = true;} catch { /* Not yet opted in. */ }
    const active = installed && await this.loaded();
    return {supported: true, installed, active, message: active
      ? '系统后台已加载；关闭应用后仍会检查计划。关机或休眠期间不能收信，恢复运行后补跑；需保持当前用户登录且钥匙串可用。'
      : installed ? '后台配置存在，但系统尚未加载；请重新保存启用设置。' : '尚未启用系统后台；保存并启用后自动安装。'};
  }

  enable(): Promise<void> {return this.serialize(async () => {
    if (this.platform !== 'darwin') throw new Error('定时后台目前仅支持 macOS。');
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
