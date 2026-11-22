import { spawn } from 'node:child_process';
import { once } from 'node:events';
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, rmSync, existsSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join, dirname, resolve } from 'node:path';
import { DatabaseSync } from 'node:sqlite';
import { randomUUID } from 'node:crypto';
import { TaskRepository } from '../src/main/repository';
import type { CanvasItem } from '../src/shared/canvas';
const source = 'a'.repeat(64);
function item(key: string, title=key, date='2026-11-01'): CanvasItem { return {key,title,description:'Canvas原始说明',url:'https://canvas.example/assignments/1',due:{mode:'value',value:{kind:'date',date}}}; }
// Windows runners may retain a directory entry briefly after database handles close.
// Retry only transient cleanup errors, with a finite deadline; never suppress failure.
function removeFixture(dir: string) {
 const delay=new Int32Array(new SharedArrayBuffer(4));
 for(let attempt=0;;attempt++) {
  try {rmSync(dir,{recursive:true,force:true});return;}
  catch(error) {
   if(attempt>=10 || !['EPERM','EBUSY','ENOTEMPTY'].includes((error as NodeJS.ErrnoException).code??''))throw error;
   Atomics.wait(delay,0,0,50*(attempt+1));
  }
 }
}
function fixture() { const dir=mkdtempSync(join(tmpdir(),'canvas-repo-'));return {path:join(dir,'todos.sqlite'),clean:()=>removeFixture(dir)}; }

test('Canvas重复与重启幂等，源标题日期更新保留本地备注完成子树，并报告修改', () => {
 const f=fixture();let repo=new TaskRepository(f.path);
 try {
  const first=repo.syncCanvas(source,[item('uid-one')]);assert.equal(first.added,1);
  const id=first.changes[0].id;const root=repo.get(id);
  assert.equal(root.notes,'');assert.equal(repo.getCanvasSource(id)?.description,'Canvas原始说明');
  const child=repo.create({title:'本地子项',parentId:id}).value;
  repo.update(id,{notes:'我的补充'},repo.get(id).revision);
  repo.setCompleted(id,true,repo.get(id).revision);
  repo.close();repo=new TaskRepository(f.path);
  assert.equal(repo.syncCanvas(source,[item('uid-one')]).unchanged,1);
  const report=repo.syncCanvas(source,[item('uid-one','老师改名','2026-12-02')]);
  assert.equal(report.updated,1);assert.equal(report.changes[0].previousTitle,'uid-one');
  assert.deepEqual(report.changes[0].previousDue,root.due);
  const latest=repo.get(id);assert.equal(latest.notes,'我的补充');assert.equal(latest.completed,true);assert.equal(repo.get(child.id).parentId,id);
  assert.equal(repo.list().length,2);assert.deepEqual(repo.getCanvasSourceIds(),[source]);
 } finally {repo.close();f.clean();}
});

test('缺项不删除，回收项不复活，源ID区分日历', () => {
 const f=fixture(),repo=new TaskRepository(f.path);
 try {
  const report=repo.syncCanvas(source,[item('x'),item('y')]);
  repo.syncCanvas(source,[]);assert.equal(repo.list().length,2);
  const id=report.changes[0].id;repo.trash(id,repo.get(id).revision);
  assert.equal(repo.syncCanvas(source,[item('x','新标题')]).skipped,1);assert.ok(repo.get(id).deletedAt);
  assert.equal(repo.syncCanvas('b'.repeat(64),[item('x')]).added,1);assert.equal(repo.list(true).length,3);
 } finally {repo.close();f.clean();}
});

test('无效整批与重复标识拒绝，真实SQL失败同时回滚事项及映射', () => {
 const f=fixture(),repo=new TaskRepository(f.path);
 try {
  assert.throws(()=>repo.syncCanvas(source,[item('x'),item('x')]));
  assert.throws(()=>repo.syncCanvas(source,[item('x'),item('y','坏日期','2026-02-30')]));
  assert.throws(()=>repo.syncCanvas('bad',[item('x')]));assert.equal(repo.list().length,0);
  const db=new DatabaseSync(f.path);db.exec("CREATE TRIGGER canvas_fail BEFORE INSERT ON todos WHEN NEW.title='失败' BEGIN SELECT RAISE(ABORT,'injected'); END;");
  assert.throws(()=>repo.syncCanvas(source,[item('x'),item('y','失败')]));
  assert.equal(repo.list().length,0);assert.equal(db.prepare('SELECT count(*) AS n FROM canvas_items').get()!.n,0);
  db.exec('DROP TRIGGER canvas_fail');db.close();assert.equal(repo.syncCanvas(source,[item('x')]).added,1);
 } finally {repo.close();f.clean();}
});

test('v5备份恢复来源映射避免重复，拒绝坏映射且不覆盖非空库', () => {
 const a=fixture(),b=fixture(),c=fixture();const repo=new TaskRepository(a.path),target=new TaskRepository(b.path),legacy=new TaskRepository(c.path);
 try {
  repo.syncCanvas(source,[item('x')]);const backup=repo.exportBackup();assert.equal(backup.version,6);if(backup.version!==6)throw Error('version');
  assert.throws(()=>target.importBackup({...backup,canvasItems:[{...backup.canvasItems[0],todoId:randomUUID()}]}));assert.equal(target.list().length,0);
  assert.throws(()=>target.importBackup({...backup,canvasItems:[backup.canvasItems[0],backup.canvasItems[0]]}));
  target.importBackup(backup);assert.equal(target.syncCanvas(source,[item('x')]).unchanged,1);assert.equal(target.list().length,1);
  assert.throws(()=>target.importBackup(backup));
  const {canvasItems,...v3}=backup;legacy.importBackup({...v3,version:3});assert.equal(legacy.list().length,1);assert.deepEqual(legacy.getCanvasSourceIds(),[]);
 } finally {repo.close();target.close();legacy.close();a.clean();b.clean();c.clean();}
});

test('真实v3升级到v5保留数据排序，损坏旧schema迁移不产生新表', () => {
 for(const bad of [false,true]) {
  const f=fixture();let repo=new TaskRepository(f.path);
  const root=repo.create({title:'旧记录'}).value;repo.setDayOrder('2026-11-01',[root.id]);repo.close();
  try {
   const old=new DatabaseSync(f.path);old.exec('DROP TABLE canvas_items; ALTER TABLE todos DROP COLUMN completed_at; PRAGMA user_version=3;');if(bad)old.exec('ALTER TABLE metadata ADD COLUMN extra TEXT');old.close();
   if(bad)assert.throws(()=>new TaskRepository(f.path));
   else {repo=new TaskRepository(f.path);assert.equal(repo.get(root.id).title,'旧记录');assert.deepEqual(repo.getDayOrder('2026-11-01'),[root.id]);repo.close();}
   const check=new DatabaseSync(f.path);assert.equal(check.prepare('PRAGMA user_version').get()!.user_version,bad?3:6);assert.equal(Boolean(check.prepare("SELECT name FROM sqlite_master WHERE name='canvas_items'").get()),!bad);check.close();
  } finally {f.clean();}
 }
});


test('长说明完整保存、更新来源不覆盖个人备注与完成状态，备份跨重启保留', () => {
 const a=fixture(),b=fixture();let repo=new TaskRepository(a.path);const restored=new TaskRepository(b.path);
 try {
  const original={...item('long'),description:'完整项目要求\n'.repeat(6000)};
  const id=repo.syncCanvas(source,[original]).changes[0].id;
  assert.equal(repo.get(id).notes,'');assert.deepEqual(repo.getCanvasSource(id),{description:original.description,url:original.url});
  repo.update(id,{notes:'我的独立备注'},repo.get(id).revision);repo.setCompleted(id,true,repo.get(id).revision);
  const changed={...original,description:original.description+'新的评分说明',url:'https://canvas.example/assignments/2'};
  const result=repo.syncCanvas(source,[changed]);assert.equal(result.updated,1);assert.equal(result.changes[0].descriptionChanged,true);assert.equal(result.changes[0].urlChanged,true);
  assert.equal(repo.get(id).notes,'我的独立备注');assert.equal(repo.get(id).completed,true);
  const backup=repo.exportBackup();restored.importBackup(backup);assert.deepEqual(restored.getCanvasSource(id),{description:changed.description,url:changed.url});
  repo.close();repo=new TaskRepository(a.path);assert.deepEqual(repo.getCanvasSource(id),restored.getCanvasSource(id));assert.equal(repo.syncCanvas(source,[changed]).unchanged,1);
  const local=repo.create({title:'本地'}).value;assert.equal(repo.getCanvasSource(local.id),null);
  repo.trash(id,repo.get(id).revision);assert.equal(repo.syncCanvas(source,[{...changed,description:'不要覆盖回收项'}]).skipped,1);assert.equal(repo.getCanvasSource(id)?.description,changed.description);
 } finally {repo.close();restored.close();a.clean();b.clean();}
});

test('真实v4升级v5保留个人数据，旧来源待下次同步补齐；坏映射回滚迁移', () => {
 for(const bad of [false,true]) {
  const f=fixture();let repo:TaskRepository|null=new TaskRepository(f.path);
  try {
   const id=repo.syncCanvas(source,[item('legacy')]).changes[0].id;
   repo.update(id,{notes:'旧导入说明或用户编辑均不可丢弃'},repo.get(id).revision);
   repo.setCompleted(id,true,repo.get(id).revision);
   // Force the timestamps apart so the migration fallback is always exercised.
   repo.update(id,{notes:'旧导入说明或用户编辑均不可丢弃'},repo.get(id).revision);
   const before=repo.get(id);assert.notEqual(before.completedAt,before.updatedAt);
   repo.close();repo=null;
   const db=new DatabaseSync(f.path);
   try {
    db.exec('ALTER TABLE canvas_items DROP COLUMN url; ALTER TABLE canvas_items DROP COLUMN description; ALTER TABLE todos DROP COLUMN completed_at; PRAGMA user_version=4;');
    if(bad)db.prepare('UPDATE canvas_items SET source_id=?').run('bad');
   } finally {db.close();}
   if(bad)assert.throws(()=>new TaskRepository(f.path));
   else {
    repo=new TaskRepository(f.path);
    // Old databases have no completion timestamp; the documented fallback is updatedAt.
    assert.deepEqual(repo.get(id),{...before,completedAt:before.updatedAt});
    assert.deepEqual(repo.getCanvasSource(id),{description:'',url:''});
    assert.equal(repo.syncCanvas(source,[item('legacy')]).updated,1);
    assert.equal(repo.get(id).notes,before.notes);assert.equal(repo.get(id).completed,true);assert.equal(repo.list().length,1);
    repo.close();repo=null;
   }
   const check=new DatabaseSync(f.path);
   try {
    assert.equal(check.prepare('PRAGMA user_version').get()!.user_version,bad?4:6);
    assert.equal(check.prepare('PRAGMA table_info(canvas_items)').all().length,bad?3:5);
   } finally {check.close();}
  } finally {repo?.close();f.clean();}
 }
});

test('v4旧备份来源映射可恢复，重新同步补齐说明但不复制为个人备注', () => {
 const a=fixture(),b=fixture();const repo=new TaskRepository(a.path),target=new TaskRepository(b.path);
 try {
  const id=repo.syncCanvas(source,[item('old-backup')]).changes[0].id;repo.update(id,{notes:'旧版备注内容'},repo.get(id).revision);
  const backup=repo.exportBackup();if(backup.version!==6)throw Error('version');
  target.importBackup({...backup,version:4,canvasItems:backup.canvasItems.map(({sourceId,key,todoId})=>({sourceId,key,todoId}))});
  assert.deepEqual(target.getCanvasSource(id),{description:'',url:''});assert.equal(target.syncCanvas(source,[item('old-backup')]).updated,1);assert.equal(target.get(id).notes,'旧版备注内容');assert.equal(target.list().length,1);
 } finally {repo.close();target.close();a.clean();b.clean();}
});


test('Windows短暂占用临时目录时，清理应等待句柄释放后成功', {skip:process.platform!=='win32'}, async () => {
 const f=fixture();
 writeFileSync(f.path,'temporary lock fixture');
 const script=[
  'import ctypes,sys,time',
  'from ctypes import wintypes',
  'kernel=ctypes.WinDLL("kernel32",use_last_error=True)',
  'kernel.CreateFileW.argtypes=[wintypes.LPCWSTR,wintypes.DWORD,wintypes.DWORD,ctypes.c_void_p,wintypes.DWORD,wintypes.DWORD,ctypes.c_void_p]',
  'kernel.CreateFileW.restype=wintypes.HANDLE',
  'kernel.CloseHandle.argtypes=[wintypes.HANDLE]',
  'handle=kernel.CreateFileW(sys.argv[1],0x80000000,1,None,3,0,None)',
  'assert handle not in (None,ctypes.c_void_p(-1).value), "directory lock failed"',
  'print("LOCKED",flush=True)',
  'time.sleep(0.4)',
  'kernel.CloseHandle(handle)',
 ].join('\n');
 const child=spawn(resolve('.venv/Scripts/python.exe'),['-u','-c',script,f.path],{windowsHide:true,stdio:['ignore','pipe','pipe']});
 const exited=once(child,'close');
 try {
  const ready=await Promise.race([once(child.stdout,'data'),exited.then(()=>{throw Error('lock process exited before ready');}),new Promise<never>((_,reject)=>{const timer=setTimeout(()=>reject(Error('lock process timeout')),5000);timer.unref();})]);
  assert.equal(String(ready[0]).trim(),'LOCKED');
  f.clean();
  assert.equal(existsSync(dirname(f.path)),false);
 } finally {
  await exited;
  f.clean();
 }
});
