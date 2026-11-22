import { test } from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, rmSync, writeFileSync, existsSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { resolveDataDirectory, prepareDataDirectory } from '../src/main/storage';

test('开发、安装及便携路径使用明确应用目录，不依赖 cwd', () => {
  const base = join(tmpdir(),'explicit-app');
  assert.equal(resolveDataDirectory({ isPackaged:false, executablePath:process.execPath, projectDirectory:base }),join(base,'data'));
  assert.equal(resolveDataDirectory({ isPackaged:true, executablePath:join(base,'Task.exe'), projectDirectory:tmpdir() }),join(base,'data'));
  assert.equal(resolveDataDirectory({ isPackaged:true, executablePath:process.execPath, projectDirectory:tmpdir(), portableDirectory:base }),join(base,'data'));
  assert.throws(() => resolveDataDirectory({ isPackaged:false, executablePath:process.execPath, projectDirectory:'relative' }));
});
test('实际 SQLite 事务探测，清理探测文件；不可写路径明确失败', () => {
  const root = mkdtempSync(join(tmpdir(),'todo-storage-'));
  try {
    const data = join(root,'data'); prepareDataDirectory(data); assert.ok(existsSync(data));
    const blocker = join(root,'blocked'); writeFileSync(blocker,'file');
    assert.throws(() => prepareDataDirectory(join(blocker,'data')), /数据目录无法使用/);
    assert.equal(existsSync(join(root,'fallback')),false);
  } finally { rmSync(root,{recursive:true,force:true}); }
});
