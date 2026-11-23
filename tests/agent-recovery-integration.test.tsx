import React from 'react';
import {test,afterEach} from 'node:test';
import assert from 'node:assert/strict';
import {JSDOM} from 'jsdom';
import {createRequire} from 'node:module';
import {AgentApp} from '../src/renderer/AgentApp';
import type {AgentAPI,AgentConversation,AgentRun} from '../src/shared/agent';
import type {DesktopAPI} from '../src/shared/contracts';
import type {MailWorkspaceAPI} from '../src/shared/mail-workspace';

const dom=new JSDOM('<!doctype html><html><body></body></html>',{url:'http://localhost'});
for(const [key,value] of Object.entries({window:dom.window,document:dom.window.document,navigator:dom.window.navigator,HTMLElement:dom.window.HTMLElement}))Object.defineProperty(globalThis,key,{value,configurable:true});
const {render,fireEvent,waitFor,cleanup}=createRequire(process.cwd()+'/package.json')('@testing-library/react') as typeof import('@testing-library/react');
afterEach(()=>{cleanup();Reflect.deleteProperty(window,'agent');});
const ok=<T,>(data:T)=>({ok:true as const,data});
const date='2026-11-23T01:00:00Z';
const run=(patch:Partial<AgentRun>={}):AgentRun=>({id:'run-a',conversationId:'conv-a',status:'failed',errorCode:'connection',createdAt:date,updatedAt:date,partialText:'已整理第一部分。',events:[],sources:[],artifacts:[{kind:'file',id:'file-a',title:'已生成的报告.docx'}],...patch});
function conversation(current?:AgentRun):AgentConversation{return {id:'conv-a',kind:'secretary',title:'Secretary',createdAt:date,updatedAt:date,turnCount:current?1:0,turns:current?[{id:'user-a',role:'user',content:'帮我整理报告',createdAt:date}]:[],...(current?{activeRun:current}:{})};}
function setup(current:()=>AgentRun|undefined,overrides:Partial<AgentAPI>={},hasKey=true){
 window.desktop={getSettings:async()=>ok({baseUrl:'https://offline.invalid',model:'offline-model',hasApiKey:hasKey,dataDirectory:'data'}),listTodos:async()=>ok([]),getCompletionConfig:async()=>({ok:false,error:'本测试未配置统计规则'})} as unknown as DesktopAPI;
 window.mailWorkspace={getAccounts:async()=>ok([])} as unknown as MailWorkspaceAPI;
 window.agent={getSecretary:async()=>ok(conversation(current())),listConversations:async()=>ok([]),listPending:async()=>ok([]),getConversation:async()=>ok(conversation(current())),getRun:async()=>ok(current()!),...overrides} as AgentAPI;
}

test('恢复卡接回同一run且保留产物，不另行创建任务；已有暂停操作不变',async()=>{
 let current=run();const resumed:unknown[]=[];
 setup(()=>current,{resumeRun:async value=>{resumed.push(value);current=run({status:'paused',errorCode:undefined});return ok(current);},startTurn:async()=>{throw Error('must not start another run');}});
 const ui=render(<AgentApp/>);
 await waitFor(()=>assert.ok(ui.getByRole('button',{name:'继续本轮'})));
 assert.ok(ui.getByText('已生成的报告.docx'));
 fireEvent.click(ui.getByRole('button',{name:'继续本轮'}));
 await waitFor(()=>assert.deepEqual(resumed,[{id:'run-a'}]));
 await waitFor(()=>assert.ok(ui.getByRole('button',{name:'继续执行'})));
 assert.ok(ui.getByText('已生成的报告.docx'));
});

test('认证失败的设置按钮打开现有模型配置窗口',async()=>{
 const current=run({errorCode:'authentication'});setup(()=>current);
 const ui=render(<AgentApp/>);await waitFor(()=>assert.ok(ui.getByRole('button',{name:'打开模型设置'})));
 fireEvent.click(ui.getByRole('button',{name:'打开模型设置'}));
 await waitFor(()=>assert.ok(ui.getByRole('dialog',{name:'偏好与数据'})));
});

test('模型尚未配置时保留用户输入，并提供设置入口',async()=>{
 let starts=0;setup(()=>undefined,{startTurn:async()=>{starts++;return ok(run());}},false);
 const ui=render(<AgentApp/>);await waitFor(()=>assert.ok(ui.getByText('offline-model')));
 fireEvent.change(ui.getByLabelText('发送给助手'),{target:{value:'请整理这份报告'}});
 fireEvent.click(ui.getByRole('button',{name:'发送消息'}));
 await waitFor(()=>assert.match(ui.getByRole('alert').textContent??'',/API Key/));
 assert.equal((ui.getByLabelText('发送给助手') as HTMLTextAreaElement).value,'请整理这份报告');
 assert.equal(starts,0);
 fireEvent.click(ui.getByRole('button',{name:'打开模型设置'}));
 await waitFor(()=>assert.ok(ui.getByRole('dialog',{name:'偏好与数据'})));
});
