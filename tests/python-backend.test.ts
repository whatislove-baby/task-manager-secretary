import assert from 'node:assert/strict';
import test from 'node:test';
import { mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join, resolve } from 'node:path';
import { PythonBackend } from '../src/main/python-backend';

test('Python process bridge saves tasks and rejects calls after disposal', async () => {
  const directory = mkdtempSync(join(tmpdir(), 'tm-python-'));
  const bridge = new PythonBackend(directory, { backendDirectory: resolve('backend') });
  try {
    const health = await bridge.call('health');
    assert.equal(health.ok, true);
    if (health.ok) {
      const info = health.data as {environment:{executable:string;prefix:string;basePrefix:string}};
      const expectedPython = resolve('.venv', process.platform === 'win32' ? 'Scripts/python.exe' : 'bin/python');
      assert.equal(resolve(info.environment.executable).toLowerCase(), expectedPython.toLowerCase());
      assert.notEqual(info.environment.prefix, info.environment.basePrefix);
    }
    const created = await bridge.call('createTodo', {title: '桥接测试'});
    assert.equal(created.ok, true);
    const listed = await bridge.call('listTodos');
    assert.equal(listed.ok, true);
    if (listed.ok) assert.equal((listed.data as any[])[0].title, '桥接测试');
    const rejected = await bridge.call('notAllowed');
    assert.equal(rejected.ok, false);
    assert.equal((await bridge.call('listTodos')).ok, true);
  } finally {
    await bridge.dispose();
    rmSync(directory, {recursive: true, force: true});
  }
  assert.equal((await bridge.call('listTodos')).ok, false);
});

test('missing Python executable fails pending calls without hanging', async () => {
  const bridge = new PythonBackend(tmpdir(), { python: 'missing-python-tm-test-123.exe', backendDirectory: resolve('backend') });
  try { assert.equal((await bridge.call('health')).ok, false); }
  finally { await bridge.dispose(); }
});
