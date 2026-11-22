import React from 'react';
import {test,afterEach} from 'node:test';
import assert from 'node:assert/strict';
import {JSDOM} from 'jsdom';
import {createRequire} from 'node:module';
import {AgentInbox} from '../src/renderer/AgentPages';
import type {MailJob,MailOperationsAPI} from '../src/shared/mail-operations';
import type {LocalMail,MailWorkspaceAPI} from '../src/shared/mail-workspace';

const dom=new JSDOM('<!doctype html><html><body></body></html>',{url:'http://localhost'});
for(const [key,value] of Object.entries({window:dom.window,document:dom.window.document,navigator:dom.window.navigator,HTMLElement:dom.window.HTMLElement}))Object.defineProperty(globalThis,key,{value,configurable:true});
const {render,fireEvent,waitFor,cleanup}=createRequire(process.cwd()+'/package.json')('@testing-library/react') as typeof import('@testing-library/react');
afterEach(()=>{cleanup();Reflect.deleteProperty(window,'mailOperations');Reflect.deleteProperty(window,'widget');});
const ok=<T,>(data:T)=>({ok:true as const,data});
const date='2026-11-21T01:00:00Z';
const makeJob=(patch:Partial<MailJob>={}):MailJob=>({id:'sync-a',kind:'import',label:'收取最近邮件',state:'queued',done:0,total:0,createdAt:date,updatedAt:date,...patch});
const completed=()=>makeJob({state:'completed',done:12,total:12,report:{added:12,skipped:3,failed:0,bodyRead:12,bodyPending:0,limited:false,errors:[]}});
function setup(patch:Partial<MailOperationsAPI>={}){
 window.mailWorkspace={getAccounts:async()=>ok([{id:'qq-a',method:'imap',state:{account:{id:'account-a',email:'test@example.invalid'}}}]),listLocal:async()=>ok({messages:[],total:0})} as unknown as MailWorkspaceAPI;
 window.mailOperations={listJobs:async()=>ok([]),startInboxSync:async()=>ok(makeJob()),getJob:async()=>ok(completed()),startImport:async()=>{throw Error('Do not use custom-filter importer');},resumeJob:async()=>ok(makeJob({state:'running'})),cancelJob:async()=>{throw Error('Unmount must not cancel the job');},...patch} as MailOperationsAPI;
}
const handlers={onRead:()=>{},onUse:()=>{},onManage:()=>{}};

test('一键收取调用无参数专用入口，不需要勾选或预设；完成刷新并保留搜索',async()=>{
 let starts=0,received=false;const inputs:unknown[][]=[],queries:unknown[]=[];
 setup({startInboxSync:async(...args)=>{starts++;inputs.push(args);return ok(makeJob());},getJob:async()=>{received=true;return ok(completed());}});
 const mail={localId:'m-a',subject:'TM演练 1121 E01 项目要求',sender:'sender@example.invalid',receivedAt:date,preview:'合成数据'} as LocalMail;
 window.mailWorkspace.listLocal=async query=>{queries.push(query);return ok({messages:received?[mail]:[],total:received?1:0});};
 const ui=render(<AgentInbox {...handlers}/>);
 fireEvent.change(ui.getByLabelText('搜索邮件主题或发件人'),{target:{value:'TM演练 1121'}});
 assert.equal(ui.queryByRole('checkbox'),null);assert.equal(ui.queryByRole('button',{name:'新建预设'}),null);
 fireEvent.click(ui.getByRole('button',{name:'收取新邮件'}));
 await waitFor(()=>assert.ok(ui.getByText('TM演练 1121 E01 项目要求')),{timeout:2000});
 assert.equal(starts,1);assert.deepEqual(inputs,[[]]);assert.ok(ui.getByText('收取完成'));
 assert.ok(ui.getByText('新增 12 封 · 跳过已收 3 封'));
 assert.equal((ui.getByLabelText('搜索邮件主题或发件人') as HTMLInputElement).value,'TM演练 1121');
 assert.deepEqual(queries.at(-1),{search:'TM演练 1121',offset:0,limit:30});
});

test('重复点击只启动一次；离开页面不中断后台，返回恢复进度并刷新结果',async()=>{
 let starts=0,cancels=0,current=makeJob({state:'running',done:1,total:12});
 let finish!:(value:ReturnType<typeof ok<MailJob>>)=>void;
 setup({startInboxSync:()=>{starts++;return new Promise(resolve=>{finish=resolve;});},listJobs:async()=>ok(starts?[current]:[]),getJob:async()=>ok(current),cancelJob:async()=>{cancels++;return ok(makeJob({state:'canceled'}));}});
 const first=render(<AgentInbox {...handlers}/>);const button=first.getByRole('button',{name:'收取新邮件'});
 fireEvent.click(button);fireEvent.click(button);assert.equal(starts,1);
 finish(ok(current));await waitFor(()=>assert.ok(first.getByText(/正在收取新邮件 · 已处理 1\/12 封/)));
 first.unmount();assert.equal(cancels,0);
 const second=render(<AgentInbox {...handlers}/>);
 await waitFor(()=>assert.equal((second.getByRole('button',{name:'正在收取…'}) as HTMLButtonElement).disabled,true));
 current=completed();await waitFor(()=>assert.ok(second.getByText('收取完成')),{timeout:2000});
 assert.equal(starts,1);assert.equal(cancels,0);
});

test('部分失败或正文不完整不会显示全部收取成功，保留计数和详情入口',async()=>{
 let details=0;
 setup({startInboxSync:async()=>ok(makeJob({state:'completed',report:{added:8,skipped:2,failed:1,bodyPending:2,limited:false,errors:['一封读取失败']}}))});
 const ui=render(<AgentInbox {...handlers} onManage={()=>{details++;}}/>);
 fireEvent.click(ui.getByRole('button',{name:'收取新邮件'}));
 await waitFor(()=>assert.ok(ui.getByText('本次收取结束，仍有内容待处理')));
 assert.equal(ui.queryByText('收取完成'),null);
 assert.ok(ui.getByText('新增 8 封 · 跳过已收 2 封 · 失败 1 项 · 2 封正文待补读'));
 fireEvent.click(ui.getByRole('button',{name:'查看收取详情'}));assert.equal(details,1);
});

test('持久失败任务在返回页面时可继续同一任务',async()=>{
 let resumed='';const failed=makeJob({state:'failed',done:1,total:12,error:'网络连接中断'});
 setup({listJobs:async()=>ok([failed]),resumeJob:async id=>{resumed=id;return ok(completed());}});
 const ui=render(<AgentInbox {...handlers}/>);
 await waitFor(()=>assert.ok(ui.getByText('本次收取未完成')));assert.ok(ui.getByText('网络连接中断'));
 fireEvent.click(ui.getByRole('button',{name:'继续收取'}));
 await waitFor(()=>assert.equal(resumed,'sync-a'));await waitFor(()=>assert.ok(ui.getByText('收取完成')));
});

test('没有已连接邮箱时说明原因并提供设置入口，错误不抹掉搜索条件',async()=>{
 let opened=0;
 setup({startInboxSync:async()=>({ok:false,error:'请先连接至少一个邮箱'})});
 window.mailWorkspace.getAccounts=async()=>ok([]);
 const ui=render(<AgentInbox {...handlers} onManage={()=>{opened++;}}/>);
 fireEvent.change(ui.getByLabelText('搜索邮件主题或发件人'),{target:{value:'课程'}});
 await waitFor(()=>assert.ok(ui.getByRole('button',{name:'连接邮箱'})));
 fireEvent.click(ui.getByRole('button',{name:'收取新邮件'}));
 await waitFor(()=>assert.match(ui.getByRole('alert').textContent??'',/请先连接至少一个邮箱/));
 assert.equal((ui.getByLabelText('搜索邮件主题或发件人') as HTMLInputElement).value,'课程');
 fireEvent.click(ui.getByRole('button',{name:'打开邮箱设置'}));assert.equal(opened,1);
});

test('邮件选择弹窗只刷新已收取列表，不触发全邮箱同步',async()=>{
 let reads=0,starts=0,jobLists=0;
 setup({startInboxSync:async()=>{starts++;return ok(makeJob());},listJobs:async()=>{jobLists++;return ok([]);}});
 window.mailWorkspace.listLocal=async()=>{reads++;return ok({messages:[],total:0});};
 const ui=render(<AgentInbox {...handlers} selectMode onSelect={()=>{}}/>);
 await waitFor(()=>assert.equal(reads,1));
 assert.equal(ui.queryByRole('button',{name:'收取新邮件'}),null);
 fireEvent.click(ui.getByRole('button',{name:'刷新列表'}));await waitFor(()=>assert.equal(reads,2));
 assert.equal(starts,0);assert.equal(jobLists,0);
});

test('晚到的旧任务列表不能覆盖用户刚启动的收取',async()=>{
 let listFinish!:(value:ReturnType<typeof ok<MailJob[]>>)=>void;
 setup({listJobs:()=>new Promise(resolve=>{listFinish=resolve;}),startInboxSync:async()=>ok(makeJob({state:'running'})),getJob:async()=>ok(makeJob({state:'running'}))});
 const ui=render(<AgentInbox {...handlers}/>);
 fireEvent.click(ui.getByRole('button',{name:'收取新邮件'}));
 await waitFor(()=>assert.ok(ui.getByText('正在收取新邮件…')));
 listFinish(ok([completed()]));
 await waitFor(()=>assert.equal((ui.getByRole('button',{name:'正在收取…'}) as HTMLButtonElement).disabled,true));
 assert.equal(ui.queryByText('收取完成'),null);
});
