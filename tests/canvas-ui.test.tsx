import React from 'react';
import { test, afterEach } from 'node:test';
import assert from 'node:assert/strict';
import { JSDOM } from 'jsdom';
import { createRequire } from 'node:module';
import { CanvasSync } from '../src/renderer/CanvasSync';
import type { DesktopAPI } from '../src/shared/contracts';
import type { CanvasSyncReport } from '../src/shared/canvas';
const dom = new JSDOM('<!doctype html><html><body></body></html>', {url:'http://localhost'});
for(const [key,value] of Object.entries({window:dom.window,document:dom.window.document,navigator:dom.window.navigator,HTMLElement:dom.window.HTMLElement})) Object.defineProperty(globalThis,key,{value,configurable:true});
const {render,fireEvent,waitFor,cleanup,within} = createRequire(process.cwd()+'/package.json')('@testing-library/react') as typeof import('@testing-library/react');
afterEach(()=>cleanup());
const ok=<T,>(data:T)=>({ok:true as const,data});
const empty:CanvasSyncReport={added:0,updated:0,unchanged:2,skipped:0,changes:[]};
function setup(overrides:Partial<DesktopAPI>={}) { window.desktop={getCanvasConfig:async()=>ok({configured:true,host:'canvas.example.edu'}),syncCanvas:async()=>ok(empty),...overrides} as DesktopAPI; }
const noop=()=>{};

test('已配置时单次打开自动同步一次，StrictMode不重复；更新在面板列出',async()=>{
 let calls=0,changed=0;
 const report:CanvasSyncReport={added:0,updated:1,unchanged:0,skipped:0,changes:[{id:'task-one',title:'新作业标题',kind:'updated',previousTitle:'旧标题',previousDue:{mode:'value',value:{kind:'date',date:'2026-11-01'}},due:{mode:'value',value:{kind:'date',date:'2026-11-03'}}}]};
 setup({syncCanvas:async()=>{calls++;return ok(report);}});
 const ui=render(<React.StrictMode><CanvasSync onClose={noop} onChanged={async()=>{changed++;}}/></React.StrictMode>);
 await waitFor(()=>assert.ok(ui.queryByLabelText('Canvas同步结果')));
 assert.equal(calls,1);assert.equal(changed,1);assert.ok(ui.getByText('新作业标题'));assert.ok(ui.getByText('已更新'));
 fireEvent.click(ui.getByText('查看变更'));assert.ok(ui.getByText('旧标题 → 新作业标题'));assert.ok(ui.getByText('2026-11-01 → 2026-11-03'));
 assert.equal(ui.container.querySelector('.notification'),null);assert.equal(ui.queryByLabelText('Calendar Feed 订阅链接'),null);
});

test('首次配置仅主动保存并同步，失败保留链接，错误不泄漏订阅地址',async()=>{
 let saved=0,synced=0,fail=true;
 const secret='https://canvas.example.edu/feeds/private-secret.ics';
 setup({getCanvasConfig:async()=>ok({configured:false,host:''}),saveCanvasFeed:async value=>{saved++;assert.equal(value,secret);return fail?{ok:false,error:'无法保存 '+secret}:ok({configured:true,host:'canvas.example.edu'});},syncCanvas:async()=>{synced++;return ok(empty);}});
 const ui=render(<CanvasSync onClose={noop} onChanged={async()=>{}}/>);
 await waitFor(()=>assert.ok(ui.queryByLabelText('Calendar Feed 订阅链接')));
 assert.equal(saved,0);assert.equal(synced,0);
 fireEvent.change(ui.getByLabelText('Calendar Feed 订阅链接'),{target:{value:secret}});fireEvent.click(ui.getByRole('button',{name:'保存并同步'}));
 await waitFor(()=>assert.ok(ui.queryByRole('alert')));
 assert.equal((ui.getByLabelText('Calendar Feed 订阅链接') as HTMLInputElement).value,secret);assert.equal(ui.getByRole('alert').textContent?.includes('private-secret'),false);assert.equal(synced,0);
 fail=false;fireEvent.click(ui.getByRole('button',{name:'保存并同步'}));
 await waitFor(()=>assert.ok(ui.queryByLabelText('Canvas同步结果')));assert.equal(synced,1);assert.equal(ui.queryByLabelText('Calendar Feed 订阅链接'),null);
});

test('同步失败可重试，不重复并发；移除订阅不删除本地待办',async()=>{
 let calls=0,clears=0;let release:()=>void=()=>{};
 setup({syncCanvas:async()=>{calls++;if(calls===1){await new Promise<void>(resolve=>{release=resolve;});return {ok:false,error:'网络暂时不可用'};}return ok(empty);},clearCanvasFeed:async()=>{clears++;return ok(undefined);}});
 const ui=render(<CanvasSync onClose={noop} onChanged={async()=>{}}/>);
 await waitFor(()=>assert.equal(calls,1));
 const button=ui.getByRole('button',{name:'正在同步…'}) as HTMLButtonElement;assert.equal(button.disabled,true);fireEvent.click(button);assert.equal(calls,1);
 release();await waitFor(()=>assert.ok(ui.queryByText('网络暂时不可用')));
 fireEvent.click(ui.getByRole('button',{name:'再次同步'}));await waitFor(()=>assert.ok(ui.queryByLabelText('Canvas同步结果')));assert.equal(calls,2);
 fireEvent.click(ui.getByRole('button',{name:'移除订阅'}));await waitFor(()=>assert.ok(ui.queryByLabelText('Calendar Feed 订阅链接')));assert.equal(clears,1);
});

test('未保存订阅的关闭保护、键盘焦点与关闭后归还焦点',async()=>{
 setup({getCanvasConfig:async()=>ok({configured:false,host:''})});let closes=0;
 const opener=document.createElement('button');document.body.append(opener);opener.focus();
 const ui=render(<CanvasSync onClose={()=>{closes++;}} onChanged={async()=>{}}/>);
 await waitFor(()=>assert.ok(ui.queryByLabelText('Calendar Feed 订阅链接')));
 fireEvent.change(ui.getByLabelText('Calendar Feed 订阅链接'),{target:{value:'https://example.edu/feed.ics'}});
 fireEvent.keyDown(document,{key:'Escape'});const confirmation=ui.getByRole('alertdialog');assert.ok(confirmation.contains(document.activeElement));
 fireEvent.keyDown(document,{key:'Tab',shiftKey:true});assert.equal(document.activeElement,within(confirmation).getByRole('button',{name:'放弃并关闭'}));
 fireEvent.click(within(confirmation).getByRole('button',{name:'继续填写'}));assert.equal(closes,0);
 fireEvent.click(ui.getByRole('button',{name:'关闭Canvas同步'}));fireEvent.click(ui.getByRole('button',{name:'放弃并关闭'}));assert.equal(closes,1);
 ui.unmount();assert.equal(document.activeElement,opener);opener.remove();
});


test('部分失败显示原因，来源说明和链接更新在同步结果内列出',async()=>{
 const report:CanvasSyncReport={added:1,updated:1,unchanged:0,skipped:1,issues:[{title:'异常事项',reason:'截止日期无法解析'}],changes:[{id:'one',title:'来源更新',kind:'updated',due:{mode:'none'},descriptionChanged:true,urlChanged:true}]};
 setup({syncCanvas:async()=>ok(report)});
 const ui=render(<CanvasSync onClose={noop} onChanged={async()=>{}}/>);
 await waitFor(()=>assert.ok(ui.queryByLabelText('Canvas同步结果')));
 assert.ok(ui.getByText('异常事项'));assert.ok(ui.getByText('截止日期无法解析'));assert.ok(ui.getByText('其他有效事项已保存。修正后可以再次同步。'));
 fireEvent.click(ui.getByText('查看变更'));assert.ok(ui.getByText('Canvas 说明已更新，可在任务详情中查看全文。'));assert.ok(ui.getByText('Canvas 原文地址已更新。'));
 assert.equal(ui.container.querySelector('.notification'),null);
});
