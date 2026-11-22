import { mkdirSync, mkdtempSync, rmSync } from 'node:fs';
import { dirname, isAbsolute, join, resolve } from 'node:path';
import { DatabaseSync } from 'node:sqlite';

export interface StorageLocation {
  isPackaged: boolean;
  executablePath: string;
  projectDirectory: string;
  /** Electron portable launcher must supply its original directory, not the temporary extraction path. */
  portableDirectory?: string;
}
export function resolveDataDirectory(options: StorageLocation): string {
  const base = options.isPackaged ? (options.portableDirectory ?? dirname(options.executablePath)) : options.projectDirectory;
  if (!isAbsolute(base)) throw new Error('应用或项目目录必须是绝对路径。');
  if (options.isPackaged && !options.portableDirectory && !isAbsolute(options.executablePath)) throw new Error('可执行文件路径必须是绝对路径。');
  return join(resolve(base), 'data');
}
/** Probe a real SQLite write, commit, and read in the requested directory. Never fallback. */
export function prepareDataDirectory(path: string): void {
  if (!isAbsolute(path)) throw new Error('数据目录必须是绝对路径。');
  let probe: string | undefined;
  let db: DatabaseSync | undefined;
  try {
    mkdirSync(path, { recursive: true });
    probe = mkdtempSync(join(path, '.write-check-'));
    db = new DatabaseSync(join(probe, 'probe.sqlite'));
    db.exec('BEGIN IMMEDIATE; CREATE TABLE probe(value TEXT NOT NULL); INSERT INTO probe VALUES (\'ok\'); COMMIT;');
    if (db.prepare('SELECT value FROM probe').get()?.value !== 'ok') throw new Error('SQLite 读写校验失败');
    db.close(); db = undefined;
  } catch (error) {
    throw new Error(`数据目录无法使用：${path}。请将应用移至可写目录，或修复该目录权限后重试。原因：${error instanceof Error ? error.message : String(error)}`, { cause:error });
  } finally {
    try { db?.close(); } finally { if (probe) rmSync(probe, { recursive:true, force:true }); }
  }
}
