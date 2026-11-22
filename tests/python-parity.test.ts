import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdtempSync, rmSync} from 'node:fs';
import {tmpdir} from 'node:os';
import {join, resolve} from 'node:path';
import {TaskRepository} from '../src/main/repository';
import {PythonBackend} from '../src/main/python-backend';

function unwrap(result: any): any {assert.equal(result.ok, true, result.error); return result.data;}

test('actual TypeScript v6 database can be edited by Python and reopened by TypeScript', async () => {
  const directory = mkdtempSync(join(tmpdir(), 'tm-v6-parity-'));
  const file = join(directory, 'tasks.sqlite');
  let legacy: TaskRepository | null = new TaskRepository(file);
  let bridge: PythonBackend | null = null;
  try {
    const parent = legacy.create({title:'parent', notes:'preserve me'}).value;
    const child = legacy.create({title:'child',parentId:parent.id}).value;
    legacy.syncCanvas('a'.repeat(64), [{key:'event',title:'course',description:'full source',url:'https://example.com/event',due:{mode:'value',value:{kind:'date',date:'2026-11-12'}}}]);
    const sourceTodo = legacy.list().find(t => t.title === 'course')!;
    legacy.close(); legacy = null;
    bridge = new PythonBackend(directory, {backendDirectory:resolve('backend')});
    const todos = unwrap(await bridge.call('listTodos'));
    const root = todos.find((t:any) => t.id === parent.id);
    unwrap(await bridge.call('editTodo', parent.id, {title:'Python edited'}, true, root.revision));
    await bridge.dispose(); bridge = null;
    legacy = new TaskRepository(file);
    assert.equal(legacy.get(parent.id).title, 'Python edited');
    assert.equal(legacy.get(parent.id).notes, 'preserve me');
    assert.equal(legacy.get(child.id).completed, true);
    assert.deepEqual(legacy.getCanvasSource(sourceTodo.id), {description:'full source',url:'https://example.com/event'});
  } finally {legacy?.close(); await bridge?.dispose(); rmSync(directory,{recursive:true,force:true});}
});

test('Python-created database meets the original repository schema and behavior', async () => {
  const directory = mkdtempSync(join(tmpdir(), 'tm-python-schema-'));
  const bridge = new PythonBackend(directory, {backendDirectory:resolve('backend')});
  try {
    const root = unwrap(await bridge.call('createTodo', {title:'Python root'})).value;
    unwrap(await bridge.call('createTodo', {title:'child',parentId:root.id}));
    await bridge.dispose();
    const legacy = new TaskRepository(join(directory,'tasks.sqlite'));
    try {
      assert.equal(legacy.list().length, 2);
      const current = legacy.get(root.id);
      legacy.setCompleted(root.id, true, current.revision);
      assert.ok(legacy.list().every(t=>t.completed));
    } finally {legacy.close();}
  } finally {await bridge.dispose(); rmSync(directory,{recursive:true,force:true});}
});

test('draft confirmation fingerprints are compatible across Python and the original backend', async () => {
  const directory=mkdtempSync(join(tmpdir(),'tm-draft-parity-'));
  let legacy:TaskRepository|null=new TaskRepository(join(directory,'tasks.sqlite'));
  let bridge:PythonBackend|null=null;
  const drafts=[{tempId:'child',parentTempId:'root',title:'子任务',source:'',notes:'note',due:{mode:'inherit' as const}}, {tempId:'root',parentTempId:null,title:'root',source:'literal'}];
  try {
    assert.equal(legacy.saveDrafts(drafts,'old-token').value,2);
    legacy.close();legacy=null;
    bridge=new PythonBackend(directory,{backendDirectory:resolve('backend')});
    assert.equal(unwrap(await bridge.call('saveDrafts',drafts,'old-token')).value,2);
    assert.equal(unwrap(await bridge.call('saveDrafts',drafts,'python-token')).value,2);
    await bridge.dispose();bridge=null;
    legacy=new TaskRepository(join(directory,'tasks.sqlite'));
    assert.equal(legacy.saveDrafts(drafts,'python-token').value,2);
    assert.equal(legacy.list().length,4);
  } finally {legacy?.close();await bridge?.dispose();rmSync(directory,{recursive:true,force:true});}
});
