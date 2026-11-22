import React from 'react';
import {test,afterEach} from 'node:test';
import assert from 'node:assert/strict';
import {JSDOM} from 'jsdom';
import {createRequire} from 'node:module';
import {MailAutomationPanel} from '../src/renderer/MailAutomationPanel';
import {defaultMailAutomationConfig,type MailAutomationAPI,type MailAutomationConfig,type MailAutomationConfigInput,type MailAutomationItem,type MailAutomationResultQuery,type MailAutomationStatus} from '../src/shared/mail-automation';
import type {MailAnalysis,MailBinding,MailWorkspaceAPI} from '../src/shared/mail-workspace';
import type {Result} from '../src/shared/contracts';
import {defaultMailFilter} from '../src/shared/mail';

const dom=new JSDOM('<!doctype html><html><body></body></html>',{url:'http://localhost'});
for(const [key,value] of Object.entries({window:dom.window,document:dom.window.document,navigator:dom.window.navigator,HTMLElement:dom.window.HTMLElement}))Object.defineProperty(globalThis,key,{value,configurable:true});
const {render,fireEvent,waitFor,cleanup,within,act}=createRequire(process.cwd()+'/package.json')('@testing-library/react') as typeof import('@testing-library/react');
const surface=window as Window & {mailAutomation?:MailAutomationAPI};
afterEach(()=>{cleanup();delete surface.mailAutomation;});
const ok=<T,>(data:T):Result<T>=>({ok:true,data});
const account:MailBinding={id:'imap-connected',method:'imap',state:{method:'imap',clientId:'',account:{id:'student',email:'student@example.invalid',name:'Student'},imap:{email:'student@example.invalid',username:'student@example.invalid',host:'imap.example.invalid',port:993,security:'tls',hasSecret:true},filter:structuredClone(defaultMailFilter)}};
const disconnected:MailBinding={...structuredClone(account),id:'imap-disconnected',state:{...structuredClone(account.state),account:null,imap:{...account.state.imap!,email:'offline@example.invalid'}}};
const microsoft:MailBinding={id:'microsoft',method:'microsoft',state:{clientId:'fixture',account:{id:'school',email:'school@example.invalid',name:'School'},filter:structuredClone(defaultMailFilter)}};
const item:MailAutomationItem={localId:'mail-1',bindingId:account.id,subject:'课程报告提交',sender:'teacher@example.invalid',receivedAt:'2026-11-20T02:00:00Z',category:'action',summary:'请在周一提交报告。',reason:'来信提出明确提交要求。',analysisId:'analysis-1',classifiedAt:'2026-11-20T02:01:00Z',corrected:false};
const analysis:MailAnalysis={id:'analysis-1',messageIds:['mail-1'],instructions:'整理',notes:'待办建议',drafts:[{tempId:'1',parentTempId:null,title:'提交课程报告',source:'原文'}],createdAt:'2026-11-20T02:01:00Z',saved:false,model:'fixture',baseUrl:'https://fixture.invalid/v1'};

function setup(overrides:Partial<MailAutomationAPI>={},initial:Partial<MailAutomationConfig>={}) {
  let config:MailAutomationConfig={...structuredClone(defaultMailAutomationConfig),nextRunAt:null,...initial};
  let status:MailAutomationStatus={state:'idle',lastRun:null,nextRunAt:config.nextRunAt};
  let items:MailAutomationItem[]=[structuredClone(item)];
  const queries:MailAutomationResultQuery[]=[];
  surface.mailAutomation={
    getConfig:async()=>ok(structuredClone(config)),
    saveConfig:async input=>{config={...structuredClone(input),nextRunAt:input.enabled?'2026-11-21T01:00:00Z':null};status={...status,nextRunAt:config.nextRunAt};return ok(config);},
    getStatus:async()=>ok(status),
    runNow:async()=>{status={...status,state:'queued'};return ok({queued:true});},
    listResults:async query=>{queries.push({...query});const filtered=items.filter(row=>!query.category||row.category===query.category);const counts:Record<string,number>={};for(const row of items)counts[row.category]=(counts[row.category]??0)+1;return ok({items:structuredClone(filtered.slice(query.offset??0,(query.offset??0)+(query.limit??20))),total:filtered.length,counts});},
    correctCategory:async(id,category)=>{items=items.map(row=>row.localId===id?{...row,category,corrected:true}:row);return ok(structuredClone(items.find(row=>row.localId===id)!));},
    getBackgroundStatus:async()=>ok({supported:true,installed:true,active:true}),
    ...overrides,
  };
  window.mailWorkspace={getAnalysis:async()=>ok(analysis)} as unknown as MailWorkspaceAPI;
  return {queries,setStatus:(value:MailAutomationStatus)=>{status=value;},setItems:(value:MailAutomationItem[])=>{items=value;}};
}
async function ready(ui:ReturnType<typeof render>) {
  await waitFor(()=>assert.equal((ui.getByRole('button',{name:/保存并/}) as HTMLButtonElement).disabled,false));
  await waitFor(()=>assert.ok(ui.queryByText('课程报告提交')));
}

test('自动整理默认关闭，只列已连接IMAP；保存发送可写字段并清理dirty',async()=>{
  let sent:MailAutomationConfigInput|undefined;const dirty:boolean[]=[];
  setup({saveConfig:async input=>{sent=input;return ok({...input,nextRunAt:'2026-11-21T02:00:00Z'});}});
  const ui=render(<MailAutomationPanel accounts={[account,disconnected,microsoft]} onReview={()=>{}} onDirty={value=>dirty.push(value)}/>);
  await ready(ui);
  assert.equal((ui.getByLabelText('第一次整理时间') as HTMLInputElement).value,'09:00');
  assert.equal((ui.getByLabelText('第二次整理时间') as HTMLInputElement).value,'18:00');
  assert.equal((ui.getByLabelText('启用定时整理') as HTMLInputElement).checked,false);
  assert.equal(ui.queryByLabelText('offline@example.invalid'),null);assert.equal(ui.queryByLabelText('school@example.invalid'),null);
  fireEvent.click(ui.getByLabelText('启用定时整理'));fireEvent.click(ui.getByLabelText('student@example.invalid'));
  fireEvent.change(ui.getByLabelText('第一次整理时间'),{target:{value:'10:00'}});
  fireEvent.change(ui.getByLabelText('首次读取范围'),{target:{value:'30'}});
  fireEvent.change(ui.getByLabelText('每次最多分析'),{target:{value:'50'}});
  fireEvent.change(ui.getByLabelText('重要发件人（每行一个完整邮箱地址）'),{target:{value:' teacher@example.invalid\nmanager@example.invalid\n'}});
  fireEvent.change(ui.getByLabelText('忽略发件人（每行一个完整邮箱地址）'),{target:{value:'news@example.invalid'}});
  await waitFor(()=>assert.equal(dirty.at(-1),true));
  assert.equal((ui.getByRole('button',{name:'立即整理一次'}) as HTMLButtonElement).disabled,true);
  fireEvent.click(ui.getByRole('button',{name:'保存并启用'}));
  await waitFor(()=>assert.ok(ui.queryByText('已保存并启用定时整理。')));
  assert.deepEqual(sent,{enabled:true,bindingIds:[account.id],times:['10:00','18:00'],timeZone:'Asia/Hong_Kong',lookbackDays:30,maxMessagesPerRun:50,importantSenders:['teacher@example.invalid','manager@example.invalid'],mutedSenders:['news@example.invalid']});
  assert.ok(!('nextRunAt' in sent!));await waitFor(()=>assert.equal(dirty.at(-1),false));
  assert.ok(ui.getByText(/邮件内容将发送到当前设置的 AI 服务/));assert.ok(ui.getByText(/不会自动发送邮件/));
  assert.ok(ui.getByText(/已启用，等待计划或执行中/));
  assert.ok(ui.getByText(/忽略发件人的邮件归为“疑似垃圾”并跳过 AI/));
});

test('保存服务端校验失败保留编辑和dirty，不显示启用成功',async()=>{
  let submitted:MailAutomationConfigInput|undefined;const dirty:boolean[]=[];
  setup({saveConfig:async value=>{submitted=value;return {ok:false,error:'请至少选择一个已连接邮箱'};}});
  const ui=render(<MailAutomationPanel accounts={[account]} onReview={()=>{}} onDirty={value=>dirty.push(value)}/>);await ready(ui);
  fireEvent.click(ui.getByLabelText('启用定时整理'));
  fireEvent.change(ui.getByLabelText('重要发件人（每行一个完整邮箱地址）'),{target:{value:'teacher@example.invalid'}});
  fireEvent.click(ui.getByRole('button',{name:'保存并启用'}));
  await waitFor(()=>assert.ok(ui.queryByRole('alert')));
  assert.equal(submitted?.bindingIds.length,0);assert.ok(ui.getByText('请至少选择一个已连接邮箱'));
  assert.ok(ui.getByText('定时整理未启用'));assert.equal(ui.queryByText('已保存并启用定时整理。'),null);
  assert.equal((ui.getByLabelText('重要发件人（每行一个完整邮箱地址）') as HTMLTextAreaElement).value,'teacher@example.invalid');assert.equal(dirty.at(-1),true);
});

test('立即整理只排队且快速连点只调用一次；缺少AI配置显示错误',async()=>{
  let calls=0;let resolve!: (value:Result<{queued:true}>)=>void;
  const fixture=setup({runNow:async()=>{calls++;return new Promise<Result<{queued:true}>>(done=>{resolve=done;});}},{bindingIds:[account.id]});
  const ui=render(<MailAutomationPanel accounts={[account]} onReview={()=>{}}/>);await ready(ui);
  const run=ui.getByRole('button',{name:'立即整理一次'});fireEvent.click(run);fireEvent.click(run);assert.equal(calls,1);
  fixture.setStatus({state:'queued',lastRun:null,nextRunAt:null});
  await act(async()=>resolve(ok({queued:true})));
  await waitFor(()=>assert.ok(ui.getByText('已加入整理队列，结果会自动更新。')));
  assert.equal((ui.getByRole('button',{name:'整理进行中'}) as HTMLButtonElement).disabled,true);
  fixture.setStatus({state:'idle',lastRun:null,nextRunAt:null});fireEvent.click(ui.getByRole('button',{name:'刷新结果'}));
  await waitFor(()=>assert.equal((ui.getByRole('button',{name:'立即整理一次'}) as HTMLButtonElement).disabled,false));
  fireEvent.click(ui.getByRole('button',{name:'立即整理一次'}));
  await act(async()=>resolve({ok:false,error:'请先配置 AI 服务'}));
  await waitFor(()=>assert.ok(ui.getByText('请先配置 AI 服务')));assert.equal(ui.queryByText('已加入整理队列，结果会自动更新。'),null);
});

test('分类筛选与分页使用服务端查询；切换类别回第一页',async()=>{
  const fixture=setup();fixture.setItems(Array.from({length:21},(_,index)=>({...item,localId:'mail-'+index,subject:index===0?item.subject:'待处理邮件 '+index})));
  const ui=render(<MailAutomationPanel accounts={[account]} onReview={()=>{}}/>);await ready(ui);
  fireEvent.click(ui.getByRole('button',{name:'下一页'}));
  await waitFor(()=>assert.ok(ui.queryByText('待处理邮件 20')));assert.equal(fixture.queries.at(-1)?.offset,20);
  fireEvent.click(ui.getByRole('button',{name:'重要知会（0）'}));
  await waitFor(()=>assert.ok(ui.queryByText('这个分类暂时没有邮件。')));
  assert.deepEqual(fixture.queries.at(-1),{category:'important',offset:0,limit:20});
  assert.equal((ui.getByRole('button',{name:'上一页'}) as HTMLButtonElement).disabled,true);
});

test('待办建议只读取现有分析再交给审核；原邮件用来源回调',async()=>{
  setup();let requested='',reviewed:MailAnalysis|undefined,opened='';let directSaves=0;
  window.mailWorkspace={getAnalysis:async (id:string)=>{requested=id;return ok(analysis);},saveAnalysis:async()=>{directSaves++;return ok({count:1});}} as unknown as MailWorkspaceAPI;
  const ui=render(<MailAutomationPanel accounts={[account]} onReview={value=>{reviewed=value;}} onOpenMail={id=>{opened=id;}}/>);await ready(ui);
  fireEvent.click(ui.getByRole('button',{name:'查看原邮件 课程报告提交'}));assert.equal(opened,'mail-1');
  fireEvent.click(ui.getByRole('button',{name:'审核待办建议 课程报告提交'}));
  await waitFor(()=>assert.equal(requested,'analysis-1'));assert.deepEqual(reviewed,analysis);assert.equal(directSaves,0);
});

test('更正分类失败不改变选择，成功后保留手动更正标记',async()=>{
  let calls=0;const fixture=setup({correctCategory:async(id,category)=>{calls++;if(calls===1)return {ok:false,error:'分类保存失败'};const changed={...item,localId:id,category,corrected:true};fixture.setItems([changed]);return ok(changed);}});
  const ui=render(<MailAutomationPanel accounts={[account]} onReview={()=>{}}/>);await ready(ui);
  fireEvent.change(ui.getByLabelText('更正分类 课程报告提交'),{target:{value:'important'}});
  await waitFor(()=>assert.ok(ui.queryByText('分类保存失败')));
  assert.equal((ui.getByLabelText('更正分类 课程报告提交') as HTMLSelectElement).value,'action');assert.equal(ui.queryByText('已手动更正'),null);
  fireEvent.change(ui.getByLabelText('更正分类 课程报告提交'),{target:{value:'important'}});
  await waitFor(()=>assert.ok(ui.queryByText('已手动更正')));
  assert.equal((ui.getByLabelText('更正分类 课程报告提交') as HTMLSelectElement).value,'important');assert.equal(calls,2);
  const article=ui.getByText('课程报告提交').closest('article')!;assert.ok(within(article).getByText('重要知会',{selector:'span'}));
});

test('轮询刷新状态结果不重载配置、不覆盖未保存的规则',async()=>{
  let configReads=0;let tick:()=>void=()=>{};const originalInterval=globalThis.setInterval;
  globalThis.setInterval=((callback:()=>void,delay?:number,...args:unknown[])=>{if(delay===3000){tick=callback;return 0 as unknown as ReturnType<typeof setInterval>;}return originalInterval(callback,delay,...args);}) as typeof setInterval;
  try {
    const fixture=setup({getConfig:async()=>{configReads++;return ok({...structuredClone(defaultMailAutomationConfig),nextRunAt:null});}});const dirty:boolean[]=[];
    const ui=render(<MailAutomationPanel accounts={[account]} onReview={()=>{}} onDirty={value=>dirty.push(value)}/>);await ready(ui);
    fireEvent.change(ui.getByLabelText('重要发件人（每行一个完整邮箱地址）'),{target:{value:'unsaved@example.invalid'}});
    fixture.setStatus({state:'failed',lastRun:{id:'run',trigger:'schedule',state:'failed',startedAt:'2026-11-20T01:00:00Z',finishedAt:'2026-11-20T01:01:00Z',imported:2,classified:1,failed:1,skipped:0,error:'模型暂时不可用'},nextRunAt:'2026-11-20T10:00:00Z'});
    await act(async()=>{tick();});
    await waitFor(()=>assert.ok(ui.queryByText('模型暂时不可用')));
    assert.ok(ui.getByText('导入 2 · 已分类 1 · 跳过 0 · 失败 1'));
    assert.equal((ui.getByLabelText('重要发件人（每行一个完整邮箱地址）') as HTMLTextAreaElement).value,'unsaved@example.invalid');assert.equal(dirty.at(-1),true);assert.equal(configReads,1);
  } finally {globalThis.setInterval=originalInterval;}
});

test('旧版本缺少自动整理接口时显示兼容说明，不触发其他邮件操作',()=>{
  delete surface.mailAutomation;
  const ui=render(<MailAutomationPanel accounts={[account]} onReview={()=>{}}/>);
  assert.ok(ui.getByText('此版本暂未提供自动整理接口，请更新应用后重试。'));
});

test('未保存设置时来源跳转被阻止，恢复已保存设置后可查看原文',async()=>{
  setup();let opened=0;const dirty:boolean[]=[];
  const ui=render(<MailAutomationPanel accounts={[account]} onReview={()=>{}} onOpenMail={()=>{opened++;}} onDirty={value=>dirty.push(value)}/>);await ready(ui);
  assert.ok(ui.getByText(/立即整理只运行一次/));
  fireEvent.change(ui.getByLabelText('重要发件人（每行一个完整邮箱地址）'),{target:{value:'unsaved@example.invalid'}});
  const source=ui.getByRole('button',{name:'查看原邮件 课程报告提交'});
  assert.equal((source as HTMLButtonElement).disabled,true);fireEvent.click(source);assert.equal(opened,0);
  assert.ok(ui.getByText(/查看原邮件前，请先保存或恢复已保存设置/));
  fireEvent.click(ui.getByRole('button',{name:'恢复已保存设置'}));
  await waitFor(()=>assert.equal(dirty.at(-1),false));
  assert.equal((ui.getByLabelText('重要发件人（每行一个完整邮箱地址）') as HTMLTextAreaElement).value,'');
  fireEvent.click(ui.getByRole('button',{name:'查看原邮件 课程报告提交'}));assert.equal(opened,1);
});

test('StrictMode较早的配置请求迟到不会覆盖新请求后的用户编辑',async()=>{
  let reads=0;let oldReply!:(value:Result<MailAutomationConfig>)=>void;
  setup({getConfig:async()=>{reads++;if(reads===1)return new Promise(done=>{oldReply=done;});return ok({...structuredClone(defaultMailAutomationConfig),nextRunAt:null});}});
  const ui=render(<React.StrictMode><MailAutomationPanel accounts={[account]} onReview={()=>{}}/></React.StrictMode>);await ready(ui);
  fireEvent.change(ui.getByLabelText('重要发件人（每行一个完整邮箱地址）'),{target:{value:'new-edit@example.invalid'}});
  await act(async()=>oldReply(ok({...structuredClone(defaultMailAutomationConfig),importantSenders:['stale@example.invalid'],nextRunAt:null})));
  assert.equal((ui.getByLabelText('重要发件人（每行一个完整邮箱地址）') as HTMLTextAreaElement).value,'new-edit@example.invalid');
  assert.ok(ui.getByText('设置尚未保存'));assert.equal(reads,2);
});

test('系统后台保存失败回滚后刷新实际开关，保留输入供修正重试',async()=>{
  let current:MailAutomationConfig={...structuredClone(defaultMailAutomationConfig),bindingIds:[account.id],enabled:true,nextRunAt:'2026-11-21T01:00:00Z'};
  const dirty:boolean[]=[];
  setup({
    getConfig:async()=>ok(structuredClone(current)),
    saveConfig:async input=>{current={...structuredClone(input),enabled:false,nextRunAt:null};return {ok:false,error:'系统后台未能应用设置，定时整理已停用'};},
    getBackgroundStatus:async()=>ok({supported:true,installed:false,active:false}),
  });
  const ui=render(<MailAutomationPanel accounts={[account]} onReview={()=>{}} onDirty={value=>dirty.push(value)}/>);await ready(ui);
  assert.ok(ui.getByText('定时整理已启用'));
  fireEvent.change(ui.getByLabelText('重要发件人（每行一个完整邮箱地址）'),{target:{value:'edited@example.invalid'}});
  fireEvent.click(ui.getByRole('button',{name:'保存并启用'}));
  await waitFor(()=>assert.ok(ui.queryByText('定时整理未启用')));
  assert.ok(ui.getByText('系统后台未能应用设置，定时整理已停用'));
  assert.equal((ui.getByLabelText('启用定时整理') as HTMLInputElement).checked,true);
  assert.equal((ui.getByLabelText('重要发件人（每行一个完整邮箱地址）') as HTMLTextAreaElement).value,'edited@example.invalid');
  assert.equal(dirty.at(-1),true);assert.equal(ui.queryByText('已保存并启用定时整理。'),null);
  fireEvent.click(ui.getByRole('button',{name:'恢复已保存设置'}));
  await waitFor(()=>assert.equal(dirty.at(-1),false));
  assert.equal((ui.getByLabelText('启用定时整理') as HTMLInputElement).checked,false);
});
