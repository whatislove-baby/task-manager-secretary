import { spawn, execFile, type ChildProcessWithoutNullStreams } from 'node:child_process';
import { createInterface } from 'node:readline';
import { resolve } from 'node:path';
import type { Result } from '../shared/contracts';

interface Options { python?: string; backendDirectory?: string; timeoutMs?: number }
interface Pending { resolve: (value: Result<unknown>) => void; timer: ReturnType<typeof setTimeout> }

/** Sole owner of the Python subprocess. No fallback to the TypeScript database. */
export class PythonBackend {
  private child: ChildProcessWithoutNullStreams;
  private pending = new Map<number, Pending>();
  private sequence = 0;
  private stopped = false;
  private closed: Promise<void>;
  private timeoutMs: number;

  constructor(directory: string, options: Options = {}) {
    this.timeoutMs = options.timeoutMs ?? 90000;
    const backendDirectory = options.backendDirectory ?? resolve(__dirname, '../../backend');
    const projectPython = resolve(backendDirectory, '..', '.venv', process.platform === 'win32' ? 'Scripts/python.exe' : 'bin/python');
    this.child = spawn(options.python ?? process.env.TM_PYTHON ?? projectPython, ['-B', '-u', '-m', 'task_backend', '--data-dir', resolve(directory)], {
      cwd: backendDirectory,
      windowsHide: true, stdio: 'pipe', env: {...process.env, PYTHONUTF8: '1'},
    });
    this.closed = new Promise(done => this.child.once('close', () => {this.fail('Python 后端已停止，请重新启动应用。'); done();}));
    this.child.on('error', () => this.fail('无法启动 Python 后端，请检查 Python 安装及启动路径。'));
    this.child.stdin.on('error', () => this.fail('Python 后端通信已中断。'));
    // Never forward stderr: it may contain third-party diagnostic content.
    this.child.stderr.resume();
    const lines = createInterface({input: this.child.stdout});
    lines.on('line', line => {
      try {
        const response = JSON.parse(line);
        const entry = this.pending.get(response.id);
        if (!entry) return;
        if (!response.result || typeof response.result.ok !== 'boolean') throw new Error('Invalid response');
        clearTimeout(entry.timer); this.pending.delete(response.id); entry.resolve(response.result);
      } catch { this.fail('Python 后端响应格式不正确，请重新启动应用。'); this.child.kill(); }
    });
  }

  private fail(message: string) {
    this.stopped = true;
    for (const entry of this.pending.values()) {clearTimeout(entry.timer); entry.resolve({ok: false, error: message});}
    this.pending.clear();
  }

  call(method: string, ...args: unknown[]): Promise<Result<unknown>> {
    if (this.stopped) return Promise.resolve({ok: false, error: 'Python 后端不可用，请重新启动应用。'});
    const id = ++this.sequence;
    let request: string;
    try {request = JSON.stringify({id, method, args}) + '\n';}
    catch {return Promise.resolve({ok: false, error: '请求无法序列化。'});}
    if (Buffer.byteLength(request, 'utf8') > 2 * 1024 * 1024) return Promise.resolve({ok: false, error: '请求超过2MiB限制。'});
    return new Promise(done => {
      const timer = setTimeout(() => {
        this.fail('本地操作超时，保存结果尚未确认。请重新启动并检查任务，勿重复提交。');
        this.child.kill();
      }, /:(connect)$/.test(method) ? Math.max(this.timeoutMs,210000) : this.timeoutMs);
      this.pending.set(id, {resolve: done, timer});
      this.child.stdin.write(request, 'utf8', error => {if (error) this.fail('Python 后端通信已中断。');});
    });
  }

  async dispose(): Promise<void> {
    this.fail('应用正在退出。');
    this.child.stdin.end();
    const timer = setTimeout(() => {
      if (process.platform === 'win32' && this.child.pid) execFile('taskkill',['/PID',String(this.child.pid),'/T','/F'],{windowsHide:true},()=>{});
      else this.child.kill();
    }, 2000);
    try {await this.closed;} finally {clearTimeout(timer);}
  }
}
