import React from 'react';
import {test,afterEach} from 'node:test';
import assert from 'node:assert/strict';
import {JSDOM} from 'jsdom';
import {createRequire} from 'node:module';
import {MailJobs} from '../src/renderer/MailJobs';
import type {MailJob,MailOperationsAPI} from '../src/shared/mail-operations';
import type {WidgetAPI} from '../src/shared/widget';

const dom=new JSDOM('<!doctype html><html><body></body></html>',{url:'http://localhost'});
for(const [key,value] of Object.entries({window:dom.window,document:dom.window.document,navigator:dom.window.navigator,HTMLElement:dom.window.HTMLElement}))Object.defineProperty(globalThis,key,{value,configurable:true});
const {render,fireEvent,waitFor,cleanup}=createRequire(process.cwd()+'/package.json')('@testing-library/react') as typeof import('@testing-library/react');
afterEach(()=>{cleanup();delete window.widget;Reflect.deleteProperty(window,'mailOperations');});
const ok=<T,>(data:T)=>({ok:true as const,data});
const failed:MailJob={id:'failed-analysis',kind:'analysis',state:'failed',label:'分析三封测试邮件',done:1,total:3,messageCount:3,conversationId:'test-conversation',createdAt:'2026-11-20T12:00:00Z',updatedAt:'2026-11-20T12:01:00Z',error:'请求超时',notificationAcknowledged:true};

test('进入页面仍能展开已收起的失败分析，查看已有结果并继续处理',async()=>{
 let job={...failed},resumed='',conversation='';
 let changed:()=>void=()=>{};
 window.widget={onChanged:callback=>{changed=callback;return()=>{changed=()=>{};};}} as WidgetAPI;
 window.mailOperations={
  listJobs:async()=>ok([job]),
  resumeJob:async id=>{resumed=id;job={...job,state:'running',notificationAcknowledged:false};changed();return ok(job);},
 } as MailOperationsAPI;
 const ui=render(<MailJobs onConversation={id=>{conversation=id;}}/>);
 const summary=await waitFor(()=>ui.getByText('已收起的失败任务（1）',{selector:'summary'}));
 const details=summary.closest('details')!;
 assert.equal(details.open,false);
 // jsdom 的角色查询不隐藏关闭的 details 子节点；用原生 open 状态及所属面板验证折叠。
 assert.ok(details.contains(ui.getByRole('button',{name:'继续处理'})));
 fireEvent.click(summary);
 assert.equal(details.open,true);
 assert.ok(ui.getByText(/请求超时/));
 assert.ok(ui.queryByRole('button',{name:/关闭任务提示/})===null);
 fireEvent.click(ui.getByRole('button',{name:'查看对话与结果'}));
 assert.equal(conversation,'test-conversation');
 fireEvent.click(ui.getByRole('button',{name:'继续处理'}));
 await waitFor(()=>assert.equal(resumed,failed.id));
 await waitFor(()=>assert.ok(ui.getByRole('button',{name:'暂停'})));
 assert.ok(ui.queryByText('已收起的失败任务（1）')===null);
});

test('关闭失败主提示后折叠保留恢复入口，重新进入页面也可恢复',async()=>{
 let job={...failed,notificationAcknowledged:false},acknowledged='';
 window.mailOperations={
  listJobs:async()=>ok([job]),
  acknowledgeJob:async id=>{acknowledged=id;job={...job,notificationAcknowledged:true};return ok(undefined);},
 } as MailOperationsAPI;
 const ui=render(<MailJobs onConversation={()=>{}}/>);
 const dismiss=await waitFor(()=>ui.getByRole('button',{name:'关闭任务提示 分析三封测试邮件'}));
 assert.ok(ui.getByRole('button',{name:'继续处理'}));
 fireEvent.click(dismiss);
 await waitFor(()=>assert.equal(acknowledged,failed.id));
 assert.ok(ui.container.querySelector('.mw-jobs > .mw-job')===null);
 const summary=ui.getByText('已收起的失败任务（1）',{selector:'summary'});
 assert.equal(summary.closest('details')!.open,false);
 assert.ok(summary.closest('details')!.contains(ui.getByRole('button',{name:'继续处理'})));
 assert.ok(ui.queryByRole('button',{name:/关闭任务提示/})===null);
 ui.unmount();
 const reopened=render(<MailJobs onConversation={()=>{}}/>);
 const restored=await waitFor(()=>reopened.getByText('已收起的失败任务（1）',{selector:'summary'}));
 fireEvent.click(restored);
 assert.ok(reopened.getByRole('button',{name:'继续处理'}));
});

test('关闭已完成任务仍移除普通主提示，不产生失败恢复列表',async()=>{
 let acknowledged='';
 window.mailOperations={
  listJobs:async()=>ok([{...failed,state:'completed',error:undefined,notificationAcknowledged:false}]),
  acknowledgeJob:async id=>{acknowledged=id;return ok(undefined);},
 } as MailOperationsAPI;
 const ui=render(<MailJobs onConversation={()=>{}}/>);
 fireEvent.click(await waitFor(()=>ui.getByRole('button',{name:'关闭任务提示 分析三封测试邮件'})));
 await waitFor(()=>assert.equal(acknowledged,failed.id));
 assert.ok(ui.queryByRole('region',{name:'后台邮件任务'})===null);
 assert.ok(ui.queryByText(/已收起的失败任务/)===null);
});
