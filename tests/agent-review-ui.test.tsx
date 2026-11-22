import React from 'react';
import {test,afterEach} from 'node:test';
import assert from 'node:assert/strict';
import {JSDOM} from 'jsdom';
import {createRequire} from 'node:module';
import {LearningReview,LearningLibrary} from '../src/renderer/LearningReview';
import {TaskChangeReview} from '../src/renderer/TaskChangeReview';
import {AgentApp} from '../src/renderer/AgentApp';
import {dispatchPython} from '../src/main/python-desktop';
import type {LearningMethod,LearningAPI,TaskChange,TaskChangesAPI,AgentAPI} from '../src/shared/agent';
const dom=new JSDOM('<!doctype html><html><body></body></html>',{url:'http://localhost'});
for(const [key,value] of Object.entries({window:dom.window,document:dom.window.document,navigator:dom.window.navigator,HTMLElement:dom.window.HTMLElement}))Object.defineProperty(globalThis,key,{value,configurable:true});
const {render,fireEvent,waitFor,cleanup,within}=createRequire(process.cwd()+'/package.json')('@testing-library/react') as typeof import('@testing-library/react');
afterEach(()=>{cleanup();for(const key of ['learning','taskChanges','agent','mailFiles'])Reflect.deleteProperty(window,key);});
const ok=<T,>(data:T)=>({ok:true as const,data});
const date='2026-11-21T01:00:00Z';
const method:LearningMethod={kind:'learning_draft',id:'method-1',title:'海港订单转换',scopeId:'scope-customer-a',scope:{kind:'customer',label:'Harbor Instruments',emails:['orders@harbor.example'],contextKey:''},version:2,versionHash:'a'.repeat(64),parentId:'method-0',status:'draft',createdAt:date,
 proposal:{title:'海港订单转换',kind:'table_workflow',scope:{kind:'customer',label:'Harbor Instruments',emails:['orders@harbor.example'],contextKey:''},instruction:'保留 PO 前导零；REV 补两位，单价为 USD 未税。',sources:[{kind:'file',id:'example-1',name:'客户确认样例.xlsx',sha256:'e'.repeat(64)}],exampleFileId:'example-1',recipe:{inputColumns:['PO','QTY'],outputColumns:['订单号','数量'],mapping:{订单号:'PO',数量:'QTY'},constants:{},concat:[],operations:[],requiredValues:['订单号'],numericColumns:['数量'],moneyColumns:[],totals:['数量'],allowExtraColumns:false,preserveRowCount:true,outputFormat:'xlsx'}},
 preview:{columns:['订单号','数量'],rows:[['00084','12']],inputRowCount:1,rowCount:1,totals:{数量:'12'},warnings:[],inputId:'example-1',inputSha256:'e'.repeat(64),outputSha256:'f'.repeat(64)}};
const change:TaskChange={kind:'task_change',id:'change-1',title:'更新：CS5021 期末考试',action:'update',taskId:'task-1',expectedRevision:3,patch:{due:{mode:'value',value:{kind:'minute',at:'2026-12-02T01:30:00.000Z',timeZone:'Asia/Hong_Kong'}}},reason:'老师明确更正了考试时间。',evidence:[{mailId:'mail-1',subject:'Updated exam arrangements',quote:'The exam is now 2 December at 09:30.',bindingId:'account-1'}],hash:'b'.repeat(64),status:'pending',createdAt:date,
 before:{id:'task-1',parentId:null,title:'CS5021 期末考试',notes:'Room 402',completed:false,due:{mode:'value',value:{kind:'minute',at:'2026-11-29T01:30:00.000Z',timeZone:'Asia/Hong_Kong'}},revision:3,createdAt:date,updatedAt:date,deletedAt:null,trashBatchId:null}};
function learning(overrides:Partial<LearningAPI>={}){window.learning={get:async()=>ok(method),list:async()=>ok([method]),approve:async()=>ok({...method,status:'approved',kind:'learning'}),disable:async()=>ok({...method,status:'disabled'}),...overrides};}
function tasks(overrides:Partial<TaskChangesAPI>={}){window.taskChanges={get:async()=>ok(change),list:async()=>ok([change]),approve:async()=>ok({...change,status:'approved',after:{...change.before,...change.patch,revision:4}}),reject:async()=>ok({...change,status:'rejected'}),...overrides};}

test('学习审核展示精确范围、规则、样例；仅显式批准/停用携带已显示版本hash',async()=>{
 const calls:unknown[]=[];learning({approve:async input=>{calls.push(['approve',input]);await new Promise(resolve=>setTimeout(resolve,10));return ok({...method,status:'approved',kind:'learning'});},disable:async input=>{calls.push(['disable',input]);return ok({...method,status:'disabled'});}});
 const sources:unknown[]=[];const ui=render(<LearningReview id={method.id} onSource={source=>sources.push(source)}/>);
 await waitFor(()=>assert.ok(ui.getByRole('button',{name:'批准并保存此方法'})));
 assert.ok(ui.getByText('orders@harbor.example'));assert.ok(ui.getByText('00084'));assert.ok(ui.getByText('PO'));assert.ok(ui.getByText(method.versionHash));assert.equal(calls.length,0);
 fireEvent.click(ui.getByRole('button',{name:'客户确认样例.xlsx'}));assert.deepEqual(sources,[{kind:'file',id:'example-1',title:'客户确认样例.xlsx'}]);
 const approve=ui.getByRole('button',{name:'批准并保存此方法'});fireEvent.click(approve);fireEvent.click(approve);
 await waitFor(()=>assert.ok(ui.getByRole('button',{name:'停用此方法'})));assert.deepEqual(calls,[['approve',{id:method.id,expectedHash:method.versionHash}]]);
 fireEvent.click(ui.getByRole('button',{name:'停用此方法'}));await waitFor(()=>assert.ok(ui.getByText(/此版本已停用/)));assert.equal(calls.length,2);assert.equal(ui.queryByRole('button',{name:'批准并保存此方法'}),null);
});
test('学习内容冲突不会伪装批准，已替代版本不提供恢复执行入口',async()=>{
 learning({approve:async()=>({ok:false,error:'方法预览已变化，请重新查看后确认'})});const ui=render(<LearningReview id={method.id}/>);await waitFor(()=>assert.ok(ui.getByRole('button',{name:'批准并保存此方法'})));fireEvent.click(ui.getByRole('button',{name:'批准并保存此方法'}));await waitFor(()=>assert.match(ui.getByRole('alert').textContent??'',/预览已变化/));assert.equal(ui.queryByRole('button',{name:'停用此方法'}),null);
 learning({get:async()=>ok({...method,id:'old',status:'superseded'})});ui.rerender(<LearningReview id="old"/>);await waitFor(()=>assert.ok(ui.getByText(/此版本保留用于追溯/)));assert.equal(ui.queryByRole('button',{name:'批准并保存此方法'}),null);
});
test('原始用户教学保持原文、时间和截断说明，与模型归纳分开；可选空列明确展示',async()=>{
 const original='REV 不能为空，只有 Note 可空。\n<img src=x onerror=alert(1)> 是原文示例。';
 const learned:LearningMethod={...method,proposal:{...method.proposal,teaching:{source:'conversation',conversationId:'chat-1',runId:'run-1',turns:[{id:'user-1',runId:'run-1',createdAt:date,content:original,textSha256:'b'.repeat(64)}],omittedUserTurns:2,sourceHash:'c'.repeat(64)},recipe:{...method.proposal.recipe!,inputColumns:['PO','REV','Note'],optionalInputColumns:['Note'],sheetName:'Order'}}};
 learning({get:async()=>ok(learned)});const ui=render(<LearningReview id={method.id}/>);await waitFor(()=>assert.ok(ui.getByText('允许空值的输入列')));
 const teaching=ui.getByRole('region',{name:'原始用户教学'});assert.equal(teaching.querySelector('blockquote')?.textContent,original);assert.equal(teaching.querySelector('img'),null);assert.doesNotMatch(teaching.textContent??'',/单价为 USD/);assert.match(teaching.textContent??'',/另有 2 条/);assert.match(teaching.textContent??'',/香港时间/);assert.equal(teaching.querySelector('time')?.getAttribute('dateTime'),date);
 assert.equal(ui.getByText('允许空值的输入列').nextElementSibling?.textContent,'Note');assert.equal(ui.getByText('输入工作表').nextElementSibling?.textContent,'Order');assert.ok(ui.getByText(method.proposal.instruction));
});
test('旧版和直接调用不会冒充用户原文；旧recipe未声明可选列时默认不允许空值',async()=>{
 learning();const ui=render(<LearningReview id={method.id}/>);await waitFor(()=>assert.ok(ui.getByText('旧版无原始对话。')));assert.equal(ui.getByText('允许空值的输入列').nextElementSibling?.textContent,'无');assert.equal(ui.getByRole('region',{name:'原始用户教学'}).querySelector('blockquote'),null);
 learning({get:async()=>ok({...method,id:'direct',proposal:{...method.proposal,teaching:{source:'direct-input',turns:[],omittedUserTurns:0,sourceHash:'d'.repeat(64)}}})});ui.rerender(<LearningReview id="direct"/>);await waitFor(()=>assert.ok(ui.getByText('直接输入；没有可验证的原始对话。')));assert.equal(ui.getByRole('region',{name:'原始用户教学'}).querySelector('blockquote'),null);
});
test('方法切换后忽略旧请求晚到，列表默认只显示已批准版本',async()=>{
 let finish!:(value:ReturnType<typeof ok<LearningMethod>>)=>void;learning({get:id=>id==='old'?new Promise(resolve=>{finish=resolve;}):Promise.resolve(ok({...method,id:'new',title:'当前方法'}))});
 const ui=render(<LearningReview id="old"/>);ui.rerender(<LearningReview id="new"/>);await waitFor(()=>assert.ok(ui.getByRole('heading',{name:'当前方法'})));finish(ok({...method,id:'old',title:'过时方法'}));await new Promise(resolve=>setTimeout(resolve,0));assert.equal(ui.queryByText('过时方法'),null);ui.unmount();
 learning({list:async()=>ok([method,{...method,id:'active',title:'已批准的课程方法',status:'approved',kind:'learning'}])});const list=render(<LearningLibrary onOpen={()=>{}}/>);await waitFor(()=>assert.ok(list.getByText('已批准的课程方法')));assert.equal(list.queryByText(method.title),null);fireEvent.change(list.getByLabelText('方法状态'),{target:{value:'all'}});assert.ok(list.getByText(method.title));
});
test('待办更正展示前后日期和原文；打开不会写任务，批准携带固定hash',async()=>{
 const calls:unknown[]=[];tasks({approve:async input=>{calls.push(input);return ok({...change,status:'approved',after:{...change.before,...change.patch,revision:4}});}});const sources:unknown[]=[];const ui=render(<TaskChangeReview id={change.id} onSource={source=>sources.push(source)}/>);await waitFor(()=>assert.ok(ui.getByRole('table',{name:'变更前后对比'})));
 const table=within(ui.getByRole('table',{name:'变更前后对比'}));assert.ok(table.getByText(/11月29日/));assert.ok(table.getByText(/12月2日/));assert.ok(ui.getByText(change.evidence[0].quote));assert.equal(calls.length,0);
 fireEvent.click(ui.getByRole('button',{name:'Updated exam arrangements'}));assert.deepEqual(sources,[{kind:'mail',id:'mail-1',title:'Updated exam arrangements'}]);
 fireEvent.click(ui.getByRole('button',{name:'批准更新此待办'}));await waitFor(()=>assert.ok(ui.getByText('变更已保存。')));assert.deepEqual(calls,[{id:change.id,expectedHash:change.hash}]);assert.equal(ui.queryByRole('button',{name:'批准更新此待办'}),null);
});
test('取消需明确批准；拒绝只调用reject，版本冲突仍保留审核信息',async()=>{
 const calls:unknown[]=[];tasks({get:async()=>ok({...change,action:'cancel',patch:{}}),reject:async input=>{calls.push(input);return ok({...change,status:'rejected'});}});const ui=render(<TaskChangeReview id={change.id}/>);await waitFor(()=>assert.ok(ui.getByRole('button',{name:'批准取消此待办'})));assert.ok(ui.getByText('移入回收站'));fireEvent.click(ui.getByRole('button',{name:'拒绝此变更'}));await waitFor(()=>assert.ok(ui.getByText('已拒绝此建议，原待办保持不变。')));assert.deepEqual(calls,[{id:change.id,expectedHash:change.hash}]);ui.unmount();
 tasks({approve:async()=>({ok:false,error:'待办在预览后发生变化，未覆盖'})});const conflict=render(<TaskChangeReview id={change.id}/>);await waitFor(()=>assert.ok(conflict.getByRole('button',{name:'批准更新此待办'})));fireEvent.click(conflict.getByRole('button',{name:'批准更新此待办'}));await waitFor(()=>assert.match(conflict.getByRole('alert').textContent??'',/未覆盖/));assert.ok(conflict.getByRole('table',{name:'变更前后对比'}));
});
test('Agent待确认卡打开各自审核器；设置可查看已批准学习方法',async()=>{
 let mailDraftReads=0;learning();tasks();window.desktop={getSettings:async()=>ok({baseUrl:'https://example.invalid',model:'fixture',hasApiKey:true,dataDirectory:'data'}),listTodos:async()=>ok([])} as any;window.mailWorkspace={getAccounts:async()=>ok([])} as any;
 window.agent={listConversations:async()=>ok([]),listPending:async()=>ok([{kind:'learning_draft',id:method.id,title:method.title,conversationId:'c',runId:'r',createdAt:date},{kind:'task_change',id:change.id,title:change.title,conversationId:'c',runId:'r',createdAt:date}]),getMailDraft:async()=>{mailDraftReads++;throw Error('wrong route');}} as unknown as AgentAPI;
 const ui=render(<AgentApp/>);await waitFor(()=>assert.ok(ui.getByText('fixture')));fireEvent.click(within(ui.getByRole('navigation',{name:'主导航'})).getByRole('button',{name:/待确认/}));await waitFor(()=>assert.ok(ui.getByRole('button',{name:/海港订单转换/})));fireEvent.click(ui.getByRole('button',{name:/海港订单转换/}));await waitFor(()=>assert.ok(ui.getByLabelText('学习方法审核')));assert.equal(mailDraftReads,0);fireEvent.click(ui.getByRole('button',{name:'关闭学习方法与版本'}));
 fireEvent.click(ui.getByRole('button',{name:/更新：CS5021/}));await waitFor(()=>assert.ok(ui.getByLabelText('待办变更审核')));fireEvent.click(ui.getByRole('button',{name:'关闭核对待办变更'}));fireEvent.click(ui.getByRole('button',{name:'设置'}));fireEvent.click(ui.getByRole('button',{name:/学习方法.*已批准的经验/}));await waitFor(()=>assert.ok(ui.getByLabelText('已保存的学习方法')));
});
test('旧待办建议显示过期状态与原始对比，不再提供批准入口',async()=>{
 let writes=0;tasks({get:async()=>ok({...change,status:'stale'}),approve:async()=>{writes++;return ok(change);}});const ui=render(<TaskChangeReview id={change.id}/>);await waitFor(()=>assert.ok(ui.getByText(/待办已变化，请重新提议/)));assert.ok(ui.getByRole('table',{name:'变更前后对比'}));assert.equal(ui.queryByRole('button',{name:'批准更新此待办'}),null);assert.equal(writes,0);assert.ok(ui.getByText(/原待办的内容或状态已改变/));
});
test('审核IPC仅受信页面可调用，写操作广播更新；读取不产生写通知',async()=>{
 const calls:unknown[]=[];let changed=0;const backend={call:async(...args:unknown[])=>{calls.push(args);return ok({});}};
 for(const channel of ['learning:list','learning:get','task-changes:list','task-changes:get'])assert.equal((await dispatchPython(backend,true,channel,[],()=>changed++)).ok,true);
 assert.equal(changed,0);for(const channel of ['learning:approve','learning:disable','task-changes:approve','task-changes:reject'])assert.equal((await dispatchPython(backend,true,channel,[{id:'x',expectedHash:'h'}],()=>changed++)).ok,true);assert.equal(changed,4);
 const before=calls.length;assert.equal((await dispatchPython(backend,false,'learning:approve',[{}],()=>changed++)).ok,false);assert.equal((await dispatchPython(backend,true,'learning:propose',[{}],()=>changed++)).ok,false);assert.equal(calls.length,before);
});
