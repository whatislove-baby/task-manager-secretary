import { test } from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { DatabaseSync } from 'node:sqlite';
import { TaskRepository } from '../src/main/repository';
function fixture() {const dir=mkdtempSync(join(tmpdir(),'todo-order-'));return {path:join(dir,'data.sqlite'),clean:()=>rmSync(dir,{recursive:true,force:true})};}
test('日排序独立持久化、备份恢复；排序不修改任务且拒绝陈旧列表',()=>{
  const a=fixture(),b=fixture();let repo=new TaskRepository(a.path);const target=new TaskRepository(b.path);
  try{
    const first=repo.create({title:'一'}).value,second=repo.create({title:'二'}).value;
    const child=repo.create({title:'子',parentId:first.id}).value;
    const before=repo.list(true);const day='2026-11-02';
    repo.setDayOrder(day,[second.id,first.id]);assert.deepEqual(repo.list(true),before);
    assert.throws(()=>repo.setDayOrder(day,[first.id,first.id]));assert.throws(()=>repo.setDayOrder(day,[child.id,first.id]));
    assert.throws(()=>repo.setDayOrder('2026-02-30',[]));assert.throws(()=>repo.setDayOrder(day,[first.id]));
    assert.deepEqual(repo.getDayOrder('2026-11-03'),[]);
    const blocked=new DatabaseSync(a.path);
    blocked.exec("CREATE TRIGGER fail_order BEFORE UPDATE ON day_orders BEGIN SELECT RAISE(ABORT,'injected'); END;");
    assert.throws(()=>repo.setDayOrder(day,[first.id,second.id]));
    assert.deepEqual(repo.getDayOrder(day),[second.id,first.id]);
    blocked.exec('DROP TRIGGER fail_order');blocked.close();
    repo.close();repo=new TaskRepository(a.path);assert.deepEqual(repo.getDayOrder(day),[second.id,first.id]);
    const backup=repo.exportBackup();assert.equal(backup.version,6);target.importBackup(backup);assert.deepEqual(target.getDayOrder(day),[second.id,first.id]);
    repo.create({title:'新'});assert.throws(()=>repo.setDayOrder(day,[second.id,first.id]),/列表已变化/);
    assert.deepEqual(repo.getDayOrder(day),[second.id,first.id]);
    repo.move(second.id,first.id,repo.get(second.id).revision);repo.close();repo=new TaskRepository(a.path);assert.deepEqual(repo.getDayOrder(day),[first.id]);
  }finally{repo.close();target.close();a.clean();b.clean();}
});
test('v2 完整 schema 和任务通过事务迁移，坏 v2 保持原版本',()=>{
  const f=fixture();let repo=new TaskRepository(f.path);
  try{
    const task=repo.create({title:'旧任务'}).value;repo.close();
    const old=new DatabaseSync(f.path);old.exec('DROP TABLE canvas_items; DROP TABLE day_orders; ALTER TABLE todos DROP COLUMN completed_at; PRAGMA user_version=2;');old.close();
    repo=new TaskRepository(f.path);assert.equal(repo.get(task.id).title,'旧任务');assert.deepEqual(repo.getDayOrder('2026-11-02'),[]);repo.close();
    const db=new DatabaseSync(f.path);assert.equal(db.prepare('PRAGMA user_version').get()!.user_version,6);db.exec('DROP TABLE canvas_items; DROP TABLE day_orders; ALTER TABLE metadata ADD COLUMN invalid TEXT; PRAGMA user_version=2;');db.close();
    assert.throws(()=>new TaskRepository(f.path));
    const check=new DatabaseSync(f.path);assert.equal(check.prepare('PRAGMA user_version').get()!.user_version,2);assert.equal(check.prepare("SELECT name FROM sqlite_master WHERE name='day_orders'").get(),undefined);check.close();
  }finally{f.clean();}
});
test('v2 备份兼容，v3 拒绝坏顺序；允许历史已改期根 ID',()=>{
  const a=fixture(),b=fixture();const repo=new TaskRepository(a.path),target=new TaskRepository(b.path);
  try{
    const root=repo.create({title:'根'}).value;const child=repo.create({title:'子',parentId:root.id}).value;
    repo.setDayOrder('2026-11-02',[root.id]);repo.update(root.id,{due:{mode:'value',value:{kind:'date',date:'2026-12-01'}}},repo.get(root.id).revision);
    const backup=repo.exportBackup();assert.equal(backup.version,6);
    if(backup.version!==6)throw new Error('version');
    assert.throws(()=>target.importBackup({...backup,dayOrders:[{day:'2026-11-02',ids:[child.id]}]}));assert.equal(target.list().length,0);
    assert.throws(()=>target.importBackup({...backup,dayOrders:[...backup.dayOrders,...backup.dayOrders]}));
    target.importBackup(backup);assert.deepEqual(target.getDayOrder('2026-11-02'),[root.id]);
    const f=fixture();const legacy=new TaskRepository(f.path);
    try{const {dayOrders,canvasItems,...fields}=backup;legacy.importBackup({...fields,version:2});assert.deepEqual(legacy.list(true),backup.todos);assert.deepEqual(legacy.getDayOrder('2026-11-02'),[]);}finally{legacy.close();f.clean();}
  }finally{repo.close();target.close();a.clean();b.clean();}
});
