import React from 'react';
import { test, afterEach } from 'node:test';
import assert from 'node:assert/strict';
import { JSDOM } from 'jsdom';
import { createRequire } from 'node:module';
import { Tools } from '../src/renderer/Tools';
import type { DesktopAPI } from '../src/shared/contracts';
import type { MailAnalysis, MailWorkspaceAPI } from '../src/shared/mail-workspace';
import type { TodoDraft } from '../src/shared/todo';
const dom=new JSDOM('<!doctype html><html><body></body></html>',{url:'http://localhost'});
for(const [key,value] of Object.entries({window:dom.window,document:dom.window.document,navigator:dom.window.navigator,HTMLElement:dom.window.HTMLElement}))Object.defineProperty(globalThis,key,{value,configurable:true});
const {render,fireEvent,waitFor,cleanup}=createRequire(process.cwd()+'/package.json')('@testing-library/react') as typeof import('@testing-library/react');
afterEach(()=>cleanup());
const ok=<T,>(data:T)=>({ok:true as const,data});
window.desktop={getSettings:async()=>ok({baseUrl:'https://example.org/v1',model:'m',hasApiKey:true,dataDirectory:'d'})} as DesktopAPI;
const drafts:TodoDraft[]=[
 {tempId:'p',parentTempId:null,title:'父项',notes:'',due:{mode:'none'},source:'来源 A'},
 {tempId:'c',parentTempId:'p',title:'子项',notes:'',due:{mode:'inherit'},source:'来源 B'},
 {tempId:'x',parentTempId:null,title:'独立项',notes:'',due:{mode:'none'},source:'来源 C'},
];
function analysis(id='analysis-review',items=drafts):MailAnalysis{return {id,messageIds:['mail-a','mail-b'],instructions:'整理',notes:'说明',drafts:structuredClone(items),createdAt:'2026-10-30T00:00:00Z',saved:false,model:'m',baseUrl:'https://example.org/v1'};}

test('审核窗口：点击遮罩、Esc、关闭按钮都只收起，重新打开保留编辑',async()=>{
 let closed=0;const opener=document.createElement('button');opener.textContent='opener';document.body.append(opener);opener.focus();
 const ui=render(<Tools section="ai" todos={[]} initialAnalysis={analysis('analysis-close')} onClose={()=>{closed++;}} onChanged={async()=>{}}/>);
 await waitFor(()=>assert.ok(ui.getAllByLabelText('标题')));
 fireEvent.change(ui.getAllByLabelText('标题')[0],{target:{value:'父项已修改'}});
 const backdrop=ui.container.querySelector('.tools-backdrop')!;
 fireEvent.click(backdrop);assert.equal(closed,1);assert.equal(ui.queryByRole('alertdialog'),null);
 ui.unmount();assert.equal(document.activeElement,opener);
 const reopened=render(<Tools section="ai" todos={[]} initialAnalysis={analysis('analysis-close')} onClose={()=>{closed++;}} onChanged={async()=>{}}/>);
 await waitFor(()=>assert.ok(reopened.getAllByLabelText('标题')));
 assert.equal((reopened.getAllByLabelText('标题')[0] as HTMLInputElement).value,'父项已修改');
 fireEvent.keyDown(document,{key:'Escape'});assert.equal(closed,2);
 reopened.unmount();
 const third=render(<Tools section="ai" todos={[]} initialAnalysis={analysis('analysis-close')} onClose={()=>{closed++;}} onChanged={async()=>{}}/>);
 await waitFor(()=>assert.ok(third.getAllByLabelText('标题')));fireEvent.click(third.getByRole('button',{name:'关闭工具面板'}));assert.equal(closed,3);
 opener.remove();
});

test('点击弹窗内部和日期选择器不会误关闭',async()=>{
 let closed=0;const ui=render(<Tools section="ai" todos={[]} initialAnalysis={analysis('analysis-inside')} onClose={()=>{closed++;}} onChanged={async()=>{}}/>);
 await waitFor(()=>assert.ok(ui.getAllByLabelText('标题')));
 fireEvent.click(ui.getByRole('dialog'));fireEvent.click(ui.getAllByLabelText('截止方式')[0]);fireEvent.change(ui.getAllByLabelText('截止方式')[0],{target:{value:'date'}});
 assert.equal(closed,0);
});

test('一键全部添加覆盖未勾选草稿并保留层级与来源，已添加项不重复',async()=>{
 const calls:MailAnalysis['drafts'][]=[];let closed=0;
 window.mailWorkspace={saveAnalysis:async(_id:string,chosen:TodoDraft[])=>{calls.push(structuredClone(chosen));return ok({count:chosen.length});}} as unknown as MailWorkspaceAPI;
 const ui=render(<Tools section="ai" todos={[]} initialAnalysis={analysis('analysis-all')} onClose={()=>{closed++;}} onChanged={async()=>{}}/>);
 await waitFor(()=>assert.ok(ui.getByRole('button',{name:'全部添加（3 项）'})));
 fireEvent.change(ui.getAllByLabelText('标题')[2],{target:{value:'独立项已修改'}});
 fireEvent.click(ui.getByLabelText('保存草稿 3')); // 取消勾选第三项
 fireEvent.click(ui.getByRole('button',{name:'全部添加（3 项）'}));
 await waitFor(()=>assert.equal(calls.length,1));
 assert.equal(calls[0]!.length,3);assert.equal(calls[0]![2]!.title,'独立项已修改');assert.equal(calls[0]![1]!.parentTempId,'p');assert.equal(calls[0]![1]!.source,'来源 B');assert.equal(calls[0]![2]!.source,'来源 C');
 assert.ok(ui.getAllByText('已添加').length===3);assert.ok((ui.getByRole('button',{name:'全部添加（3 项）'}) as HTMLButtonElement).disabled);
 assert.equal(closed,0);
});

test('批量保存前统一校验并定位具体草稿，保留编辑内容',async()=>{
 let called=0;window.mailWorkspace={saveAnalysis:async()=>{called++;return ok({count:1});}} as unknown as MailWorkspaceAPI;
 const ui=render(<Tools section="ai" todos={[]} initialAnalysis={analysis('analysis-invalid')} onClose={()=>{}} onChanged={async()=>{}}/>);
 await waitFor(()=>assert.ok(ui.getByRole('button',{name:'全部添加（3 项）'})));
 fireEvent.change(ui.getAllByLabelText('标题')[1],{target:{value:'   '}});
 fireEvent.click(ui.getByRole('button',{name:'全部添加（3 项）'}));
 await waitFor(()=>assert.ok(ui.getByRole('alert')));
 assert.equal(called,0);assert.ok(ui.getByRole('alert').textContent?.includes('草稿 2'));
 assert.equal((ui.getAllByLabelText('标题')[1] as HTMLInputElement).value,'   ');
 assert.ok(ui.container.querySelector('.tools-draft-invalid'));
});

test('分批添加后仍可继续添加剩余草稿，父子层级保持',async()=>{
 const calls:MailAnalysis['drafts'][]=[];window.mailWorkspace={saveAnalysis:async(_id:string,chosen:TodoDraft[])=>{calls.push(structuredClone(chosen));return ok({count:1});}} as unknown as MailWorkspaceAPI;
 const partial={...analysis('analysis-partial'),addedTempIds:['p']};
 const ui=render(<Tools section="ai" todos={[]} initialAnalysis={partial} onClose={()=>{}} onChanged={async()=>{}}/>);
 await waitFor(()=>assert.ok(ui.getAllByText('已添加').length===1));
 fireEvent.click(ui.getByRole('button',{name:'确认保存 3 项待办'}));
 await waitFor(()=>assert.equal(calls.length,1));
 assert.ok(calls[0]!.some(d=>d.tempId==='c'));assert.ok(calls[0]!.some(d=>d.tempId==='x'));
 assert.ok(ui.getAllByText('已添加').length===3);
});
