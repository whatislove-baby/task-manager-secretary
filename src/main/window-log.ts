// 窗口生命周期日志：只追加本地文件；未配置目录或写盘失败时静默跳过，绝不影响窗口。
import { appendFileSync, existsSync, mkdirSync, renameSync, rmSync, statSync } from 'node:fs';
import { join } from 'node:path';

const MAX_BYTES = 1024 * 1024;
let directory = '';

export function setWindowLogDirectory(path: string): void {
  try {
    mkdirSync(path, { recursive: true });
    directory = path;
  } catch {
    directory = '';
  }
}

export function windowLogPath(): string | null {
  return directory ? join(directory, 'window.log') : null;
}

/** 追加一条事件；字段只放窗口类型、尺寸、事件名和错误码，不放业务内容。 */
export function logWindowEvent(event: string, details: Record<string, unknown> = {}): void {
  const file = windowLogPath();
  if (!file) return;
  try {
    if (existsSync(file) && statSync(file).size >= MAX_BYTES) {
      const previous = file + '.1';
      rmSync(previous, { force: true });
      renameSync(file, previous);
    }
    appendFileSync(file, JSON.stringify({ at: new Date().toISOString(), event, ...details }) + '\n', 'utf8');
  } catch {
    // 诊断日志不能反过来影响窗口。
  }
}
