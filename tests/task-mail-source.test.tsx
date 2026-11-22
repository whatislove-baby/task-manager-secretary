import React from 'react';
import {test,afterEach} from 'node:test';
import assert from 'node:assert/strict';
import {JSDOM} from 'jsdom';
import {createRequire} from 'node:module';
import {TaskMailSource} from '../src/renderer/TaskMailSource';
import type {MailWorkspaceAPI,LocalMail} from '../src/shared/mail-workspace';
const dom=new JSDOM('<!doctype html><html><body></body></html>',{url:'http://localhost'});
for(const [k,v] of Object.entries({window:dom.window,document:dom.window.document,navigator:dom.window.navigator,HTMLElement:dom.window.HTMLElement}))Object.defineProperty(globalThis,k,{value:v,configurable:true});
const {render,fireEvent,waitFor,cleanup}=createRequire(process.cwd()+'/package.json')('@testing-library/react') as typeof import('@testing-library/react');
afterEach(()=>{cleanup();delete window.widget;});
const ok=<T,>(data:T)=>({ok:true as const,data});
const id='a'.repeat(64),aid='11111111-1111-4111-8111-111111111111';
const mail=(subject:string)=>({subject,sender:'teacher@example.invalid',receivedAt:'2026-10-31T00:00:00Z',body:'原文<script>not code</script>'}) as LocalMail;
test('备注精确邮件入口只读本地正文，纯文本显示且关闭恢复焦点',async()=>{
 let calls=0;window.mailWorkspace={getLocal:async (key:string)=>{calls++;assert.equal(key,id);return ok(mail('正确来源'));}} as unknown as MailWorkspaceAPI;
 const ui=render(<TaskMailSource notes={'本地邮件：'+id}/>);assert.equal(calls,0);const opener=ui.getByRole('button',{name:'查看来源邮件正文'});opener.focus();fireEvent.click(opener);
 await waitFor(()=>assert.ok(ui.queryByText('正确来源')));assert.ok(ui.getByLabelText('邮件原文内容').textContent?.includes('<script>'));assert.ok(!ui.getByRole('dialog').querySelector('script'));
 fireEvent.keyDown(document,{key:'Escape'});assert.ok(!ui.queryByRole('dialog'));assert.ok(document.activeElement===opener);
});
test('旧分析批次多邮件先列候选而不默认认定第一封；正文删除不重新导入',async()=>{
 let reads=0;window.mailWorkspace={getAnalysis:async()=>ok({messageIds:[id,'b'.repeat(64)]}),getLocal:async (key:string)=>{reads++;return key===id?ok(mail('候选甲')):{ok:false,error:'本地正文已删除'};}} as unknown as MailWorkspaceAPI;
 const ui=render(<TaskMailSource notes={'分析记录：'+aid}/>);fireEvent.click(ui.getByRole('button',{name:'查看来源邮件正文'}));await waitFor(()=>assert.ok(ui.queryByText('候选甲')));assert.ok(ui.queryByText(/以下为该分析批次/));assert.ok(!ui.queryByText('原文<script>not code</script>'));fireEvent.click(ui.getByRole('button',{name:'正文已删除的邮件'}));assert.ok(ui.queryByText(/不会自动重新导入/));assert.equal(reads,2);
});

test('旧备注包含唯一完整邮件标题时直接打开对应正文，不误取批次第一封',async()=>{
 window.mailWorkspace={getAnalysis:async()=>ok({messageIds:[id,'b'.repeat(64)]}),getLocal:async(key:string)=>ok(mail(key===id?'无关第一封':'Mock Interview成绩通知'))} as unknown as MailWorkspaceAPI;
 const ui=render(<TaskMailSource notes={'邮件分析来源：Mock Interview成绩通知\n分析记录：'+aid}/>);fireEvent.click(ui.getByRole('button',{name:'查看来源邮件正文'}));await waitFor(()=>assert.ok(ui.queryByRole('heading',{name:'Mock Interview成绩通知'})));assert.ok(!ui.queryByText(/以下为该分析批次/));assert.ok(!ui.queryByRole('button',{name:'无关第一封'}));
});
