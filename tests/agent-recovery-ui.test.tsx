import React from 'react';
import {test,afterEach} from 'node:test';
import assert from 'node:assert/strict';
import {JSDOM} from 'jsdom';
import {createRequire} from 'node:module';
import {AgentRunRecovery} from '../src/renderer/AgentRunRecovery';
import type {AgentRun} from '../src/shared/agent';

const dom=new JSDOM('<!doctype html><html><body></body></html>',{url:'http://localhost'});
for(const [key,value] of Object.entries({window:dom.window,document:dom.window.document,navigator:dom.window.navigator,HTMLElement:dom.window.HTMLElement}))Object.defineProperty(globalThis,key,{value,configurable:true});
const {render,fireEvent,cleanup}=createRequire(process.cwd()+'/package.json')('@testing-library/react') as typeof import('@testing-library/react');
afterEach(cleanup);
const date='2026-11-23T01:00:00Z';
const run=(patch:Partial<AgentRun>={}):AgentRun=>({id:'same-run',conversationId:'conversation-a',status:'failed',createdAt:date,updatedAt:date,partialText:'已完成的部分',events:[],sources:[],artifacts:[],...patch});

test('网络失败给出可操作指引，点击后才继续，停止收尾时禁止重试和结束',()=>{
 let continued=0,canceled=0;
 const props={run:run({errorCode:'connection'}),busy:false,onContinue:()=>continued++,onCancel:()=>canceled++,onSettings:()=>{throw Error('unused');}};
 const ui=render(<AgentRunRecovery {...props}/>);
 assert.match(ui.getByRole('status').textContent??'',/网络|连接/);
 assert.match(ui.getByRole('status').textContent??'',/保留/);
 assert.equal(continued,0);
 fireEvent.click(ui.getByRole('button',{name:'继续本轮'}));assert.equal(continued,1);
 ui.rerender(<AgentRunRecovery {...props} run={run({errorCode:'connection',stopping:true})}/>);
 assert.equal((ui.getByRole('button',{name:'继续本轮'}) as HTMLButtonElement).disabled,true);
 assert.equal((ui.getByRole('button',{name:'结束本轮'}) as HTMLButtonElement).disabled,true);
 fireEvent.click(ui.getByRole('button',{name:'继续本轮'}));assert.equal(continued,1);
 assert.equal(canceled,0);
});

test('认证失败提供设置入口，预算限制建议拆分，不把运行错误原文当作可展示诊断',()=>{
 let opened=0;
 const props={run:run({errorCode:'authentication',error:'Authorization: secret-test-marker'}),busy:false,onContinue:()=>{},onCancel:()=>{},onSettings:()=>opened++};
 const ui=render(<AgentRunRecovery {...props}/>);
 assert.match(ui.getByRole('status').textContent??'',/密钥|权限/);
 assert.equal(ui.container.textContent?.includes('secret-test-marker'),false);
 fireEvent.click(ui.getByRole('button',{name:'打开模型设置'}));assert.equal(opened,1);
 ui.rerender(<AgentRunRecovery {...props} run={run({errorCode:'execution_limit'})}/>);
 assert.match(ui.getByRole('status').textContent??'',/拆分/);
 assert.equal(ui.queryByRole('button',{name:'打开模型设置'}),null);
});

test('旧失败记录无分类仍可恢复，执行中或已完成不展示恢复按钮',()=>{
 let canceled=0;
 const props={run:run(),busy:true,onContinue:()=>{},onCancel:()=>canceled++,onSettings:()=>{}};
 const ui=render(<AgentRunRecovery {...props}/>);
 assert.match(ui.getByRole('status').textContent??'',/未完成/);
 assert.equal((ui.getByRole('button',{name:'继续本轮'}) as HTMLButtonElement).disabled,true);
 ui.rerender(<AgentRunRecovery {...props} busy={false}/>);
 fireEvent.click(ui.getByRole('button',{name:'结束本轮'}));assert.equal(canceled,1);
 ui.rerender(<AgentRunRecovery {...props} run={run({status:'completed'})}/>);
 assert.equal(ui.queryByRole('button',{name:'继续本轮'}),null);
});
