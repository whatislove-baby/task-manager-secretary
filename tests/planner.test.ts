import { test } from 'node:test';
import assert from 'node:assert/strict';
import { randomUUID } from 'node:crypto';
import { projectPlanner, localDay, mondayOf, shiftDay } from '../src/shared/planner';
import type { Todo } from '../src/shared/todo';
function task(title:string, parentId:string|null = null, due:Todo['due']={mode:'none'}):Todo {
  return {id:randomUUID(),title,parentId,due,notes:'',completed:false,createdAt:'2026-01-01T00:00:00Z',updatedAt:'2026-01-01T00:00:00Z',revision:1,deletedAt:null,trashBatchId:null};
}
const date = (day:string):Todo['due'] => ({mode:'value',value:{kind:'date',date:day}});
test('左列只有当日与无截止根；周格只显示手设异日子，保留父标签和根定位', () => {
  const root=task('根',null,date('2026-11-02')), undated=task('无截止'), old=task('过期',null,date('2026-10-01'));
  const inherited=task('继承',root.id,{mode:'inherit'}), same=task('同日',root.id,date('2026-11-02'));
  const override=task('异日',root.id,date('2026-11-03')), none=task('截断',root.id), nested=task('嵌套',none.id,date('2026-11-02'));
  const p=projectPlanner([root,undated,old,inherited,same,override,none,nested]);
  assert.deepEqual(p.dailyRoots('2026-11-02').map(t=>t.title),['根','无截止']);
  assert.deepEqual(p.dailyRoots('2026-11-03').map(t=>t.title),['无截止']);
  assert.deepEqual(p.weekEntries('2026-11-02').map(e=>[e.todo.title,e.parentTitle,e.rootId]),[['根',null,root.id],['嵌套','截断',root.id]]);
  assert.deepEqual(p.weekEntries('2026-11-03').map(e=>e.todo.id),[override.id]);
  assert.equal(p.effectiveDeadline(inherited.id)?.kind,'date'); assert.equal(p.effectiveDeadline(none.id),null);
  assert.equal(p.rootId(nested.id),root.id); assert.equal(p.rootId('missing'),null);
  assert.deepEqual(p.dailyRoots('2026-11-02',[undated.id,root.id]).map(t=>t.id),[undated.id,root.id]);
  assert.deepEqual(p.dailyRoots('2026-11-02',[undated.id]).map(t=>t.id),[undated.id,root.id]);
});
test('分钟按本机日期投影，回收项不展示；快照隔离', () => {
  const at='2026-11-01T23:30:00Z';const root=task('分钟',null,{mode:'value',value:{kind:'minute',at,timeZone:'America/New_York'}});
  const deleted=task('删除');deleted.deletedAt=at;deleted.trashBatchId=randomUUID();
  const p=projectPlanner([root,deleted]);root.title='外部修改';
  const day=localDay(new Date(at)); assert.equal(p.weekEntries(day)[0].todo.title,'分钟');
  assert.equal(p.dailyRoots(day).length,1); assert.equal(p.weekEntries(shiftDay(day,1)).length,0);
  p.weekEntries(day)[0].todo.title='修改返回';assert.equal(p.weekEntries(day)[0].todo.title,'分钟');
});
test('日历跨年、跨周、闰日和无效输入；深树迭代解析', () => {
  assert.equal(mondayOf('2027-01-03'),'2026-12-28');assert.equal(shiftDay('2024-02-28',1),'2024-02-29');
  assert.equal(shiftDay('2026-12-31',1),'2027-01-01');assert.equal(shiftDay('2026-03-08',1),'2026-03-09');
  assert.throws(()=>shiftDay('2026-02-30',1));assert.throws(()=>mondayOf('oops'));
  const nodes=[task('根',null,date('2026-11-02'))];
  for(let i=0;i<10000;i++)nodes.push(task('子',nodes.at(-1)!.id,{mode:'inherit'}));
  const p=projectPlanner(nodes.reverse());assert.equal(p.rootId(nodes[0].id),nodes.at(-1)!.id);assert.equal(p.effectiveDeadline(nodes[0].id)?.kind,'date');
  assert.throws(()=>projectPlanner([nodes[0]]));
  const a=task('a'),b=task('b',a.id);a.parentId=b.id;assert.throws(()=>projectPlanner([a,b]));
});

test('本机时区跨日和 DST 按日历推进，不使用固定毫秒', () => {
  const before=process.env.TZ;process.env.TZ='America/New_York';
  try {
    assert.equal(localDay(new Date('2026-03-09T00:30:00Z')),'2026-03-08');
    assert.equal(shiftDay('2026-03-07',2),'2026-03-09');
    assert.equal(shiftDay('2026-11-01',1),'2026-11-02');
    const node=task('跨日',null,{mode:'value',value:{kind:'minute',at:'2026-03-09T00:30:00Z',timeZone:'Asia/Shanghai'}});
    assert.equal(projectPlanner([node]).weekEntries('2026-03-08')[0].todo.id,node.id);
    assert.equal(projectPlanner([node]).weekEntries('2026-03-09').length,0);
  } finally { if(before===undefined)delete process.env.TZ;else process.env.TZ=before; }
});


test('周格与同日列表共用排序，无截止不进入周格，独立子项按所属根排序且保持稳定', () => {
  const day='2026-11-02';
  const first=task('甲',null,date(day)), second=task('乙',null,date(day)), undated=task('无截止');
  const child=task('独立子项',undated.id,date(day));
  const other=task('异日',null,date('2026-11-03'));
  const p=projectPlanner([first,second,undated,child,other]);
  const order=[undated.id,second.id,first.id];
  assert.deepEqual(p.dailyRoots(day,order).map(t=>t.id),order);
  assert.deepEqual(p.weekEntries(day,order).map(e=>e.todo.id),[child.id,second.id,first.id]);
  assert.deepEqual(p.weekEntries(day,[second.id]).map(e=>e.todo.id),[second.id,first.id,child.id]);
  assert.deepEqual(p.weekEntries('2026-11-03',order).map(e=>e.todo.id),[other.id]);
  assert.deepEqual(p.weekEntries(day).map(e=>e.todo.id),[first.id,second.id,child.id]);
});

test('无截止任务未完成时每天出现，完成后只出现在完成当天，旧数据用更新时间兜底', () => {
  const undated = task('无截止');
  const done = task('已完成无截止'); done.completed = true; done.completedAt = new Date(2026, 10, 1, 10).toISOString();
  const before = task('前一天完成'); before.completed = true; before.completedAt = new Date(2026, 9, 31, 10).toISOString();
  const p = projectPlanner([undated, done, before]);
  assert.deepEqual(p.dailyRoots('2026-10-31').map(t => t.title), ['无截止', '前一天完成']);
  assert.deepEqual(p.dailyRoots('2026-11-01').map(t => t.title), ['无截止', '已完成无截止']);
  assert.deepEqual(p.dailyRoots('2026-11-02').map(t => t.title), ['无截止']);
  const legacy = task('旧数据完成'); legacy.completed = true; legacy.updatedAt = new Date(2026, 10, 1, 10).toISOString();
  assert.deepEqual(projectPlanner([legacy]).dailyRoots('2026-11-01').map(t => t.title), ['旧数据完成']);
  assert.deepEqual(projectPlanner([legacy]).dailyRoots('2026-11-02'), []);
});
