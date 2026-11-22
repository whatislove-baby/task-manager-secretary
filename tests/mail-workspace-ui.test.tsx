import React from 'react';
import {test,afterEach} from 'node:test';
import assert from 'node:assert/strict';
import {JSDOM} from 'jsdom';
import {createRequire} from 'node:module';
import {MailWorkspacePanel} from '../src/renderer/MailWorkspacePanel';
import {defaultMailFilter} from '../src/shared/mail';
import type {MailWorkspaceAPI,MailBinding,LocalMail,MailAnalysis} from '../src/shared/mail-workspace';
import type {DesktopAPI} from '../src/shared/contracts';
const dom=new JSDOM('<!doctype html><html><body></body></html>',{url:'http://localhost'});
for(const [key,value] of Object.entries({window:dom.window,document:dom.window.document,navigator:dom.window.navigator,HTMLElement:dom.window.HTMLElement}))Object.defineProperty(globalThis,key,{value,configurable:true});
const {render,fireEvent,waitFor,cleanup,within}=createRequire(process.cwd()+'/package.json')('@testing-library/react') as typeof import('@testing-library/react');
afterEach(()=>{cleanup();delete window.widget;Reflect.deleteProperty(window,'mailOperations');});
const ok=<T,>(data:T)=>({ok:true as const,data});
const bindings:MailBinding[]=['a','b'].map(id=>({id,method:'imap',state:{method:'imap',clientId:'',account:{id,email:id+'@example.com',name:id},filter:structuredClone(defaultMailFilter)}}));
const messages:LocalMail[]=['a','b'].map((id,i)=>({localId:id,bindingId:id,mailboxIdentity:id,accountEmail:id+'@example.com',cachedAt:'2026-10-30T00:00:00Z',analyzedAt:i?'2026-10-30T01:00:00Z':null,analysisId:i?'analysis':null,status:i?'analyzed':'unanalyzed',id:'remote-'+id,subject:'邮件'+id,sender:'teacher',receivedAt:'2026-10-30T00:00:00Z',preview:'',unread:false,webUrl:'',imported:false,body:'完整正文'+id}));
const analysis:MailAnalysis={id:'analysis',messageIds:['a','b'],instructions:'整理',notes:'请确认',drafts:[],createdAt:'2026-10-30T00:00:00Z',saved:false,model:'test-model',baseUrl:'https://ai.example/v1'};
function setup(overrides:Partial<MailWorkspaceAPI>={}){window.desktop={getSettings:async()=>ok({baseUrl:'https://ai.example/v1',model:'test-model',hasApiKey:true,dataDirectory:'data'})} as DesktopAPI;window.mailWorkspace={listPresets:async()=>ok([]),knownSenders:async()=>ok([]),listFoldersLocal:async()=>ok([]),getAccounts:async()=>ok(bindings),listLocal:async()=>ok({messages,total:messages.length}),getLocal:async id=>ok(messages.find(m=>m.localId===id)!),getAnalysis:async()=>ok(analysis),...overrides} as MailWorkspaceAPI;}
async function ready(ui:ReturnType<typeof render>){await waitFor(()=>assert.ok(!(ui.getByRole('button',{name:'添加邮箱'}) as HTMLButtonElement).disabled));}
test('离线加载所有邮箱本地邮件，不连接服务器；跨账号选择带用户要求一次分析',async()=>{let sent:unknown;setup({analyze:async input=>{sent=input;return ok(analysis);}});const ui=render(<MailWorkspacePanel onClose={()=>{}}/>);await ready(ui);assert.ok(ui.queryAllByText('未分析').length>0);assert.ok(ui.queryAllByText('已分析').length>0);fireEvent.click(ui.getByLabelText('选择邮件 邮件a'));fireEvent.click(ui.getByLabelText('选择邮件 邮件b'));fireEvent.change(ui.getByLabelText('给 AI 的操作要求'),{target:{value:'只找课程截止日期'}});fireEvent.click(ui.getByRole('button',{name:'分析已选 2 封'}));await waitFor(()=>assert.ok(ui.queryByText('02 / 审核与确认')));assert.deepEqual(sent,{messageIds:['a','b'],instructions:'只找课程截止日期',target:{baseUrl:'https://ai.example/v1',model:'test-model'}});});
test('导入默认仅新增，使用全部连接账号与各自保存的筛选范围',async()=>{let imported:unknown;setup({importMail:async input=>{imported=input;return ok({added:2,skipped:3,failed:0,limited:false,errors:[]});}});const ui=render(<MailWorkspacePanel onClose={()=>{}}/>);await ready(ui);fireEvent.click(ui.getByText('导入邮件',{selector:'summary'}));assert.equal((ui.getByLabelText('仅导入未保存的邮件') as HTMLInputElement).checked,true);fireEvent.click(ui.getByRole('button',{name:'导入所选邮箱'}));await waitFor(()=>assert.ok(imported));assert.deepEqual(imported,{bindingIds:['a','b'],onlyNew:true});await waitFor(()=>assert.ok(ui.queryByText(/新增 2 封/)));});
test('恢复已分析结果不重新发送AI，正文以文本展示；审核浮层关闭后回到原页面',async()=>{setup({getLocal:async id=>ok({...messages.find(m=>m.localId===id)!,body:'<script>unsafe()</script>'}),analyze:async()=>{throw Error('不应调用');}});const ui=render(<MailWorkspacePanel onClose={()=>{}}/>);await ready(ui);fireEvent.click(ui.getByText('邮件b').closest('button')!);await waitFor(()=>assert.ok(ui.queryByLabelText('邮件正文')));assert.ok(ui.getByLabelText('邮件正文').textContent?.includes('<script>'));assert.ok(ui.getByLabelText('邮件正文').querySelector('script')===null);fireEvent.click(ui.getByRole('button',{name:'查看这批分析结果'}));await waitFor(()=>assert.ok(ui.queryByText('02 / 审核与确认')));fireEvent.click(ui.getByRole('button',{name:'关闭工具面板'}));await waitFor(()=>assert.equal(ui.queryByText('02 / 审核与确认'),null));assert.ok(ui.getByLabelText('邮件正文'));});
test('账号设置默认隐藏读取范围，选择默认预设并应用后写入收件箱最近7天',async()=>{let saved:unknown;setup({listPresets:async()=>ok([{id:'preset-default',bindingId:'a',name:'默认预设',filter:{...defaultMailFilter}}]),saveFilter:async(id,filter)=>{saved={id,filter};return ok({...bindings[0],state:{...bindings[0].state,filter}});}});const ui=render(<MailWorkspacePanel onClose={()=>{}}/>);await ready(ui);fireEvent.click(ui.getByRole('button',{name:'设置 a@example.com'}));await waitFor(()=>assert.ok(ui.getByRole('button',{name:'新建预设'})));assert.equal(ui.queryByLabelText('最近天数'),null);assert.equal(ui.queryByRole('button',{name:'加载文件夹'}),null);assert.equal(ui.queryByLabelText('包含这些发件人（每行一个，可自行添加）'),null);const select=ui.getByLabelText('已保存的预设') as HTMLSelectElement;assert.deepEqual([...select.options].map(option=>option.textContent),['默认预设']);fireEvent.change(select,{target:{value:'preset-default'}});fireEvent.click(ui.getByRole('button',{name:'应用这些读取条件'}));await waitFor(()=>assert.ok(saved));assert.deepEqual(saved,{id:'a',filter:{...defaultMailFilter}});});
test('当前条件不匹配任何预设时，下拉回退到默认预设且修改预设始终可用',async()=>{
 const custom={...defaultMailFilter,subject:'课程通知'};
 const binding:MailBinding={id:'a',method:'imap',state:{method:'imap',clientId:'',account:{id:'a',email:'a@example.com',name:'a'},filter:custom}};
 const defaultPreset={id:'p-default',bindingId:'a',name:'默认预设',filter:{...defaultMailFilter}};
 setup({getAccounts:async()=>ok([binding]),listPresets:async()=>ok([defaultPreset])});
 const ui=render(<MailWorkspacePanel onClose={()=>{}}/>);await ready(ui);fireEvent.click(ui.getByRole('button',{name:'设置 a@example.com'}));await waitFor(()=>assert.ok(ui.getByRole('button',{name:'新建预设'})));
 assert.equal((ui.getByLabelText('已保存的预设') as HTMLSelectElement).value,'p-default');
 const modify=ui.getByRole('button',{name:'修改预设'}) as HTMLButtonElement;assert.equal(modify.disabled,false);fireEvent.click(modify);
 assert.equal((ui.getByLabelText('预设名称') as HTMLInputElement).value,'默认预设');
});
test('预设编辑未保存时返回邮件工作台先确认，取消不改已应用条件',async()=>{setup();const ui=render(<MailWorkspacePanel onClose={()=>{}}/>);await ready(ui);fireEvent.click(ui.getByRole('button',{name:'设置 a@example.com'}));await waitFor(()=>assert.ok(ui.getByRole('button',{name:'新建预设'})));fireEvent.click(ui.getByRole('button',{name:'新建预设'}));fireEvent.change(ui.getByLabelText('最近天数'),{target:{value:'14'}});fireEvent.click(ui.getByRole('button',{name:'返回邮件工作台'}));const confirm=ui.getByRole('alertdialog');fireEvent.click(within(confirm).getByRole('button',{name:'继续设置'}));assert.equal((ui.getByLabelText('最近天数') as HTMLInputElement).value,'14');fireEvent.click(ui.getByRole('button',{name:'取消'}));assert.equal(ui.queryByLabelText('最近天数'),null);fireEvent.click(ui.getByRole('button',{name:'返回邮件工作台'}));await waitFor(()=>assert.ok(ui.getByRole('button',{name:'添加邮箱'})));assert.equal(ui.queryByRole('button',{name:'新建预设'}),null);});

test('后台分析立刻返回，任务可暂停且收起不要求等待完成',async()=>{let started:unknown,paused='',closed=0;setup();const job={id:'job',kind:'analysis' as const,state:'running' as const,label:'分析两封邮件',done:1,total:2,createdAt:'2026-10-30',updatedAt:'2026-10-30'};window.mailOperations={listJobs:async()=>ok([job]),startAnalysis:async (input:any)=>{started=input;return ok(job);},pauseJob:async (id:string)=>{paused=id;return ok({...job,state:'paused' as const});}} as unknown as import('../src/shared/mail-operations').MailOperationsAPI;const ui=render(<MailWorkspacePanel onClose={()=>closed++}/>);await ready(ui);fireEvent.click(ui.getByLabelText('选择邮件 邮件a'));fireEvent.click(ui.getByRole('button',{name:'分析已选 1 封'}));await waitFor(()=>assert.ok(started));await waitFor(()=>assert.ok(!(ui.getByRole('button',{name:'收起邮件窗口'}) as HTMLButtonElement).disabled));fireEvent.click(ui.getByRole('button',{name:'暂停'}));await waitFor(()=>assert.equal(paused,'job'));fireEvent.click(ui.getByRole('button',{name:'收起邮件窗口'}));assert.equal(closed,1);});

test('本地删除先确认且说明保留导入记忆，确认只传本地邮件标识',async()=>{let deleted:unknown;setup({deleteLocal:async ids=>{deleted=ids;return ok({count:ids.length});}});const ui=render(<MailWorkspacePanel onClose={()=>{}}/>);await ready(ui);fireEvent.click(ui.getByLabelText('选择邮件 邮件a'));fireEvent.click(ui.getByText(/归档与删除 · 已选/,{selector:'summary'}));fireEvent.click(ui.getByRole('button',{name:'删除本地邮件'}));assert.ok(ui.queryByText(/导入与分析记忆保留/));assert.equal(deleted,undefined);fireEvent.click(ui.getByRole('button',{name:'确认删除正文'}));await waitFor(()=>assert.deepEqual(deleted,['a']));});

test('可恢复持久对话并继续追问，不需要重新复制邮件',async()=>{let sent:unknown;setup();const conversation={id:'c',title:'课程分析',createdAt:'2026-10-30',updatedAt:'2026-10-30',turnCount:1,messageIds:['a'],turns:[{id:'t',role:'assistant' as const,content:'课程截止时间汇总',createdAt:'2026-10-30'}]};window.mailOperations={...window.mailOperations,listJobs:async()=>ok([]),listConversations:async()=>ok([conversation]),getConversation:async()=>ok(conversation),continueConversation:async(id:string,text:string,target:any)=>{sent={id,text,target};return ok({id:'j',kind:'chat',state:'queued',label:'继续',done:0,total:1,createdAt:'2026-10-30',updatedAt:'2026-10-30'});}} as unknown as import('../src/shared/mail-operations').MailOperationsAPI;const ui=render(<MailWorkspacePanel onClose={()=>{}}/>);await ready(ui);fireEvent.click(ui.getByRole('button',{name:'AI 对话历史'}));await waitFor(()=>assert.ok(ui.queryByText('课程分析')));fireEvent.click(ui.getByText('课程分析'));await waitFor(()=>assert.ok(ui.queryByText('课程截止时间汇总')));fireEvent.change(ui.getByLabelText('继续这次对话'),{target:{value:'再帮我按截止日期排序'}});fireEvent.click(ui.getByRole('button',{name:'发送并继续交流'}));await waitFor(()=>assert.ok(sent));assert.deepEqual(sent,{id:'c',text:'再帮我按截止日期排序',target:{baseUrl:'https://ai.example/v1',model:'test-model'}});});


test('实际使用预设与编辑中的草稿分开显示，取消编辑不改当前条件',async()=>{
 const named={id:'p1',bindingId:'a',name:'课程通知',filter:{...defaultMailFilter}};
 setup({listPresets:async()=>ok([named])});const ui=render(<MailWorkspacePanel onClose={()=>{}}/>);await ready(ui);fireEvent.click(ui.getByRole('button',{name:'设置 a@example.com'}));
 await waitFor(()=>assert.ok(ui.queryByText('当前使用：课程通知')));assert.ok(ui.getByLabelText('当前使用的读取预设').textContent?.includes('最近 7 天'));
 fireEvent.click(ui.getByRole('button',{name:'修改预设'}));fireEvent.change(ui.getByLabelText('最近天数'),{target:{value:'14'}});assert.ok(ui.getByLabelText('当前使用的读取预设').textContent?.includes('最近 7 天'));
 fireEvent.click(ui.getByRole('button',{name:'取消'}));assert.ok(ui.getByLabelText('当前使用的读取预设').textContent?.includes('最近 7 天'));assert.equal(ui.queryByLabelText('最近天数'),null);
});

test('左侧保留设置导航，邮件执行操作全部位于中栏且进度明确邮件与批次',async()=>{
 setup();window.mailOperations={listJobs:async()=>ok([{id:'j',kind:'analysis',state:'running',label:'分析邮件',messageCount:15,done:0,total:4,createdAt:'2026-10-31',updatedAt:'2026-10-31'}])} as unknown as unknown as import('../src/shared/mail-operations').MailOperationsAPI;
 const ui=render(<MailWorkspacePanel onClose={()=>{}}/>);await ready(ui);await waitFor(()=>assert.ok(ui.queryByText(/15 封邮件 · 分析批次 0 \/ 4/)));
 const left=ui.container.ownerDocument.querySelector('.mw-accounts')!;const middle=ui.getByLabelText('本地邮件');
 assert.ok(!left.querySelector('.mw-import'));assert.ok(!left.querySelector('.mw-analysis'));assert.ok(middle.querySelector('.mw-import'));assert.ok(!middle.querySelector('.mw-analysis'));const ai=ui.getByLabelText('AI 分析操作');assert.ok(ai.closest('.mw-content'));assert.ok(!ai.closest('.mw-layout'));
 assert.ok(ui.getByText('邮件a').closest('.mw-row')?.classList.contains('unanalyzed'));assert.ok(ui.getByText('邮件b').closest('.mw-row')?.classList.contains('analyzed'));
});


test('预设列表只显示已保存预设，新建与修改都从按钮进入独立编辑界面',async()=>{
 const presets=[{id:'p1',bindingId:'a',name:'课程通知',filter:structuredClone(defaultMailFilter)}];const writes:{id?:string;name:string;filter:typeof defaultMailFilter}[]=[];
 setup({listPresets:async()=>ok([...presets]),savePreset:async(binding,input)=>{writes.push(structuredClone(input));const saved={id:input.id??'p2',bindingId:binding,name:input.name,filter:input.filter};const index=presets.findIndex(p=>p.id===saved.id);if(index<0)presets.push(saved);else presets[index]=saved;return ok(saved);}});
 const ui=render(<MailWorkspacePanel onClose={()=>{}}/>);await ready(ui);fireEvent.click(ui.getByRole('button',{name:'设置 a@example.com'}));await waitFor(()=>assert.ok(ui.queryByText('当前使用：课程通知')));
 assert.equal(ui.queryByText('新建预设（不修改已有预设）'),null);assert.equal(ui.queryByLabelText('预设名称'),null);assert.equal(ui.queryByLabelText('最近天数'),null);
 fireEvent.click(ui.getByRole('button',{name:'新建预设'}));assert.ok(ui.getByLabelText('预设名称'));assert.equal((ui.getByLabelText('预设名称') as HTMLInputElement).value,'');assert.ok(ui.getByLabelText('最近天数'));
 fireEvent.change(ui.getByLabelText('预设名称'),{target:{value:'作业通知'}});fireEvent.click(ui.getByRole('button',{name:'保存新预设'}));await waitFor(()=>assert.equal(writes.length,1));assert.equal(writes[0].id,undefined);assert.equal(presets[0].name,'课程通知');
 await waitFor(()=>assert.equal(ui.queryByLabelText('预设名称'),null));
 fireEvent.change(ui.getByLabelText('已保存的预设'),{target:{value:'p1'}});fireEvent.click(ui.getByRole('button',{name:'修改预设'}));assert.equal((ui.getByLabelText('预设名称') as HTMLInputElement).value,'课程通知');
 fireEvent.change(ui.getByLabelText('预设名称'),{target:{value:'课程重要通知'}});fireEvent.click(ui.getByRole('button',{name:'保存修改'}));await waitFor(()=>assert.equal(writes.length,2));assert.equal(writes[1].id,'p1');assert.equal(presets[0].name,'课程重要通知');assert.equal(presets[1].name,'作业通知');
});

test('新建预设可取消并返回列表，不写入任何预设',async()=>{
 let writes=0;setup({listPresets:async()=>ok([{id:'p1',bindingId:'a',name:'课程通知',filter:{...defaultMailFilter}}]),savePreset:async()=>{writes++;return ok({id:'p',bindingId:'a',name:'x',filter:{...defaultMailFilter}});}});
 const ui=render(<MailWorkspacePanel onClose={()=>{}}/>);await ready(ui);fireEvent.click(ui.getByRole('button',{name:'设置 a@example.com'}));await waitFor(()=>assert.ok(ui.queryByText('当前使用：课程通知')));
 fireEvent.click(ui.getByRole('button',{name:'新建预设'}));assert.ok(ui.getByLabelText('最近天数'));fireEvent.change(ui.getByLabelText('预设名称'),{target:{value:'临时'}});fireEvent.click(ui.getByRole('button',{name:'取消'}));
 assert.equal(ui.queryByLabelText('预设名称'),null);assert.equal(ui.queryByLabelText('最近天数'),null);assert.equal(writes,0);assert.ok(ui.getByRole('button',{name:'新建预设'}));
});

test('旧版单值发件人并入多值列表，已知发件人可按单选目标加入排除列表',async()=>{
 const legacy={...defaultMailFilter,sender:'old@school.edu',senderExclude:'spam@school.edu'};
 const binding:MailBinding={id:'a',method:'imap',state:{method:'imap',clientId:'',account:{id:'a',email:'a@example.com',name:'a'},filter:legacy}};
 setup({getAccounts:async()=>ok([binding]),knownSenders:async()=>ok(['new@school.edu'])});
 const ui=render(<MailWorkspacePanel onClose={()=>{}}/>);await ready(ui);fireEvent.click(ui.getByRole('button',{name:'设置 a@example.com'}));await waitFor(()=>assert.ok(ui.getByRole('button',{name:'新建预设'})));
 fireEvent.click(ui.getByRole('button',{name:'新建预设'}));
 const include=ui.getByLabelText('包含这些发件人（每行一个，可自行添加）') as HTMLTextAreaElement,exclude=ui.getByLabelText('排除这些发件人（每行一个）') as HTMLTextAreaElement;
 assert.equal(include.value,'old@school.edu');assert.equal(exclude.value,'spam@school.edu');
 await waitFor(()=>assert.ok(ui.queryByText(/从已知发件人中选择/)));fireEvent.click(ui.getByText(/从已知发件人中选择/));fireEvent.click(ui.getByLabelText('加入“排除这些发件人”'));fireEvent.click(ui.getByRole('checkbox',{name:/new@school\.edu/}));
 assert.equal(exclude.value,'spam@school.edu\nnew@school.edu');assert.equal(include.value,'old@school.edu');
 fireEvent.click(ui.getByLabelText('加入“包含这些发件人”'));assert.equal((ui.getByRole('checkbox',{name:/new@school\.edu/}) as HTMLInputElement).checked,false);
});

test('预设编辑内可选择文件夹并随预设保存',async()=>{
 let written:{name:string;filter:typeof defaultMailFilter}|undefined;
 setup({listFolders:async()=>ok([{id:'inbox',name:'课程通知文件夹',childCount:0,unreadCount:0,parentId:null}]),savePreset:async(binding,input)=>{written={name:input.name,filter:input.filter};return ok({id:'p1',bindingId:binding,name:input.name,filter:input.filter});}});
 const ui=render(<MailWorkspacePanel onClose={()=>{}}/>);await ready(ui);fireEvent.click(ui.getByRole('button',{name:'设置 a@example.com'}));await waitFor(()=>assert.ok(ui.getByRole('button',{name:'新建预设'})));
 fireEvent.click(ui.getByRole('button',{name:'新建预设'}));fireEvent.click(ui.getByRole('button',{name:'加载文件夹'}));await waitFor(()=>assert.ok(ui.queryByLabelText('课程通知文件夹')));fireEvent.click(ui.getByLabelText('课程通知文件夹'));
 fireEvent.change(ui.getByLabelText('预设名称'),{target:{value:'课程文件夹'}});fireEvent.click(ui.getByRole('button',{name:'保存新预设'}));await waitFor(()=>assert.ok(written));assert.deepEqual(written?.filter.folderIds,['inbox']);
});

test('后台任务完成后只提示一次，可关闭且不再显示；已确认的历史任务不出现',async()=>{
 const completed={id:'j1',kind:'analysis' as const,state:'completed' as const,label:'分析邮件',messageCount:2,done:2,total:2,createdAt:'2026-10-31',updatedAt:'2026-10-31',notificationAcknowledged:false};
 let acknowledged='';setup();window.mailOperations={listJobs:async()=>ok([completed]),acknowledgeJob:async (id:string)=>{acknowledged=id;return ok(undefined);}} as unknown as unknown as import('../src/shared/mail-operations').MailOperationsAPI;
 const ui=render(<MailWorkspacePanel onClose={()=>{}}/>);await ready(ui);await waitFor(()=>assert.ok(ui.queryByText(/已完成/)));
 fireEvent.click(ui.getByRole('button',{name:'关闭任务提示 分析邮件'}));await waitFor(()=>assert.equal(acknowledged,'j1'));assert.equal(ui.queryByText(/已完成/),null);
 cleanup();
 window.mailOperations={listJobs:async()=>ok([{...completed,notificationAcknowledged:true}])} as unknown as unknown as import('../src/shared/mail-operations').MailOperationsAPI;
 const second=render(<MailWorkspacePanel onClose={()=>{}}/>);await ready(second);await new Promise(resolve=>setTimeout(resolve,50));assert.equal(second.queryByText(/已完成/),null);
});

test('本地文件夹树支持行内重命名',async()=>{
 let renamed:unknown;setup({listFoldersLocal:async()=>ok([{id:'f1',name:'课程',createdAt:'2026-10-30'}]),renameLocalFolder:async(id,name)=>{renamed={id,name};return ok({id,name,createdAt:'2026-10-30'});}});
 const ui=render(<MailWorkspacePanel onClose={()=>{}}/>);await ready(ui);await waitFor(()=>assert.ok(ui.getByRole('button',{name:'课程'})));
 fireEvent.click(ui.getByRole('button',{name:'重命名文件夹 课程'}));const input=ui.getByLabelText('新文件夹名称 课程');fireEvent.change(input,{target:{value:'学校'}});
 const form=(input as HTMLElement).closest('form')!;fireEvent.click(within(form).getByRole('button',{name:'保存'}));
 await waitFor(()=>assert.deepEqual(renamed,{id:'f1',name:'学校'}));
});

test('设置内可删除邮箱绑定，确认后从邮箱列表移除并保留本地记录',async()=>{
 let removed='';let current=[...bindings];
 setup({getAccounts:async()=>ok(current),removeAccount:async(id:string)=>{removed=id;current=current.filter(binding=>binding.id!==id);return ok(undefined);}});
 const ui=render(<MailWorkspacePanel onClose={()=>{}}/>);await ready(ui);fireEvent.click(ui.getByRole('button',{name:'设置 a@example.com'}));await waitFor(()=>assert.ok(ui.getByRole('button',{name:'删除此邮箱'})));
 fireEvent.click(ui.getByRole('button',{name:'删除此邮箱'}));const dialog=ui.getByRole('alertdialog',{name:'删除邮箱确认'});assert.ok(within(dialog).getByText(/本地邮件、分析、对话与预设保留/));
 fireEvent.click(within(dialog).getByRole('button',{name:'确认删除'}));
 await waitFor(()=>assert.equal(removed,'a'));await waitFor(()=>assert.equal(ui.queryByRole('button',{name:'设置 a@example.com'}),null));assert.ok(ui.getByRole('button',{name:'添加邮箱'}));
});

test('导入显示当前条件匹配的命名预设，AI操作位于三列之外的底部',async()=>{
 setup({listPresets:async (id:string)=>ok([{id:'preset-'+id,bindingId:id,name:'课程邮件',filter:{...defaultMailFilter,senderExclude:''}}])});const ui=render(<MailWorkspacePanel onClose={()=>{}}/>);await ready(ui);fireEvent.click(ui.getByText('导入邮件',{selector:'summary'}));await waitFor(()=>assert.ok(ui.queryAllByText(/当前预设「课程邮件」/).length===2));const ai=ui.getByLabelText('AI 分析操作');assert.ok(!ai.closest('.mw-library'));assert.ok(!ai.closest('.mw-layout'));assert.ok(ai.textContent?.includes('默认逐封独立分析'));
});

test('后台任务取消需要确认，取消调用主进程且不作为完成提示',async()=>{
 let canceled='';setup();const job={id:'job-cancel',kind:'import' as const,state:'running' as const,label:'导入邮件',done:1,total:3,createdAt:'2026-10-30',updatedAt:'2026-10-30'};
 window.mailOperations={listJobs:async()=>ok([job]),cancelJob:async(id:string)=>{canceled=id;return ok({...job,state:'canceled' as const,notificationAcknowledged:true});}} as unknown as import('../src/shared/mail-operations').MailOperationsAPI;
 const ui=render(<MailWorkspacePanel onClose={()=>{}}/>);await ready(ui);await waitFor(()=>assert.ok(ui.getByRole('button',{name:'取消任务'})));
 fireEvent.click(ui.getByRole('button',{name:'取消任务'}));assert.equal(canceled,'');
 fireEvent.click(ui.getByRole('button',{name:'确认取消本次任务'}));await waitFor(()=>assert.equal(canceled,'job-cancel'));
});

test('导入失败明细显示邮件、阶段、原因并支持单封与全部重试；关闭提示后记录仍可查看',async()=>{
 const retries:unknown[]=[];setup();const job={id:'job-failed',kind:'import' as const,state:'completed' as const,label:'导入邮件',done:2,total:2,createdAt:'2026-10-30',updatedAt:'2026-10-30',report:{added:1,skipped:0,failed:1,limited:false,errors:[],failures:[{key:'k1',bindingId:'a',mailboxEmail:'a@example.com',remoteId:'r1',identity:'a',subject:'失败邮件',sender:'sender@example.org',receivedAt:'2026-10-30T00:00:00Z',stage:'transfer' as const,code:'TRANSFER_FAILED',reason:'下载正文中断',retryable:true}],mailboxErrors:[]}};
 window.mailOperations={listJobs:async()=>ok([job]),retryImportFailures:async (input:any)=>{retries.push(input);return ok({...job,id:'retry'});},acknowledgeJob:async()=>ok(undefined)} as unknown as import('../src/shared/mail-operations').MailOperationsAPI;
 const ui=render(<MailWorkspacePanel onClose={()=>{}}/>);await ready(ui);const jobs=()=>document.body.querySelector('.mw-jobs') as HTMLElement;await waitFor(()=>assert.ok(within(jobs()).getByText('失败明细（1 封）')));
 assert.ok(within(jobs()).getByText('失败邮件'));assert.ok(within(jobs()).getByText(/sender@example.org/));assert.ok(within(jobs()).getAllByText(/下载正文/).length>0);assert.ok(within(jobs()).getByText(/TRANSFER_FAILED/));
 fireEvent.click(within(jobs()).getByRole('button',{name:'重试这一封'}));await waitFor(()=>assert.deepEqual(retries,[{jobId:'job-failed',keys:['k1']}]));
 fireEvent.click(within(jobs()).getByRole('button',{name:'重试全部失败邮件（1）'}));await waitFor(()=>assert.deepEqual(retries.at(-1),{jobId:'job-failed'}));
});

test('正文待读取与附件信息清楚显示，长正文在独立阅读窗格查看',async()=>{
 setup({getLocal:async id=>ok({...messages.find(m=>m.localId===id)!,body:'很长正文'.repeat(2000),bodyStatus:'partial' as const,bodyNote:'已保存前10MB',attachments:[{name:'report.pdf',type:'application/pdf',size:2048,inline:false}]})});
 const ui=render(<MailWorkspacePanel onClose={()=>{}}/>);await ready(ui);fireEvent.click(ui.getByText('邮件a').closest('button')!);
 await waitFor(()=>assert.ok(ui.getByText('正文尚未完整')));
 assert.ok(ui.getByText(/report.pdf/));assert.ok(ui.getByText(/已保存前10MB/));
 fireEvent.click(ui.getByRole('button',{name:'在阅读窗格查看全文'}));await waitFor(()=>assert.ok(ui.getByRole('dialog',{name:/邮件a/})));
 fireEvent.keyDown(document,{key:'Escape'});await waitFor(()=>assert.equal(ui.queryByRole('dialog',{name:/邮件a/}),null));
});
