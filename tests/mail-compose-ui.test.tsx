import React from 'react';
import {test,afterEach} from 'node:test';
import assert from 'node:assert/strict';
import {JSDOM} from 'jsdom';
import {createRequire} from 'node:module';
import {MailComposePanel} from '../src/renderer/MailComposePanel';
import {MailWorkspacePanel} from '../src/renderer/MailWorkspacePanel';
import type {MailComposeAPI,ComposeReceipt} from '../src/shared/mail-compose';
import type {MailBinding,LocalMail} from '../src/shared/mail-workspace';
import type {DesktopAPI} from '../src/shared/contracts';
import {defaultMailFilter} from '../src/shared/mail';
const dom=new JSDOM('<!doctype html><html><body></body></html>',{url:'http://localhost'});
for(const [key,value] of Object.entries({window:dom.window,document:dom.window.document,navigator:dom.window.navigator,HTMLElement:dom.window.HTMLElement}))Object.defineProperty(globalThis,key,{value,configurable:true});
const {render,fireEvent,waitFor,cleanup,within}=createRequire(process.cwd()+'/package.json')('@testing-library/react') as typeof import('@testing-library/react');
afterEach(()=>{cleanup();});
const ok=<T,>(data:T)=>({ok:true as const,data});
const account:MailBinding={id:'mail-a',method:'imap',state:{method:'imap',clientId:'',account:{id:'a',email:'student@example.com',name:'Student'},imap:{email:'student@example.com',username:'student@example.com',host:'imap.example.com',port:993,security:'tls',hasSecret:true},filter:structuredClone(defaultMailFilter)}};
const config={bindingId:'mail-a',email:'student@example.com',host:'smtp.example.com',port:465,security:'ssl' as const,configured:true,hasSecret:true};
const receipt:ComposeReceipt={id:'prepared-a',bindingId:'mail-a',from:'student@example.com',to:'teacher@example.com',subject:'作业问题',body:'请问第 3 题能否使用 Python？',messageId:'<test@example.com>',createdAt:'2026-11-20T12:00:00Z',status:'prepared',detail:'尚未发送'};
function setup(overrides:Partial<MailComposeAPI>={},hasApiKey=true){window.desktop={getSettings:async()=>ok({baseUrl:'https://ai.example/v1',model:'test-model',hasApiKey,dataDirectory:'data'})} as DesktopAPI;window.mailCompose={getConfig:async()=>ok(config),saveConfig:async()=>ok(config),generate:async()=>ok({subject:'作业问题',body:'请问第 3 题能否使用 Python？'}),prepare:async(input)=>ok({...receipt,...input}),send:async()=>ok({...receipt,status:'sent',detail:'邮件服务器已接受'}),listRecent:async()=>ok([]),...overrides};}
async function ready(ui:ReturnType<typeof render>){await waitFor(()=>assert.equal((ui.getByLabelText('选择发件账号') as HTMLSelectElement).disabled,false));}
function fill(ui:ReturnType<typeof render>){fireEvent.change(ui.getByLabelText('收件人（一个邮箱地址）'),{target:{value:receipt.to}});fireEvent.change(ui.getByLabelText('邮件主题'),{target:{value:receipt.subject}});fireEvent.change(ui.getByLabelText('邮件正文'),{target:{value:receipt.body}});}
test('AI 起草只更新可编辑主题正文，不替换收件人，不调用 SMTP',async()=>{let generated:unknown,sendCount=0;setup({generate:async input=>{generated=input;return ok({subject:'作业问题',body:'草稿正文'});},send:async()=>{sendCount++;return ok({...receipt,status:'sent'});}});const ui=render(<MailComposePanel accounts={[account]}/>);await ready(ui);fireEvent.change(ui.getByLabelText('收件人（一个邮箱地址）'),{target:{value:'chosen@example.com'}});fireEvent.change(ui.getByLabelText('写信要求'),{target:{value:'礼貌询问老师'}});fireEvent.click(ui.getByRole('button',{name:'AI 起草／润色'}));await waitFor(()=>assert.equal((ui.getByLabelText('邮件正文') as HTMLTextAreaElement).value,'草稿正文'));assert.equal((ui.getByLabelText('收件人（一个邮箱地址）') as HTMLInputElement).value,'chosen@example.com');assert.equal(sendCount,0);assert.deepEqual(generated,{instructions:'礼貌询问老师',subject:'',body:'',target:{baseUrl:'https://ai.example/v1',model:'test-model'}});});
test('准备发送只创建预览，必须第二次明确确认才发送，连续点击只调用一次',async()=>{let prepareCount=0,sendCount=0;setup({prepare:async input=>{prepareCount++;return ok({...receipt,...input});},send:async()=>{sendCount++;await new Promise(resolve=>setTimeout(resolve,10));return ok({...receipt,status:'sent',detail:'SMTP accepted'});}});const ui=render(<MailComposePanel accounts={[account]}/>);await ready(ui);fill(ui);fireEvent.click(ui.getByRole('button',{name:'预览并准备发送'}));await waitFor(()=>assert.ok(ui.getByRole('button',{name:'确认发送这封邮件'})));assert.equal(prepareCount,1);assert.equal(sendCount,0);assert.ok(within(ui.getByLabelText('发送前预览')).getByText(receipt.body));const button=ui.getByRole('button',{name:'确认发送这封邮件'});fireEvent.click(button);fireEvent.click(button);await waitFor(()=>assert.ok(ui.queryByText('SMTP accepted')));assert.equal(sendCount,1);assert.equal(ui.queryByRole('button',{name:'确认发送这封邮件'}),null);assert.equal((ui.getByLabelText('邮件正文') as HTMLTextAreaElement).disabled,true);});
test('修改已预览的收件人会清除确认按钮，旧快照不能在当前编辑内容下发送',async()=>{let sends=0;setup({send:async()=>{sends++;return ok({...receipt,status:'sent'});}});const ui=render(<MailComposePanel accounts={[account]}/>);await ready(ui);fill(ui);fireEvent.click(ui.getByRole('button',{name:'预览并准备发送'}));await waitFor(()=>assert.ok(ui.getByRole('button',{name:'确认发送这封邮件'})));fireEvent.change(ui.getByLabelText('收件人（一个邮箱地址）'),{target:{value:'different@example.com'}});assert.equal(ui.queryByRole('button',{name:'确认发送这封邮件'}),null);assert.equal(ui.queryByLabelText('发送前预览'),null);assert.equal(sends,0);});
test('发送结果不确定时锁定原快照，不提供自动重试或再次发送按钮',async()=>{let sends=0;setup({send:async()=>{sends++;return ok({...receipt,status:'uncertain',detail:'DATA 后连接中断，可能已被接受'});}});const ui=render(<MailComposePanel accounts={[account]}/>);await ready(ui);fill(ui);fireEvent.click(ui.getByRole('button',{name:'预览并准备发送'}));await waitFor(()=>assert.ok(ui.getByRole('button',{name:'确认发送这封邮件'})));fireEvent.click(ui.getByRole('button',{name:'确认发送这封邮件'}));await waitFor(()=>assert.ok(ui.queryByText('发送结果不确定')));assert.equal(sends,1);assert.equal(ui.queryByRole('button',{name:'确认发送这封邮件'}),null);assert.equal((ui.getByRole('button',{name:'预览并准备发送'}) as HTMLButtonElement).disabled,true);assert.ok(ui.getByText(/此快照不会自动重发/));});
test('IPC 发送异常也不允许盲目重发，刷新记录后由用户核实',async()=>{setup({send:async()=>{throw Error('连接中断');}});const ui=render(<MailComposePanel accounts={[account]}/>);await ready(ui);fill(ui);fireEvent.click(ui.getByRole('button',{name:'预览并准备发送'}));await waitFor(()=>assert.ok(ui.getByRole('button',{name:'确认发送这封邮件'})));fireEvent.click(ui.getByRole('button',{name:'确认发送这封邮件'}));await waitFor(()=>assert.ok(ui.getByRole('alert').textContent?.includes('发送状态暂时无法确认')));assert.equal(ui.queryByRole('button',{name:'确认发送这封邮件'}),null);assert.equal((ui.getByRole('button',{name:'预览并准备发送'}) as HTMLButtonElement).disabled,true);});
test('根据来信起草携带明确本地来源，显示原发件人但不自动填写收件地址',async()=>{let generated:any;setup({generate:async input=>{generated=input;return ok({subject:'Re: 项目要求',body:'谢谢老师，我会按时提交。'});}});const source:LocalMail={localId:'local-a',bindingId:'mail-a',mailboxIdentity:'a',accountEmail:'student@example.com',cachedAt:'2026-11-20T12:00:00Z',analyzedAt:null,analysisId:null,status:'unanalyzed',id:'remote-a',subject:'项目要求',sender:'Teacher <teacher@example.com>',receivedAt:'2026-11-20T12:00:00Z',preview:'请在下周提交',body:'请在下周提交',unread:false,webUrl:'',imported:false};const ui=render(<MailComposePanel accounts={[account]} replySource={source}/>);await ready(ui);assert.equal((ui.getByLabelText('收件人（一个邮箱地址）') as HTMLInputElement).value,'');assert.ok(ui.getByText(/Teacher <teacher@example.com>/));fireEvent.click(ui.getByRole('button',{name:'AI 起草／润色'}));await waitFor(()=>assert.ok(generated));assert.equal(generated.replyToLocalId,'local-a');});
test('没有账号或模型 Key 也能手写并复制，AI 返回具体配置引导',async()=>{let copied='';Object.defineProperty(navigator,'clipboard',{value:{writeText:async(value:string)=>{copied=value;}},configurable:true});setup({},false);const ui=render(<MailComposePanel accounts={[]}/>);fill(ui);fireEvent.click(ui.getByRole('button',{name:'复制正文'}));await waitFor(()=>assert.equal(copied,receipt.body));fireEvent.click(ui.getByRole('button',{name:'AI 起草／润色'}));await waitFor(()=>assert.ok(ui.getByRole('alert').textContent?.includes('设置 → AI 服务')));assert.equal((ui.getByLabelText('邮件正文') as HTMLTextAreaElement).value,receipt.body);assert.equal((ui.getByRole('button',{name:'预览并准备发送'}) as HTMLButtonElement).disabled,true);});
test('SMTP 更改后阻止预览直到显式保存，密码不进入发送设置对象',async()=>{let saved:unknown;setup({saveConfig:async(id,input)=>{saved={id,input};return ok({...config,...input});}});const ui=render(<MailComposePanel accounts={[account]}/>);await ready(ui);fill(ui);fireEvent.click(ui.getByText(/SMTP 发送设置/));fireEvent.change(ui.getByLabelText('SMTP 服务器'),{target:{value:'smtp.qq.com'}});assert.equal((ui.getByRole('button',{name:'预览并准备发送'}) as HTMLButtonElement).disabled,true);fireEvent.click(ui.getByRole('button',{name:'保存 SMTP 设置'}));await waitFor(()=>assert.deepEqual(saved,{id:'mail-a',input:{host:'smtp.qq.com',port:465,security:'ssl'}}));await waitFor(()=>assert.equal((ui.getByRole('button',{name:'预览并准备发送'}) as HTMLButtonElement).disabled,false));});


test('保存同一邮箱授权码后刷新 SMTP 可用状态，普通账号刷新保留未保存服务器输入',async()=>{
 let reads=0;setup({getConfig:async()=>{reads++;return ok({...config,hasSecret:reads>1});}});
 const withoutSecret={...account,state:{...account.state,imap:{...account.state.imap!,hasSecret:false}}};
 const ui=render(<MailComposePanel accounts={[withoutSecret]}/>);await ready(ui);await waitFor(()=>assert.ok(ui.getByText(/尚未保存邮箱授权码/)));
 ui.rerender(<MailComposePanel accounts={[account]}/>);await waitFor(()=>assert.equal(reads,2));await waitFor(()=>assert.equal(ui.queryByText(/尚未保存邮箱授权码/),null));
 fireEvent.click(ui.getByText(/SMTP 发送设置/));fireEvent.change(ui.getByLabelText('SMTP 服务器'),{target:{value:'smtp.unsaved.example'}});
 ui.rerender(<MailComposePanel accounts={[structuredClone(account)]}/>);assert.equal(reads,2);assert.equal((ui.getByLabelText('SMTP 服务器') as HTMLInputElement).value,'smtp.unsaved.example');
});
test('删除当前发件邮箱会清除过期选择，保留写好的主题正文',async()=>{
 setup();const ui=render(<MailComposePanel accounts={[account]}/>);await ready(ui);fill(ui);ui.rerender(<MailComposePanel accounts={[]}/>);await waitFor(()=>assert.equal((ui.getByLabelText('选择发件账号') as HTMLSelectElement).value,''));assert.equal((ui.getByLabelText('邮件正文') as HTMLTextAreaElement).value,receipt.body);assert.equal((ui.getByRole('button',{name:'预览并准备发送'}) as HTMLButtonElement).disabled,true);
});
test('写信草稿留在窗口内时仍可配置邮箱，返回写邮件后保留输入',async()=>{
 setup();Reflect.deleteProperty(window,'mailOperations');Reflect.deleteProperty(window,'widget');let connected=0;
 window.mailWorkspace={getAccounts:async()=>ok([account]),listPresets:async()=>ok([]),listFoldersLocal:async()=>ok([]),listLocal:async()=>ok({messages:[],total:0}),knownSenders:async()=>ok([]),disconnect:async()=>{connected++;return ok(account);}} as unknown as import('../src/shared/mail-workspace').MailWorkspaceAPI;
 const ui=render(<MailWorkspacePanel onClose={()=>{}}/>);await waitFor(()=>assert.equal((ui.getByRole('button',{name:'写邮件'}) as HTMLButtonElement).disabled,false));
 fireEvent.click(ui.getByRole('button',{name:'写邮件'}));await ready(ui);fill(ui);fireEvent.click(ui.getByRole('button',{name:'返回邮件列表'}));
 fireEvent.click(ui.getByRole('button',{name:'设置 student@example.com'}));await waitFor(()=>assert.equal((ui.getByRole('button',{name:'断开此邮箱'}) as HTMLButtonElement).disabled,false));
 fireEvent.click(ui.getByRole('button',{name:'断开此邮箱'}));await waitFor(()=>assert.equal(connected,1));
 // Header close while a settings view is open must leave settings without discarding the hidden draft.
 await waitFor(()=>assert.equal((ui.getByRole('button',{name:'返回邮件工作台'}) as HTMLButtonElement).disabled,false));
 fireEvent.click(ui.getByRole('button',{name:'返回邮件工作台'}));
 await waitFor(()=>assert.ok(ui.getByRole('button',{name:'写邮件'})));fireEvent.click(ui.getByRole('button',{name:'写邮件'}));
 assert.equal((ui.getByLabelText('邮件正文') as HTMLTextAreaElement).value,receipt.body);
});

test('同一封来信发送完成后可以再次进入新的回复草稿',async()=>{
 setup();Reflect.deleteProperty(window,'mailOperations');Reflect.deleteProperty(window,'widget');
 const source:LocalMail={localId:'local-a',bindingId:'mail-a',mailboxIdentity:'a',accountEmail:'student@example.com',cachedAt:'2026-11-20T12:00:00Z',analyzedAt:null,analysisId:null,status:'unanalyzed',id:'remote-a',subject:'项目要求',sender:'teacher@example.com',receivedAt:'2026-11-20T12:00:00Z',preview:'请在下周提交',body:'请在下周提交',unread:false,webUrl:'',imported:false};
 window.mailWorkspace={getAccounts:async()=>ok([account]),listPresets:async()=>ok([]),listFoldersLocal:async()=>ok([]),listLocal:async()=>ok({messages:[source],total:1}),getLocal:async()=>ok(source)} as unknown as import('../src/shared/mail-workspace').MailWorkspaceAPI;
 const ui=render(<MailWorkspacePanel onClose={()=>{}}/>);await waitFor(()=>assert.equal((ui.getByRole('button',{name:'写邮件'}) as HTMLButtonElement).disabled,false));
 fireEvent.click(ui.getByText('项目要求').closest('button')!);await waitFor(()=>assert.equal((ui.getByRole('button',{name:'根据来信写回复'}) as HTMLButtonElement).disabled,false));
 fireEvent.click(ui.getByRole('button',{name:'根据来信写回复'}));await ready(ui);fill(ui);fireEvent.click(ui.getByRole('button',{name:'预览并准备发送'}));await waitFor(()=>assert.ok(ui.getByRole('button',{name:'确认发送这封邮件'})));fireEvent.click(ui.getByRole('button',{name:'确认发送这封邮件'}));
 await waitFor(()=>assert.equal((ui.getByRole('button',{name:'返回邮件列表'}) as HTMLButtonElement).disabled,false));fireEvent.click(ui.getByRole('button',{name:'返回邮件列表'}));
 await waitFor(()=>assert.equal((ui.getByRole('button',{name:'根据来信写回复'}) as HTMLButtonElement).disabled,false));fireEvent.click(ui.getByRole('button',{name:'根据来信写回复'}));
 await waitFor(()=>assert.equal((ui.getByLabelText('邮件正文') as HTMLTextAreaElement).disabled,false));assert.equal((ui.getByLabelText('邮件正文') as HTMLTextAreaElement).value,'');assert.equal((ui.getByLabelText('邮件主题') as HTMLInputElement).value,'Re: 项目要求');assert.equal(ui.queryByLabelText('发送前预览'),null);
});

test('Agent 草稿打开后仍是可编辑输入，附件随人工准备发送，打开不会生成或发信',async()=>{
 let generated=0,sends=0,prepared:any;
 setup({generate:async()=>{generated++;return ok({subject:'意外生成',body:'不应执行'});},prepare:async input=>{prepared=input;return ok({...receipt,...input});},send:async()=>{sends++;return ok({...receipt,status:'sent'});}});
 const draft={id:'agent-draft-a',bindingId:account.id,to:'chosen@example.com',subject:'项目进度',body:'我们已经完成了主要测试。',attachmentIds:['file-a']};
 const ui=render(<MailComposePanel accounts={[account]} initialDraft={draft}/>);await ready(ui);
 assert.equal((ui.getByLabelText('收件人（一个邮箱地址）') as HTMLInputElement).value,draft.to);
 assert.equal((ui.getByLabelText('邮件主题') as HTMLInputElement).value,draft.subject);
 assert.equal((ui.getByLabelText('邮件正文') as HTMLTextAreaElement).value,draft.body);
 assert.equal(generated,0);assert.equal(sends,0);assert.equal(prepared,undefined);
 fireEvent.change(ui.getByLabelText('邮件正文'),{target:{value:'人工修改后的正文。'}});
 fireEvent.click(ui.getByRole('button',{name:'预览并准备发送'}));
 await waitFor(()=>assert.ok(ui.getByRole('button',{name:'确认发送这封邮件'})));
 assert.deepEqual(prepared,{bindingId:account.id,to:draft.to,subject:draft.subject,body:'人工修改后的正文。',attachmentIds:['file-a'],originDraftId:draft.id,expectedRevision:1});
 assert.equal(sends,0);
});

test('发送成功后的草稿状态更新失败不会把 sent 改成 uncertain，也不会允许重复发送',async()=>{
 let callbacks=0,sends=0;setup({send:async()=>{sends++;return ok({...receipt,status:'sent',detail:'SMTP accepted'});}});
 const ui=render(<MailComposePanel accounts={[account]} onSent={async value=>{callbacks++;assert.equal(value.status,'sent');throw Error('本地待确认更新失败');}}/>);await ready(ui);fill(ui);
 fireEvent.click(ui.getByRole('button',{name:'预览并准备发送'}));await waitFor(()=>assert.ok(ui.getByRole('button',{name:'确认发送这封邮件'})));
 fireEvent.click(ui.getByRole('button',{name:'确认发送这封邮件'}));await waitFor(()=>assert.match(ui.getByRole('alert').textContent??'',/邮件服务器已接受邮件，但待确认列表更新失败/));
 assert.equal(callbacks,1);assert.equal(sends,1);assert.ok(ui.getByText('SMTP accepted'));assert.equal(ui.queryByText('发送结果不确定'),null);
 assert.equal(ui.queryByRole('button',{name:'确认发送这封邮件'}),null);
 assert.equal((ui.getByRole('button',{name:'预览并准备发送'}) as HTMLButtonElement).disabled,true);
});

test('发送结果不确定时不将 Agent 邮件草稿标为已完成',async()=>{
 let callbacks=0;setup({send:async()=>ok({...receipt,status:'uncertain',detail:'需核实'})});
 const ui=render(<MailComposePanel accounts={[account]} onSent={()=>{callbacks++;}}/>);await ready(ui);fill(ui);
 fireEvent.click(ui.getByRole('button',{name:'预览并准备发送'}));await waitFor(()=>assert.ok(ui.getByRole('button',{name:'确认发送这封邮件'})));
 fireEvent.click(ui.getByRole('button',{name:'确认发送这封邮件'}));await waitFor(()=>assert.ok(ui.getByText('发送结果不确定')));
 assert.equal(callbacks,0);
});

test('Reply-To优先填入回复地址，发送前展示后端固定线程；打开预览不会自动发送',async()=>{
 let sent=0,prepared:any;const source:LocalMail={localId:'reply-mail',bindingId:account.id,mailboxIdentity:'a',accountEmail:config.email,cachedAt:receipt.createdAt,analyzedAt:null,analysisId:null,status:'unanalyzed',id:'remote',subject:'考试安排更新',sender:'Iris <iris@example.com>',fromAddress:'iris@example.com',replyToAddresses:['office@example.com'],messageId:'<exam-update@example.com>',receivedAt:receipt.createdAt,preview:'',body:'Please reply to the course office.',unread:false,webUrl:'',imported:false};
 const thread={to:'office@example.com',replyToAddresses:['office@example.com'],sourceMessageId:source.messageId!,inReplyTo:source.messageId!,references:['<exam-root@example.com>',source.messageId!],threadAvailable:true,replyToLocalId:source.localId,bindingId:account.id,accountEmail:config.email};
 setup({prepare:async input=>{prepared=input;return ok({...receipt,...input,replyContext:thread});},send:async()=>{sent++;return ok({...receipt,status:'sent'});}});const ui=render(<MailComposePanel accounts={[account]} replySource={source}/>);await ready(ui);assert.equal((ui.getByLabelText('收件人（一个邮箱地址）') as HTMLInputElement).value,'office@example.com');fireEvent.change(ui.getByLabelText('邮件正文'),{target:{value:'已收到，我会按更新后的时间参加。'}});fireEvent.click(ui.getByRole('button',{name:'预览并准备发送'}));await waitFor(()=>assert.ok(ui.getByLabelText('固定回复线程')));assert.equal(prepared.to,'office@example.com');assert.equal(prepared.replyToLocalId,source.localId);assert.equal(sent,0);assert.ok(within(ui.getByLabelText('固定回复线程')).getByText(thread.inReplyTo));
});
test('多个Reply-To不擅自选第一个，用户选择后才可准备回复',async()=>{
 setup();const source:LocalMail={localId:'reply-multi',bindingId:account.id,mailboxIdentity:'a',accountEmail:config.email,cachedAt:receipt.createdAt,analyzedAt:null,analysisId:null,status:'unanalyzed',id:'remote',subject:'Discussion',sender:'Teacher',fromAddress:'teacher@example.com',replyToAddresses:['office@example.com','ta@example.com'],receivedAt:receipt.createdAt,preview:'',body:'Please reply.',unread:false,webUrl:'',imported:false};
 const ui=render(<MailComposePanel accounts={[account]} replySource={source}/>);await ready(ui);assert.equal((ui.getByLabelText('收件人（一个邮箱地址）') as HTMLInputElement).value,'');fireEvent.change(ui.getByLabelText('选择回复地址'),{target:{value:'ta@example.com'}});assert.equal((ui.getByLabelText('收件人（一个邮箱地址）') as HTMLInputElement).value,'ta@example.com');
});

test('旧草稿编辑器携带版本，后端拒绝时不提供发送按钮',async()=>{let input:any,sends=0;setup({prepare:async value=>{input=value;return {ok:false,error:'此草稿已被替代，请打开当前版本'};},send:async()=>{sends++;return ok({...receipt,status:'sent'});}});const draft={id:'old-draft',revision:3,bindingId:account.id,to:receipt.to,subject:receipt.subject,body:receipt.body};const ui=render(<MailComposePanel accounts={[account]} initialDraft={draft}/>);await ready(ui);fireEvent.click(ui.getByRole('button',{name:'预览并准备发送'}));await waitFor(()=>assert.ok(ui.getByRole('alert').textContent?.includes('已被替代')));assert.equal(input.originDraftId,'old-draft');assert.equal(input.expectedRevision,3);assert.equal(ui.queryByRole('button',{name:'确认发送这封邮件'}),null);assert.equal(sends,0);});
