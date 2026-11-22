import React from 'react';
import { test, afterEach } from 'node:test';
import assert from 'node:assert/strict';
import { JSDOM } from 'jsdom';
import { createRequire } from 'node:module';
import { CanvasSource } from '../src/renderer/CanvasSource';
import type { DesktopAPI } from '../src/shared/contracts';
import type { WidgetAPI } from '../src/shared/widget';
const dom = new JSDOM('<!doctype html><html><body></body></html>', {url:'http://localhost'});
for(const [key,value] of Object.entries({window:dom.window,document:dom.window.document,navigator:dom.window.navigator,HTMLElement:dom.window.HTMLElement})) Object.defineProperty(globalThis,key,{value,configurable:true});
const {render,fireEvent,waitFor,cleanup,within} = createRequire(process.cwd()+'/package.json')('@testing-library/react') as typeof import('@testing-library/react');
afterEach(()=>{cleanup();delete window.widget;});
const ok=<T,>(data:T)=>({ok:true as const,data});

test('完整长说明独立阅读、纯文本安全呈现、复制与原文，关闭归还焦点',async()=>{
 const text='项目说明\n'+'Long description '.repeat(1500)+'<script>danger()</script>';
 let opened='',copied='',moves=0,outerEscape=0;
 Object.defineProperty(navigator,'clipboard',{configurable:true,value:{writeText:async(value:string)=>{copied=value;}}});
 window.desktop={getCanvasSource:async()=>ok({description:text,url:'https://canvas.example.edu/courses/1/assignments/2'}),openCanvasOriginal:async (id:string)=>{opened=id;return ok(undefined);}} as unknown as DesktopAPI;
 const ui=render(<div onPointerDown={()=>{moves++;}} onKeyDown={event=>{if(event.key==='Escape')outerEscape++;}}><CanvasSource todoId="one" title="Tokenizer" revision={1}/></div>);
 await waitFor(()=>assert.ok(ui.queryByRole('button',{name:/查看说明/})));
 assert.ok(ui.queryByRole('dialog') === null);assert.equal(ui.container.textContent?.includes(text),false);
 const opener=ui.getByRole('button',{name:/查看说明/});opener.focus();fireEvent.click(opener);
 const dialog=ui.getByRole('dialog');assert.ok(ui.getByLabelText('完整Canvas说明').textContent === text);assert.equal(dialog.querySelector('script'),null);
 assert.ok(dialog.contains(document.activeElement));assert.equal(ui.container.inert,true);
 fireEvent.pointerDown(ui.getByLabelText('完整Canvas说明'));assert.equal(moves,0);
 fireEvent.keyDown(document,{key:'Tab',shiftKey:true});assert.ok(document.activeElement===within(dialog).getByRole('button',{name:'在 Canvas 中打开 ↗'}));
 fireEvent.keyDown(document,{key:'Tab'});assert.ok(document.activeElement===within(dialog).getByRole('button',{name:'关闭事项说明'}));
 fireEvent.click(within(dialog).getByRole('button',{name:'复制全文'}));await waitFor(()=>assert.equal(copied,text));
 fireEvent.click(within(dialog).getByRole('button',{name:'在 Canvas 中打开 ↗'}));await waitFor(()=>assert.equal(opened,'one'));
 fireEvent.keyDown(document,{key:'Escape'});assert.ok(ui.queryByRole('dialog') === null);assert.ok(document.activeElement===opener);assert.ok(!ui.container.inert);assert.equal(outerEscape,0);
});

test('切换任务时不显示旧说明，过期请求不可覆盖新任务',async()=>{
 let resolve:(value:ReturnType<typeof ok<{description:string;url:string}>>) =>void=()=>{};
 window.desktop={getCanvasSource:async (id:string)=>id==='one'?await new Promise(done=>{resolve=done;}):ok({description:'新任务说明',url:''})} as unknown as DesktopAPI;
 const ui=render(<CanvasSource todoId="one" title="旧任务" revision={1}/>);
 ui.rerender(<CanvasSource todoId="two" title="新任务" revision={1}/>);
 await waitFor(()=>assert.ok(ui.queryByRole('button',{name:/查看说明/})));
 resolve(ok({description:'旧任务说明',url:''}));fireEvent.click(ui.getByRole('button',{name:/查看说明/}));
 await waitFor(()=>assert.equal(ui.getByLabelText('完整Canvas说明').textContent,'新任务说明'));
 assert.ok(ui.queryByText('旧任务说明') === null);assert.ok(ui.queryByRole('button',{name:'在 Canvas 中打开 ↗'}) === null);
});

test('来源独立更新后刷新，失败留在局部并可再次收到变更',async()=>{
 let changed=()=>{},description='旧说明',reads=0;
 window.widget={onChanged:callback=>{changed=callback;return ()=>{};}} as WidgetAPI;
 window.desktop={getCanvasSource:async()=>{reads++;return ok({description,url:'https://canvas.example.edu/item'});},openCanvasOriginal:async()=>({ok:false,error:'无法打开'})} as unknown as DesktopAPI;
 const ui=render(<CanvasSource todoId="one" title="作业" revision={1}/>);
 await waitFor(()=>assert.ok(ui.queryByRole('button',{name:/查看说明/})));
 fireEvent.click(ui.getByRole('button',{name:/查看说明/}));description='新的完整来源说明';changed();
 await waitFor(()=>assert.equal(ui.getByLabelText('完整Canvas说明').textContent,description));assert.equal(reads,2);
 const dialog=ui.getByRole('dialog');fireEvent.click(within(dialog).getByRole('button',{name:'在 Canvas 中打开 ↗'}));
 await waitFor(()=>assert.ok(within(dialog).queryByRole('alert')));assert.equal(ui.container.querySelector('.notification'),null);
});
