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
const ok = <T,>(data: T) => ({ ok:true as const, data });
let serial = 0;
function task(title:string, overrides:Partial<Todo> = {}):Todo { return {id:`00000000-0000-4000-8000-${String(++serial).padStart(12,'0')}`,parentId:null,title,notes:'原备注',completed:false,due:{mode:'value',value:{kind:'date',date:localDay(new Date())}},createdAt:'2026-01-01T00:00:00Z',updatedAt:'2026-01-01T00:00:00Z',revision:1,deletedAt:null,trashBatchId:null,...overrides}; }
function setup(items:Todo[], overrides:Partial<DesktopAPI>={}) {
  const listeners = new Set<() => void>();
  window.widget = {getState:async()=>({enabled:true}), setEnabled:async()=>{},setExpanded:async()=>{},close:async()=>{},showMain:async()=>{},quit:async()=>{},onChanged:callback=>{listeners.add(callback);return()=>{listeners.delete(callback);};}};
  window.desktop = {
    getCanvasSource:async()=>ok(null),
    listTodos:async()=>ok(structuredClone(items)),
    getDayOrder:async()=>ok([]),
    setCompleted:async(id,value)=>{const item=items.find(t=>t.id===id)!;item.completed=value;item.revision++;return ok({value:structuredClone(item),warnings:[]});},
    editTodo:async(id,patch,completed,revision)=>{const item=items.find(t=>t.id===id)!;Object.assign(item,patch,{completed,revision:revision+1,updatedAt:'2026-01-02T00:00:00Z'});return ok({value:structuredClone(item),warnings:[]});},
    ...overrides,
  } as DesktopAPI;
  return () => { for(const listener of [...listeners]) listener(); };
}
async function openBird(ui:ReturnType<typeof render>, title:string) {
  await waitFor(()=>assert.ok(ui.getByRole('button',{name:'查看任务 '+title})));
  fireEvent.click(ui.getByRole('button',{name:'查看任务 '+title}));
  await waitFor(()=>assert.ok(ui.getByLabelText('悬浮窗任务详情')));
}
const subtaskSection = (ui:ReturnType<typeof render>) => ui.getByLabelText('子任务');
const rowTitles = (ui:ReturnType<typeof render>) => Array.from(subtaskSection(ui).querySelectorAll('.crow-subtask-title span')).map(node=>node.textContent);

test('多层子任务默认展开，可收起和再次展开', async()=>{
  const root=task('父任务'), child=task('子任务',{parentId:root.id,due:{mode:'inherit'}}), grand=task('孙任务',{parentId:child.id,due:{mode:'inherit'}});
  setup([root,child,grand]); const ui=render(<CrowWidget/>);
  await openBird(ui,'父任务');
  assert.deepEqual(rowTitles(ui),['父任务','子任务','孙任务']);
  fireEvent.click(within(subtaskSection(ui)).getByRole('button',{name:'折叠 子任务'}));
  assert.deepEqual(rowTitles(ui),['父任务','子任务']);
  assert.equal(within(subtaskSection(ui)).queryByText('孙任务'),null);
  fireEvent.click(within(subtaskSection(ui)).getByRole('button',{name:'展开 子任务'}));
  assert.deepEqual(rowTitles(ui),['父任务','子任务','孙任务']);
});

test('点击子任务查看详情，可从子任务区回到根任务与父任务', async()=>{
  const root=task('父任务'), child=task('子任务',{parentId:root.id,due:{mode:'inherit'}}), grand=task('孙任务',{parentId:child.id,due:{mode:'inherit'}});
  setup([root,child,grand]); const ui=render(<CrowWidget/>);
  await openBird(ui,'父任务');
  fireEvent.click(within(subtaskSection(ui)).getByRole('button',{name:/^子任务/}));
  await waitFor(()=>assert.equal(ui.container.querySelector('.crow-detail h3')?.textContent,'子任务'));
  assert.ok(ui.getByRole('button',{name:/返回父任务/}));
  fireEvent.click(within(subtaskSection(ui)).getByRole('button',{name:/^孙任务/}));
  await waitFor(()=>assert.equal(ui.container.querySelector('.crow-detail h3')?.textContent,'孙任务'));
  fireEvent.click(ui.getByRole('button',{name:/返回父任务/}));
  await waitFor(()=>assert.equal(ui.container.querySelector('.crow-detail h3')?.textContent,'子任务'));
  fireEvent.click(within(subtaskSection(ui)).getByRole('button',{name:/^父任务/}));
  await waitFor(()=>assert.equal(ui.container.querySelector('.crow-detail h3')?.textContent,'父任务'));
});

test('子任务行编辑预填原值，取消回到该子任务详情且不丢原值', async()=>{
  const root=task('父任务',{notes:'父备注'}), child=task('子任务',{parentId:root.id,notes:'子备注',due:{mode:'inherit'}});
  setup([root,child]); const ui=render(<CrowWidget/>);
  await openBird(ui,'父任务');
  fireEvent.click(within(subtaskSection(ui)).getByRole('button',{name:'编辑 子任务'}));
  const title=ui.getByLabelText('标题') as HTMLInputElement;
  assert.equal(title.value,'子任务');
  assert.equal((ui.getByLabelText('备注') as HTMLTextAreaElement).value,'子备注');
  fireEvent.change(title,{target:{value:'未保存的子任务标题'}});
  fireEvent.click(ui.getByRole('button',{name:'取消'}));
  await waitFor(()=>assert.equal(ui.queryByLabelText('标题'),null));
  assert.equal(ui.container.querySelector('.crow-detail h3')?.textContent,'子任务');
  fireEvent.click(within(subtaskSection(ui)).getByRole('button',{name:'编辑 子任务'}));
  assert.equal((ui.getByLabelText('标题') as HTMLInputElement).value,'子任务');
  assert.equal((ui.getByLabelText('备注') as HTMLTextAreaElement).value,'子备注');
});

test('没有子任务时不显示子任务区域', async()=>{
  setup([task('独立任务')]); const ui=render(<CrowWidget/>);
  await openBird(ui,'独立任务');
  assert.equal(ui.queryByLabelText('子任务'),null);
});

test('子任务完成圆点沿用 setCompleted 并以最新 revision 刷新', async()=>{
  const root=task('父任务',{revision:4}), child=task('子任务',{parentId:root.id,due:{mode:'inherit'},revision:2});
  const calls:{id:string;value:boolean;revision:number}[]=[];
  setup([root,child],{setCompleted:async(id,value,revision)=>{calls.push({id,value,revision});const item=[root,child].find(t=>t.id===id)!;item.completed=value;item.revision++;return ok({value:structuredClone(item),warnings:[]});}});
  const ui=render(<CrowWidget/>);
  await openBird(ui,'父任务');
  fireEvent.click(within(subtaskSection(ui)).getByRole('button',{name:'完成 子任务'}));
  await waitFor(()=>assert.deepEqual(calls,[{id:child.id,value:true,revision:2}]));
  await waitFor(()=>assert.equal(within(subtaskSection(ui)).getByRole('button',{name:'取消完成 子任务'}).getAttribute('aria-pressed'),'true'));
});

test('展开、查看和保存子任务编辑都不改变其他乌鸦站位', async()=>{
  const first=task('甲任务'), second=task('乙任务'), child=task('甲子任务',{parentId:first.id,notes:'子备注',due:{mode:'inherit'}});
  setup([first,second,child]); const ui=render(<CrowWidget/>);
  await waitFor(()=>assert.equal(ui.container.querySelectorAll('.crow-slot').length,2));
  const slots=()=>Array.from(ui.container.querySelectorAll('.crow-slot')).map(node=>node.getAttribute('style'));
  const before=slots();
  await openBird(ui,'甲任务');
  fireEvent.click(within(subtaskSection(ui)).getByRole('button',{name:'折叠 甲任务'}));
  assert.deepEqual(rowTitles(ui),['甲任务']);
  fireEvent.click(within(subtaskSection(ui)).getByRole('button',{name:'展开 甲任务'}));
  fireEvent.click(within(subtaskSection(ui)).getByRole('button',{name:'编辑 甲子任务'}));
  fireEvent.change(ui.getByLabelText('标题'),{target:{value:'甲子任务改'}});
  fireEvent.click(ui.getByRole('button',{name:'保存修改'}));
  await waitFor(()=>assert.ok(within(subtaskSection(ui)).getByText('甲子任务改')));
  assert.deepEqual(slots(),before);
  assert.equal(ui.container.querySelectorAll('.crow-bird').length,2);
});
