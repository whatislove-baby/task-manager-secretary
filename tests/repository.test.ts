import { test } from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { DatabaseSync } from 'node:sqlite';
import { TaskRepository } from '../src/main/repository';
import type { TodoDraft } from '../src/shared/todo';
function fixture() { const root = mkdtempSync(join(tmpdir(),'todo-repo-')); return { root, path:join(root,'tasks.sqlite'), cleanup:() => rmSync(root,{recursive:true,force:true}) }; }

test('任务、截止、完成配置、回收批次跨重启保持；重复恢复幂等', () => {
  const f=fixture(); let repo=new TaskRepository(f.path);
  try {
    repo.setConfig({cascadeComplete:false,autoCompleteParent:false});
    const parent=repo.create({title:'父',due:{mode:'value',value:{kind:'date',date:'2026-11-01'}}}).value;
    const child=repo.create({title:'子',parentId:parent.id,due:{mode:'inherit'}}).value;
    const batch=repo.trash(parent.id,repo.get(parent.id).revision).value;
    repo.close(); repo=new TaskRepository(f.path);
    assert.deepEqual(repo.getConfig(),{cascadeComplete:false,autoCompleteParent:false});
    assert.equal(repo.list().length,0); assert.equal(repo.list(true).length,2);
    assert.equal(repo.restore(batch).value.length,2); assert.equal(repo.restore(batch).value.length,0);
    assert.equal(repo.get(child.id).parentId,parent.id);
    assert.equal(repo.effectiveDue(child.id).sourceId,parent.id);
  } finally { repo.close(); f.cleanup(); }
});
test('revision 冲突和无效移动不改变数据库', () => {
  const f=fixture(); const repo=new TaskRepository(f.path);
  try {
    const parent=repo.create({title:'父'}).value;
    const child=repo.create({title:'子',parentId:parent.id}).value;
    repo.update(parent.id,{title:'改名'},repo.get(parent.id).revision);
    const before=repo.exportBackup().todos;
    assert.throws(() => repo.update(parent.id,{title:'陈旧'},parent.revision));
    assert.throws(() => repo.move(parent.id,child.id,repo.get(parent.id).revision));
    assert.deepEqual(repo.list(true),before);
  } finally { repo.close(); f.cleanup(); }
});
const drafts:TodoDraft[]=[{tempId:'child',parentTempId:'parent',title:'子',source:''},{tempId:'parent',parentTempId:null,title:'父',source:''}];
test('草稿拓扑保存、事务失败、令牌跨重启幂等与冲突', () => {
  const f=fixture(); let repo=new TaskRepository(f.path);
  try {
    const invalid=[...drafts,{tempId:'bad',parentTempId:'missing',title:'失败',source:''}];
    assert.throws(() => repo.saveDrafts(invalid,'failure')); assert.equal(repo.list(true).length,0);
    assert.throws(() => repo.saveDrafts([{tempId:'a',parentTempId:'b',title:'a',source:''},{tempId:'b',parentTempId:'a',title:'b',source:''}],'cycle'));
    assert.equal(repo.saveDrafts(drafts,'confirm').value,2);
    repo.close(); repo=new TaskRepository(f.path);
    assert.equal(repo.saveDrafts(drafts,'confirm').value,2); assert.equal(repo.list().length,2);
    assert.throws(() => repo.saveDrafts([{...drafts[0],title:'改变'},drafts[1]],'confirm'));
    assert.equal(repo.list().length,2);
  } finally { repo.close(); f.cleanup(); }
});
test('版本化备份完整恢复配置与回收，坏备份和非空导入不写入', () => {
  const a=fixture(), b=fixture(); const source=new TaskRepository(a.path), target=new TaskRepository(b.path);
  try {
    source.setConfig({cascadeComplete:false,autoCompleteParent:false});
    const node=source.create({title:'备份',notes:'保留',due:{mode:'value',value:{kind:'minute',at:'2026-11-01T10:15:00.000Z',timeZone:'Asia/Shanghai'}}}).value;
    source.trash(node.id,node.revision);
    const backup=source.exportBackup();
    assert.throws(() => target.importBackup({...backup,todos:[{...backup.todos[0],parentId:backup.todos[0].id}]}));
    assert.equal(target.list(true).length,0);
    assert.equal(target.importBackup(backup).value,1);
    assert.deepEqual(target.list(true),backup.todos); assert.deepEqual(target.getConfig(),backup.completionConfig);
    assert.throws(() => target.importBackup(backup));
    assert.equal(JSON.stringify(backup).includes('apiKey'),false);
  } finally { source.close();target.close();a.cleanup();b.cleanup(); }
});
test('旧版本、未知表、伪造 v2 和坏数据拒绝打开且保留原表', () => {
  for (const setup of ['CREATE TABLE tasks (id TEXT); PRAGMA user_version=1;', 'CREATE TABLE unknown (id TEXT);', 'CREATE TABLE todos (id TEXT); PRAGMA user_version=2;']) {
    const f=fixture();
    try {
      const db=new DatabaseSync(f.path);db.exec(setup);db.close();
      assert.throws(() => new TaskRepository(f.path));
      const check=new DatabaseSync(f.path);assert.equal(check.prepare("SELECT count(*) AS n FROM sqlite_master WHERE type='table'").get()!.n,1);check.close();
    } finally { f.cleanup(); }
  }
});
test('SQL 约束失败回滚整批任务与幂等令牌', () => {
  const f=fixture();const repo=new TaskRepository(f.path);
  try {
    // Inject a write failure after validation to verify actual SQLite rollback.
    const db=new DatabaseSync(f.path);
    db.exec("CREATE TRIGGER test_fail BEFORE INSERT ON todos WHEN NEW.title='子' BEGIN SELECT RAISE(ABORT,'injected failure'); END;");
    assert.throws(() => repo.saveDrafts(drafts,'retry'));
    assert.equal(repo.list(true).length,0);
    db.exec('DROP TRIGGER test_fail');db.close();
    assert.equal(repo.saveDrafts(drafts,'retry').value,2);
  } finally {repo.close();f.cleanup();}
});

test('完成时刻跨重启与 v6 备份保留', () => {
  const f=fixture(), other=fixture(); let repo=new TaskRepository(f.path); let target:TaskRepository|undefined;
  try {
    const task=repo.create({title:'无截止'}).value;
    const completed=repo.setCompleted(task.id,true,task.revision).value;
    assert.ok(completed.completedAt);
    const stored=repo.get(task.id).completedAt;
    repo.close(); repo=new TaskRepository(f.path);
    assert.equal(repo.get(task.id).completedAt,stored);
    const backup=repo.exportBackup(); assert.equal(backup.version,6);
    target=new TaskRepository(other.path);
    assert.equal(target.importBackup(backup).value,1);
    assert.equal(target.get(task.id).completedAt,stored);
  } finally { repo.close(); target?.close(); f.cleanup(); other.cleanup(); }
});

test('v5 数据库迁移为已完成项回填完成时间，重开后保持', () => {
  const f=fixture(); let repo=new TaskRepository(f.path);
  try {
    const task=repo.create({title:'旧完成'}).value;
    repo.setCompleted(task.id,true,task.revision);
    const updated=repo.get(task.id).updatedAt;
    repo.close();
    const db=new DatabaseSync(f.path);db.exec('ALTER TABLE todos DROP COLUMN completed_at; PRAGMA user_version=5;');db.close();
    repo=new TaskRepository(f.path);
    assert.equal(repo.get(task.id).completedAt,updated);
    const check=new DatabaseSync(f.path);assert.equal(check.prepare('PRAGMA user_version').get()!.user_version,6);check.close();
  } finally { repo.close(); f.cleanup(); }
});
