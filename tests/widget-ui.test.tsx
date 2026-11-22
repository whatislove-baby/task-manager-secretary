import React from 'react';
import { test, afterEach } from 'node:test';
import assert from 'node:assert/strict';
import { JSDOM } from 'jsdom';
import { createRequire } from 'node:module';
import { CrowWidget } from '../src/renderer/CrowWidget';
import { localDay } from '../src/shared/planner';
import type { DesktopAPI } from '../src/shared/contracts';
import type { Todo } from '../src/shared/todo';
const dom = new JSDOM('<!doctype html><html><body></body></html>', { url: 'http://localhost?widget=1' });
for (const [key, value] of Object.entries({ window: dom.window, document: dom.window.document, navigator: dom.window.navigator, HTMLElement: dom.window.HTMLElement })) Object.defineProperty(globalThis, key, { value, configurable: true });
const { render, fireEvent, waitFor, cleanup, within } = createRequire(process.cwd() + '/package.json')('@testing-library/react') as typeof import('@testing-library/react');
afterEach(() => cleanup());
function assertAbsent(value:Element|null) {assert.ok(value===null,'Expected DOM element to be absent');}

const ok = <T,>(data: T) => ({ ok:true as const, data });
let serial = 0;
function task(title:string, overrides:Partial<Todo> = {}):Todo { return {id:`00000000-0000-4000-8000-${String(++serial).padStart(12,'0')}`,parentId:null,title,notes:'原备注',completed:false,due:{mode:'value',value:{kind:'date',date:localDay(new Date())}},createdAt:'2026-01-01T00:00:00Z',updatedAt:'2026-01-01T00:00:00Z',revision:1,deletedAt:null,trashBatchId:null,...overrides}; }
function setup(items:Todo[], overrides:Partial<DesktopAPI>={}) {
  const listeners = new Set<() => void>();
  window.widget = {getState:async()=>({enabled:true}), setEnabled:async()=>{},setExpanded:async()=>{},close:async()=>{},showMain:async()=>{},quit:async()=>{},onChanged:callback=>{listeners.add(callback);return()=>{listeners.delete(callback);};}};
  window.desktop = {getCanvasSource:async()=>ok(null),listTodos:async()=>ok(structuredClone(items)),getDayOrder:async()=>ok([]),setCompleted:async(id,value)=>{const item=items.find(t=>t.id===id)!;item.completed=value;item.revision++;return ok({value:structuredClone(item),warnings:[]});},...overrides} as DesktopAPI;
  return () => { for(const listener of [...listeners]) listener(); };
}
test('乌鸦只计今日及无截止根，展开列表完成沉底，成功飞走并支持重新打开', async()=>{
  const done=task('已完成',{completed:true}), first=task('今日甲'), undated=task('无截止',{due:{mode:'none'}}), child=task('子项',{parentId:first.id,due:{mode:'inherit'}});
  setup([done,first,undated,child]); const ui=render(<CrowWidget/>);
  await waitFor(()=>assert.equal(ui.container.querySelectorAll('.crow-bird').length,2));
  fireEvent.click(ui.getByRole('button',{name:'展开今日待办'}));await waitFor(()=>assert.ok(ui.getByLabelText('今日待办列表')));
  assert.deepEqual(Array.from(ui.container.querySelectorAll('.crow-task-title')).map(n=>n.textContent),['今日甲','无截止','已完成']);
  fireEvent.click(ui.getByRole('button',{name:'完成 今日甲'}));
  await waitFor(()=>assert.equal(ui.container.querySelectorAll('.crow-flying').length,1));
  assert.ok(ui.container.querySelector('.crow-flying .crow-flight-sprite'));
  assertAbsent(ui.container.querySelector('.crow-flying img.crow-illustration'));
  assert.deepEqual(Array.from(ui.container.querySelectorAll('.crow-task-title')).map(n=>n.textContent),['无截止','已完成','今日甲']);
  await waitFor(()=>assert.equal(ui.container.querySelectorAll('.crow-bird').length,1),{timeout:1800});
  fireEvent.click(ui.getByRole('button',{name:'取消完成 今日甲'}));
  await waitFor(()=>assert.equal(ui.container.querySelectorAll('.crow-bird').length,2));
});
test('完成失败不触发飞走或沉底，错误在本窗显示',async()=>{
  setup([task('保留乌鸦')],{setCompleted:async()=>({ok:false,error:'保存失败'})});const ui=render(<CrowWidget/>);
  await waitFor(()=>assert.ok(ui.getByRole('button',{name:'查看任务 保留乌鸦'})));
  fireEvent.click(ui.getByRole('button',{name:'展开今日待办'}));await waitFor(()=>assert.ok(ui.getByLabelText('今日待办列表')));fireEvent.click(ui.getByRole('button',{name:'完成 保留乌鸦'}));
  await waitFor(()=>assert.equal(ui.getByRole('alert').textContent,'保存失败'));
  assert.equal(ui.container.querySelectorAll('.crow-flying').length,0);assert.equal(ui.container.querySelectorAll('.crow-bird').length,1);
});
test('乌鸦打开只读详情，编辑预填；跨窗更新保留草稿及原revision，冲突不覆盖输入',async()=>{
  const item=task('原标题');let usedRevision=0;
  const changed=setup([item],{editTodo:async(_id,_patch,_completed,revision)=>{usedRevision=revision;return{ok:false,error:'事项已变化，请重新选择。'};}});
  const ui=render(<CrowWidget/>);await waitFor(()=>assert.ok(ui.getByRole('button',{name:'查看任务 原标题'})));
  fireEvent.click(ui.getByRole('button',{name:'查看任务 原标题'}));await waitFor(()=>assert.ok(ui.getByLabelText('悬浮窗任务详情')));
  assertAbsent(ui.queryByLabelText('标题'));fireEvent.click(ui.getByRole('button',{name:'编辑任务'}));
  assert.equal((ui.getByLabelText('标题') as HTMLInputElement).value,'原标题');assert.equal((ui.getByLabelText('备注') as HTMLTextAreaElement).value,'原备注');
  fireEvent.change(ui.getByLabelText('标题'),{target:{value:'我的草稿'}});item.title='另一窗口修改';item.revision=2;changed();
  await waitFor(()=>assert.ok(ui.getByRole('button',{name:'查看任务 另一窗口修改'})));
  assert.equal((ui.getByLabelText('标题') as HTMLInputElement).value,'我的草稿');fireEvent.click(ui.getByRole('button',{name:'保存修改'}));
  await waitFor(()=>assert.equal(usedRevision,1));assert.equal((ui.getByLabelText('标题') as HTMLInputElement).value,'我的草稿');
  assert.ok(ui.getByRole('alert'));assertAbsent(ui.container.querySelector('.notification'));
});
test('外部完成触发飞走，关闭控件有独立入口，空状态是空枝',async()=>{
  const item=task('外部任务');const changed=setup([item]);const ui=render(<CrowWidget/>);
  await waitFor(()=>assert.equal(ui.container.querySelectorAll('.crow-bird').length,1));item.completed=true;changed();
  await waitFor(()=>assert.equal(ui.container.querySelectorAll('.crow-flying').length,1));
  assert.ok(ui.getByRole('button',{name:'关闭悬浮窗'}));fireEvent.contextMenu(ui.getByLabelText('乌鸦待办悬浮窗'));assert.ok(ui.getByRole('button',{name:'退出整个应用'}));
  await waitFor(()=>assert.equal(ui.container.querySelectorAll('.crow-bird').length,0),{timeout:1800});assertAbsent(ui.queryByText('今天的枝头很安静。'));
});


test('默认只留图案，点鸟仅显示单任务纸笺，挂饰切换列表，编辑中保护切换',async()=>{
  setup([task('枝头任务')]);const ui=render(<CrowWidget/>);
  await waitFor(()=>assert.ok(ui.getByRole('button',{name:'查看任务 枝头任务'})));
  assertAbsent(ui.container.querySelector('.crow-toolbar'));
  assert.equal(ui.container.querySelector('.crow-bird img')?.getAttribute('src'),'./art/raven-pencil.png');
  assert.equal(ui.container.querySelector('img.crow-branch')?.getAttribute('src'),'./art/branch-pencil.png');
  assertAbsent(ui.container.querySelector('.crow-bird svg'));
  assertAbsent(ui.queryByText(/未归巢/));
  assertAbsent(ui.queryByRole('button',{name:'退出整个应用'}));
  assert.equal(ui.getByRole('button',{name:'展开今日待办'}).textContent,'');
  fireEvent.click(ui.getByRole('button',{name:'查看任务 枝头任务'}));
  await waitFor(()=>assert.ok(ui.getByLabelText('悬浮窗任务详情')));
  assertAbsent(ui.queryByLabelText('今日待办列表'));
  assert.ok(ui.container.querySelector('.crow-paper-outline'));
  fireEvent.click(ui.getByRole('button',{name:'展开今日待办'}));
  await waitFor(()=>assert.ok(ui.getByLabelText('今日待办列表')));
  assertAbsent(ui.queryByLabelText('悬浮窗任务详情'));
  fireEvent.click(ui.getByRole('button',{name:'查看任务 枝头任务'}));
  await waitFor(()=>assert.ok(ui.getByLabelText('悬浮窗任务详情')));
  fireEvent.click(ui.getByRole('button',{name:'编辑任务'}));
  fireEvent.change(ui.getByLabelText('标题'),{target:{value:'未保存'}});
  fireEvent.click(ui.getByRole('button',{name:'展开今日待办'}));
  assertAbsent(ui.queryByLabelText('今日待办列表'));
  assert.equal((ui.getByLabelText('标题') as HTMLInputElement).value,'未保存');
  fireEvent.keyDown(ui.getByLabelText('乌鸦待办悬浮窗'),{key:'Escape'});
  assert.ok(ui.getByLabelText('悬浮窗任务详情'));
  fireEvent.click(ui.getByRole('button',{name:'取消'}));
  fireEvent.keyDown(ui.getByLabelText('乌鸦待办悬浮窗'),{key:'Escape'});
  await waitFor(()=>assert.ok(ui.queryByLabelText('悬浮窗任务详情') === null));
});


test('再次点击同一只乌鸦关闭任务详情，点击另一只切换详情',async()=>{
  setup([task('甲鸟'),task('乙鸟')]);const ui=render(<CrowWidget/>);
  await waitFor(()=>assert.equal(ui.container.querySelectorAll('.crow-bird').length,2));
  fireEvent.click(ui.getByRole('button',{name:'查看任务 甲鸟'}));
  await waitFor(()=>assert.ok(ui.getByLabelText('悬浮窗任务详情')));
  assert.ok(within(ui.getByLabelText('悬浮窗任务详情')).getByText('甲鸟'));
  fireEvent.click(ui.getByRole('button',{name:'查看任务 甲鸟'}));
  await waitFor(()=>assertAbsent(ui.queryByLabelText('悬浮窗任务详情')));
  assert.equal(ui.getByRole('button',{name:'展开今日待办'}).getAttribute('aria-expanded'),'false');
  fireEvent.click(ui.getByRole('button',{name:'查看任务 甲鸟'}));
  await waitFor(()=>assert.ok(ui.getByLabelText('悬浮窗任务详情')));
  fireEvent.click(ui.getByRole('button',{name:'查看任务 乙鸟'}));
  await waitFor(()=>assert.ok(ui.getByLabelText('悬浮窗任务详情')));
  assert.ok(within(ui.getByLabelText('悬浮窗任务详情')).getByText('乙鸟'));
});


test('树枝最多五只，多余待办只用一只盘旋鸟表示，完整列表不截断', async () => {
  const items = Array.from({ length: 8 }, (_, i) => task('数量任务' + i));
  const changed = setup(items); const ui = render(<CrowWidget/>);
  await waitFor(() => assert.equal(ui.container.querySelectorAll('.crow-bird').length, 5));
  assert.equal(ui.container.querySelectorAll('.crow-overflow').length, 1);
  assert.ok(ui.container.querySelector('.crow-overflow .crow-flight-sprite'));
  assertAbsent(ui.container.querySelector('.crow-overflow img'));
  fireEvent.click(ui.container.querySelector('.crow-overflow')!);
  await waitFor(() => assert.equal(ui.container.querySelectorAll('.crow-task').length, 8));
  items.slice(0, 3).forEach(item => { item.completed = true; item.revision++; }); changed();
  await waitFor(() => assert.ok(ui.container.querySelector('.crow-overflow') === null));
  await waitFor(() => assert.equal(ui.container.querySelectorAll('.crow-flying').length, 0), { timeout: 1800 });
  assert.equal(ui.container.querySelectorAll('.crow-bird').length, 5);
});


test('中间乌鸦完成后其他槽位不动，候补飞来落到原空位，标题常驻', async () => {
  const items = Array.from({ length: 6 }, (_, index) => task('固定站位' + index));
  const changed = setup(items); const ui = render(<CrowWidget/>);
  const node = (id: string) => Array.from(ui.container.querySelectorAll<HTMLElement>('.crow-slot')).find(slot => slot.querySelector('button')?.getAttribute('aria-label') === '查看任务 ' + id);
  await waitFor(() => assert.equal(ui.container.querySelectorAll('.crow-slot').length, 5));
  const initial = items.slice(0, 5).map(item => node(item.title)!.getAttribute('style'));
  assert.equal(ui.container.querySelectorAll('.crow-name-tag').length, 5);
  assert.equal(ui.container.querySelector('.crow-name-tag')?.textContent, items[0]!.title);
  items[2]!.completed = true; items[2]!.revision++; changed();
  await waitFor(() => assert.ok(node(items[2]!.title)?.querySelector('.crow-flying')));
  for (const index of [0,1,3,4]) assert.equal(node(items[index]!.title)!.getAttribute('style'), initial[index]);
  await waitFor(() => assert.ok(node(items[5]!.title)?.querySelector('.crow-incoming')), { timeout: 1800 });
  assert.equal(node(items[5]!.title)!.getAttribute('style'), initial[2]);
  for (const index of [0,1,3,4]) assert.equal(node(items[index]!.title)!.getAttribute('style'), initial[index]);
  await waitFor(() => assert.ok(!node(items[5]!.title)?.querySelector('.crow-incoming')), { timeout: 1800 });
  assert.ok(node(items[5]!.title)?.querySelector('img.crow-illustration'));
  items.reverse(); changed();
  await waitFor(() => assert.equal(ui.container.querySelectorAll('.crow-slot').length, 5));
  for (const index of [0,1,3,4]) assert.equal(node('固定站位'+index)!.getAttribute('style'), initial[index]);
});

test('拖乌鸦和叶子均移动窗口而不误触，轻点仍打开详情与列表', async () => {
  setup([task('拖动图案')]); const calls: number[][] = [];
  window.widget!.moveBy = async (x, y) => { calls.push([x,y]); };
  const ui = render(<CrowWidget/>);
  await waitFor(() => assert.equal(ui.container.querySelectorAll('.crow-bird').length, 1));
  function pointer(target: Element | Window, type: string, x: number, y: number) {
    const event = new dom.window.MouseEvent(type, { bubbles: true, cancelable: true, button: 0, screenX: x, screenY: y });
    Object.defineProperty(event, 'pointerId', { value: 1 }); fireEvent(target, event);
  }
  for (const selector of ['.crow-bird img','.crow-pendant path','.crow-name-tag']) {
    const target = ui.container.querySelector(selector)!;
    pointer(target, 'pointerdown', 100, 100); pointer(window, 'pointermove', 112, 106); pointer(window, 'pointerup', 112, 106);
    fireEvent.click(target, { detail: 1 });
    assert.ok(ui.queryByLabelText('悬浮窗任务详情') === null);
    assert.ok(ui.queryByLabelText('今日待办列表') === null);
  }
  await waitFor(() => assert.equal(calls.reduce((sum, delta) => sum + delta[0]!, 0), 36));
  assert.equal(calls.reduce((sum, delta) => sum + delta[1]!, 0), 18);
  const bird = ui.container.querySelector('.crow-bird')!;
  pointer(bird, 'pointerdown', 100, 100); pointer(window, 'pointerup', 100, 100); fireEvent.click(bird, { detail: 1 });
  await waitFor(() => assert.ok(ui.queryByLabelText('悬浮窗任务详情')));
  const leaf = ui.container.querySelector('.crow-pendant')!;
  pointer(leaf, 'pointerdown', 100, 100); pointer(window, 'pointerup', 100, 100); fireEvent.click(leaf, { detail: 1 });
  await waitFor(() => assert.ok(ui.queryByLabelText('今日待办列表')));
});


test('拖动释放失焦及detail0点击不展开，移动失败也不扩大占用范围', async () => {
  setup([task('拖动尺寸')]); let expandedCalls = 0;
  window.widget!.setExpanded = async () => { expandedCalls++; };
  window.widget!.moveBy = async () => { throw new Error('移动暂时失败'); };
  const ui = render(<CrowWidget/>);
  await waitFor(() => assert.ok(ui.container.querySelector('.crow-bird')));
  function pointer(target: Element | Window, type: string, x: number) {
    const event = new dom.window.MouseEvent(type, { bubbles: true, cancelable: true, button: 0, screenX: x, screenY: 80 });
    Object.defineProperty(event, 'pointerId', { value: 1 }); fireEvent(target, event);
  }
  const bird = ui.container.querySelector('.crow-bird')!;
  pointer(bird, 'pointerdown', 100); pointer(window, 'pointermove', 120);
  fireEvent.blur(window); pointer(window, 'pointerup', 120); fireEvent.click(bird, { detail: 0 });
  await waitFor(() => assert.ok(ui.queryByText('Error: 移动暂时失败')));
  assert.equal(expandedCalls, 0);
  assertAbsent(ui.container.querySelector('.crow-paper'));
  fireEvent.keyDown(bird, { key: 'Enter' }); fireEvent.click(bird, { detail: 0 });
  await waitFor(() => assert.ok(ui.container.querySelector('.crow-detail')));
  assert.equal(expandedCalls, 1);
});


test('DOM absence diagnostics remain bounded even with large attached state', () => {
 const element=document.createElement('div');
 Object.defineProperty(element,'attachedState',{value:'x'.repeat(8192),enumerable:true});
 let message='';
 try {assertAbsent(element);} catch(error) {message=(error as Error).message;}
 assert.ok(message.length>0&&message.length<200,`diagnostic length: ${message.length}`);
 assertAbsent(null);
});
