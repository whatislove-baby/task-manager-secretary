import { test } from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, readFileSync, rmSync, statSync, writeFileSync, existsSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { logWindowEvent, setWindowLogDirectory, windowLogPath } from '../src/main/window-log';

function tempDirectory(): string {
  return mkdtempSync(join(tmpdir(), 'task-manager-window-log-'));
}

test('未配置目录时不写文件也不抛错', () => {
  setWindowLogDirectory('');
  assert.equal(windowLogPath(), null);
  assert.doesNotThrow(() => logWindowEvent('app:start', { electron: 'test' }));
});

test('配置目录后按 JSON 行追加事件', () => {
  const directory = tempDirectory();
  try {
    setWindowLogDirectory(directory);
    assert.equal(windowLogPath(), join(directory, 'window.log'));
    logWindowEvent('window:create', { name: 'main', width: 1320 });
    logWindowEvent('page:show', { name: 'main', reason: 'ready-to-show' });
    const lines = readFileSync(join(directory, 'window.log'), 'utf8').trim().split('\n');
    assert.equal(lines.length, 2);
    const first = JSON.parse(lines[0]!);
    assert.equal(first.event, 'window:create');
    assert.equal(first.name, 'main');
    assert.equal(first.width, 1320);
    assert.match(first.at, /^\d{4}-\d{2}-\d{2}T/);
    assert.equal(JSON.parse(lines[1]!).reason, 'ready-to-show');
  } finally {
    setWindowLogDirectory('');
    rmSync(directory, { recursive: true, force: true });
  }
});

test('超过大小上限时轮换为 window.log.1 并继续写入', () => {
  const directory = tempDirectory();
  try {
    setWindowLogDirectory(directory);
    const file = windowLogPath()!;
    writeFileSync(file, 'x'.repeat(1024 * 1024), 'utf8');
    logWindowEvent('page:ready-to-show', { name: 'widget' });
    assert.equal(existsSync(file + '.1'), true);
    assert.equal(statSync(file + '.1').size, 1024 * 1024);
    assert.match(readFileSync(file, 'utf8'), /page:ready-to-show/);
  } finally {
    setWindowLogDirectory('');
    rmSync(directory, { recursive: true, force: true });
  }
});

test('目录不可创建时静默降级，不影响调用方', () => {
  const directory = tempDirectory();
  try {
    const blocker = join(directory, 'blocker');
    writeFileSync(blocker, 'not a directory', 'utf8');
    assert.doesNotThrow(() => setWindowLogDirectory(join(blocker, 'logs')));
    assert.equal(windowLogPath(), null);
    assert.doesNotThrow(() => logWindowEvent('app:start'));
  } finally {
    rmSync(directory, { recursive: true, force: true });
  }
});
