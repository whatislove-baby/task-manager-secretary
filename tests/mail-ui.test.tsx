import React from 'react';
import { test, afterEach } from 'node:test';
import assert from 'node:assert/strict';
import { JSDOM } from 'jsdom';
import { createRequire } from 'node:module';
import { MailPanel } from '../src/renderer/MailPanel';
import { Tools } from '../src/renderer/Tools';
import { defaultMailFilter, type MailAPI, type MailState, type MailExtraction } from '../src/shared/mail';
import type { DesktopAPI } from '../src/shared/contracts';
const dom=new JSDOM('<!doctype html><html><body></body></html>',{url:'http://localhost'});
for(const [key,value] of Object.entries({window:dom.window,document:dom.window.document,navigator:dom.window.navigator,HTMLElement:dom.window.HTMLElement}))Object.defineProperty(globalThis,key,{value,configurable:true});
const {render,fireEvent,waitFor,cleanup,within}=createRequire(process.cwd()+'/package.json')('@testing-library/react') as typeof import('@testing-library/react');
afterEach(()=>{cleanup();delete window.widget;});
const ok=<T,>(data:T)=>({ok:true as const,data});
const state:MailState={clientId:'11111111-1111-4111-8111-111111111111',account:{id:'school',email:'student@school.edu',name:'Student'},filter:structuredClone(defaultMailFilter)};
const settings={baseUrl:'https://ai.example/v1',model:'model-for-mail',hasApiKey:true,dataDirectory:'test'};
const extraction:MailExtraction={drafts:[{tempId:'1',parentTempId:null,title:'提交报告',notes:'来源邮件',due:{mode:'none'},source:'原文要求'}],notes:'核对日期',confirmationToken:'mail:stable-marker',sourceName:'学校课程通知'};
function setup(overrides:Partial<MailAPI>={}){window.mail={getState:async()=>ok(structuredClone(state)),listFolders:async()=>ok([{id:'course',name:'课程',childCount:1,unreadCount:0,parentId:null}]),saveFilter:async filter=>ok({...state,filter}),...overrides} as MailAPI;window.desktop={getSettings:async()=>ok(settings)} as Partial<DesktopAPI> as DesktopAPI;}
const noop=()=>{};
async function ready(ui:ReturnType<typeof render>){await waitFor(()=>assert.ok(ui.queryByRole('button',{name:'读取邮件'})&&!(ui.getByRole('button',{name:'读取邮件'}) as HTMLButtonElement).disabled));}

test('首次配置公开应用ID后由用户主动登录，StrictMode不自动connect',async()=>{
 let connected=0,configured='';let current={...state,clientId:'',account:null} as MailState;
 setup({getState:async()=>ok(current),configure:async id=>{configured=id;current={...current,clientId:id};return ok(current);},connect:async()=>{connected++;return ok(state);}});
 const ui=render(<React.StrictMode><MailPanel onClose={noop} onDrafts={noop}/></React.StrictMode>);
 await waitFor(()=>assert.ok(ui.queryByRole('button',{name:'登录 Microsoft 365'})));
 assert.equal(connected,0);assert.equal((ui.getByRole('button',{name:'登录 Microsoft 365'}) as HTMLButtonElement).disabled,true);
 fireEvent.change(ui.getByLabelText('应用程序（客户端）ID'),{target:{value:state.clientId}});fireEvent.click(ui.getByRole('button',{name:'保存连接设置'}));
 await waitFor(()=>assert.ok(!(ui.getByRole('button',{name:'登录 Microsoft 365'}) as HTMLButtonElement).disabled));assert.equal(configured,state.clientId);
 fireEvent.click(ui.getByRole('button',{name:'登录 Microsoft 365'}));await ready(ui);assert.equal(connected,1);assert.ok(ui.queryByText('student@school.edu'));
});

test('目录按需展开多选，组合筛选可保存为账户默认；读取不自动保存',async()=>{
 let readFilter:unknown,savedFilter:unknown;const parents:(string|undefined)[]=[];
 setup({listFolders:async parent=>{parents.push(parent);return ok(parent?[{id:'sub',name:'作业',childCount:0,unreadCount:0,parentId:'course'}]:[{id:'course',name:'课程',childCount:1,unreadCount:0,parentId:null}]);},read:async filter=>{readFilter=filter;return ok({messages:[],limited:false});},saveFilter:async filter=>{savedFilter=filter;return ok({...state,filter});}});
 const ui=render(<MailPanel onClose={noop} onDrafts={noop}/>);await ready(ui);
 fireEvent.click(ui.getByRole('button',{name:'展开 课程'}));await waitFor(()=>assert.ok(ui.queryByLabelText('作业')));fireEvent.click(ui.getByLabelText('作业'));
 fireEvent.click(ui.getByLabelText('包含子文件夹'));fireEvent.change(ui.getByLabelText('收件日期'),{target:{value:'since'}});fireEvent.change(ui.getByLabelText('开始日期'),{target:{value:'2026-10-20'}});fireEvent.change(ui.getByLabelText('发件人包含'),{target:{value:'teacher'}});
 fireEvent.click(ui.getByRole('button',{name:'读取邮件'}));await ready(ui);assert.ok(readFilter);assert.equal(savedFilter,undefined);
 fireEvent.click(ui.getByRole('button',{name:'保存为默认筛选'}));await ready(ui);assert.deepEqual(savedFilter,{...defaultMailFilter,folderIds:['sub'],includeChildren:true,dateMode:'since',since:'2026-10-20',sender:'teacher'});assert.deepEqual(parents,[undefined,'course']);
});

test('查看纯文本邮件不触发AI，用户主动生成单封草稿并转交审核',async()=>{
 let extracted=0,passed:MailExtraction|undefined;const summary={id:'m1',subject:'课程报告',sender:'teacher@school.edu',receivedAt:'2026-10-30T09:00:00Z',preview:'请提交报告',unread:true,webUrl:'https://outlook.office.com/mail/id/m1',imported:false};
 setup({read:async()=>ok({messages:[summary],limited:true}),getMessage:async()=>ok({...summary,body:'说明 <script>bad()</script>'}),extract:async id=>{assert.equal(id,'m1');extracted++;return ok(extraction);}});
 const ui=render(<MailPanel onClose={noop} onDrafts={next=>{passed=next;}}/>);await ready(ui);fireEvent.click(ui.getByRole('button',{name:'读取邮件'}));await ready(ui);
 fireEvent.click(ui.getByRole('button',{name:/课程报告/}));await waitFor(()=>assert.ok(ui.queryByLabelText('邮件正文')));assert.equal(extracted,0);assert.equal(ui.getByLabelText('邮件正文').querySelector('script'),null);assert.ok(ui.getByLabelText('邮件正文').textContent?.includes('<script>'));
 assert.ok(ui.queryByText(/model-for-mail/));assert.ok(ui.queryByText(/点击 AI 整理会把这封邮件的主题、发件人、收件时间和正文发送/));
 fireEvent.click(ui.getByRole('button',{name:'AI 整理这封邮件'}));await waitFor(()=>assert.equal(extracted,1));await waitFor(()=>assert.ok(passed===extraction));
});

test('未保存筛选关闭保护、键盘焦点及局部读取错误',async()=>{
 let closed=0;setup({read:async()=>({ok:false,error:'学校权限不足'})});
 const ui=render(<MailPanel onClose={()=>{closed++;}} onDrafts={noop}/>);await ready(ui);
 fireEvent.click(ui.getByRole('button',{name:'读取邮件'}));await waitFor(()=>assert.ok(ui.queryByRole('alert')));assert.equal(ui.container.querySelector('.notification'),null);
 fireEvent.change(ui.getByLabelText('主题包含'),{target:{value:'报告'}});fireEvent.keyDown(document,{key:'Escape'});const dialog=ui.getByRole('alertdialog');assert.equal(closed,0);assert.ok(dialog.contains(document.activeElement));
 fireEvent.click(within(dialog).getByRole('button',{name:'继续设置'}));assert.ok(ui.queryByRole('alertdialog')===null);fireEvent.click(ui.getByRole('button',{name:'关闭邮箱'}));fireEvent.click(ui.getByRole('button',{name:'放弃并关闭'}));assert.equal(closed,1);
});

test('邮件草稿复用审核界面与稳定确认令牌，不再次调用模型',async()=>{
 let savedToken='',models=0;window.desktop={getSettings:async()=>ok(settings),extract:async()=>{models++;throw Error('不应重新请求');},saveDrafts:async(drafts,token)=>{assert.equal(drafts[0].title,'提交报告');savedToken=token;return ok({value:1,warnings:[]});}} as Partial<DesktopAPI> as DesktopAPI;
 const ui=render(<Tools section="ai" initialExtraction={extraction} todos={[]} onClose={noop} onChanged={async()=>{}}/>);
 await waitFor(()=>assert.ok(ui.queryByRole('button',{name:'确认保存 1 项待办'})));
 assert.ok(ui.queryByLabelText('邮件或其他原文')===null);fireEvent.click(ui.getByRole('button',{name:'确认保存 1 项待办'}));await waitFor(()=>assert.equal(savedToken,'mail:stable-marker'));assert.equal(models,0);
});


test('跨窗断开清空邮件与目录，旧读取迟响应不能重新展示',async()=>{
 let current=structuredClone(state),changed=()=>{},resolve:(value:any)=>void=()=>{};
 window.widget={onChanged:(callback:()=>void)=>{changed=callback;return()=>{};}} as any;
 setup({getState:async()=>ok(current),read:async()=>await new Promise(done=>{resolve=done;})});
 const ui=render(<MailPanel onClose={noop} onDrafts={noop}/>);await ready(ui);
 fireEvent.click(ui.getByRole('button',{name:'读取邮件'}));current={...state,account:null};changed();
 await waitFor(()=>assert.ok(ui.queryByText('尚未连接邮箱')));
 resolve(ok({messages:[{id:'old',subject:'旧账户秘密',sender:'old',receivedAt:'2026-10-30T00:00:00Z',preview:'旧内容',unread:false,webUrl:'',imported:false}],limited:false}));
 await waitFor(()=>assert.ok(!(ui.getByRole('button',{name:'登录 Microsoft 365'}) as HTMLButtonElement).disabled));assert.ok(ui.queryByText('旧账户秘密')===null);assert.ok(ui.queryByRole('button',{name:'读取邮件'})===null);
});


test('StrictMode打开已连接邮箱仍显示根目录且不重复拉取',async()=>{
 let folders=0,connections=0;setup({listFolders:async()=>{folders++;return ok([{id:'course',name:'课程',childCount:0,unreadCount:0,parentId:null}]);},connect:async()=>{connections++;return ok(state);}});
 const ui=render(<React.StrictMode><MailPanel onClose={noop} onDrafts={noop}/></React.StrictMode>);await ready(ui);assert.ok(ui.queryByLabelText('课程'));assert.equal(folders,1);assert.equal(connections,0);
});


test('AI目标在其他窗口改变时先更新显示，首次点击不发送，再次确认才整理',async()=>{
 let extracted=0;let active=settings;
 const summary={id:'m1',subject:'报告',sender:'teacher',receivedAt:'2026-10-30T09:00:00Z',preview:'正文',unread:false,webUrl:'',imported:false};
 setup({read:async()=>ok({messages:[summary],limited:false}),getMessage:async()=>ok({...summary,body:'正文'}),extract:async()=>{extracted++;return ok(extraction);}});
 window.desktop={getSettings:async()=>ok(active)} as Partial<DesktopAPI> as DesktopAPI;
 const ui=render(<MailPanel onClose={noop} onDrafts={noop}/>);await ready(ui);fireEvent.click(ui.getByRole('button',{name:'读取邮件'}));await ready(ui);fireEvent.click(ui.getByRole('button',{name:/报告/}));await waitFor(()=>assert.ok(ui.queryByLabelText('邮件正文')));
 active={...settings,baseUrl:'https://other-ai.example/v1',model:'new-model'};
 fireEvent.click(ui.getByRole('button',{name:'AI 整理这封邮件'}));await waitFor(()=>assert.ok(ui.queryByText(/请核对下方目标后再次点击/)));assert.equal(extracted,0);assert.ok(ui.queryByText(/new-model/));
 fireEvent.click(ui.getByRole('button',{name:'AI 整理这封邮件'}));await waitFor(()=>assert.equal(extracted,1));
});


const imapState:MailState={method:'imap',clientId:'',account:null,filter:structuredClone(defaultMailFilter)};
const savedImap={email:'person@example.test',username:'person@example.test',host:'mail.example.test',port:993,security:'tls' as const,hasSecret:true};
async function imapReady(ui:ReturnType<typeof render>){await waitFor(()=>assert.ok(ui.queryByLabelText('绑定方式')&&ui.queryByRole('button',{name:'保存并连接 IMAP'})&&!(ui.getByRole('button',{name:'保存并连接 IMAP'}) as HTMLButtonElement).disabled));}

test('按绑定方式配置任意IMAP服务器，授权码保存后不回填，连接失败可用已保存配置重试',async()=>{
 let current=structuredClone(imapState),received:any,connections=0;
 setup({getState:async()=>ok(current),configureImap:async input=>{received=input;current={...imapState,imap:{...savedImap,email:input.email,username:input.username||input.email,host:input.host,port:input.port,security:input.security}};return ok(current);},connect:async()=>{connections++;if(connections===1)return {ok:false,error:'连接失败，请检查授权码'};current={...current,account:{id:'imap:one',email:savedImap.email,name:savedImap.email}};return ok(current);}});
 const ui=render(<MailPanel onClose={noop} onDrafts={noop}/>);await imapReady(ui);
 assert.equal((ui.getByLabelText('绑定方式') as HTMLSelectElement).value,'imap');assert.ok(ui.queryByLabelText('应用程序（客户端）ID')===null);
 fireEvent.change(ui.getByLabelText('邮箱地址'),{target:{value:'person@example.test'}});fireEvent.change(ui.getByLabelText('IMAP 服务器'),{target:{value:'mail.example.test'}});
 fireEvent.change(ui.getByLabelText('连接安全'),{target:{value:'starttls'}});assert.equal((ui.getByLabelText('端口') as HTMLInputElement).value,'143');fireEvent.change(ui.getByLabelText('端口'),{target:{value:'1993'}});
 fireEvent.change(ui.getByLabelText('授权码或密码'),{target:{value:'private-app-password'}});fireEvent.click(ui.getByRole('button',{name:'保存并连接 IMAP'}));
 await waitFor(()=>assert.ok(ui.queryByRole('alert')));assert.equal(received.password,'private-app-password');assert.equal(received.host,'mail.example.test');assert.equal(received.port,1993);assert.equal(received.security,'starttls');
 assert.equal((ui.getByLabelText('授权码或密码') as HTMLInputElement).value,'');assert.ok(ui.queryByText('private-app-password')===null);assert.ok(ui.queryByRole('button',{name:'读取邮件'})===null);
 fireEvent.click(ui.getByRole('button',{name:'测试并连接 IMAP'}));await ready(ui);assert.equal(connections,2);assert.ok(ui.queryByText('person@example.test'));
});

test('未保存IMAP口令和配置在切换方式前确认，可取消；确认后口令清空且不自动登录',async()=>{
 let switches=0,logins=0;
 setup({getState:async()=>ok(imapState),selectMethod:async method=>{switches++;assert.equal(method,'microsoft');return ok({...state,method:'microsoft',account:null});},connect:async()=>{logins++;return ok(state);}});
 const ui=render(<MailPanel onClose={noop} onDrafts={noop}/>);await imapReady(ui);
 fireEvent.change(ui.getByLabelText('授权码或密码'),{target:{value:'unsaved-secret'}});fireEvent.change(ui.getByLabelText('绑定方式'),{target:{value:'microsoft'}});
 await waitFor(()=>assert.ok(ui.queryByRole('alertdialog')));assert.equal(switches,0);fireEvent.click(ui.getByRole('button',{name:'继续设置'}));assert.equal((ui.getByLabelText('授权码或密码') as HTMLInputElement).value,'unsaved-secret');
 fireEvent.change(ui.getByLabelText('绑定方式'),{target:{value:'microsoft'}});fireEvent.click(ui.getByRole('button',{name:'放弃并切换'}));
 await waitFor(()=>assert.ok(ui.queryByRole('button',{name:'登录 Microsoft 365'})));assert.equal(switches,1);assert.equal(logins,0);assert.ok(ui.queryByLabelText('授权码或密码')===null);
});

test('更换IMAP连接身份清除输入中的口令，已保存密钥不会在DOM中回填',async()=>{
 setup({getState:async()=>ok({...imapState,imap:savedImap})});const ui=render(<MailPanel onClose={noop} onDrafts={noop}/>);await imapReady(ui);
 assert.equal((ui.getByLabelText('授权码或密码') as HTMLInputElement).value,'');fireEvent.change(ui.getByLabelText('授权码或密码'),{target:{value:'new-secret'}});fireEvent.change(ui.getByLabelText('IMAP 服务器'),{target:{value:'other.example.test'}});
 assert.equal((ui.getByLabelText('授权码或密码') as HTMLInputElement).value,'');assert.equal((ui.getByRole('button',{name:'测试并连接 IMAP'}) as HTMLButtonElement).disabled,true);
});

test('跨窗切换绑定方式清理旧邮件与口令，旧读取迟响应不能重新展示',async()=>{
 let current:MailState={...imapState,imap:savedImap,account:{id:'imap:one',email:savedImap.email,name:'One'}},changed=()=>{},release:(value:any)=>void=()=>{};
 window.widget={onChanged:(callback:()=>void)=>{changed=callback;return()=>{};}} as any;
 setup({getState:async()=>ok(current),read:async()=>await new Promise(resolve=>{release=resolve;})});
 const ui=render(<MailPanel onClose={noop} onDrafts={noop}/>);await ready(ui);fireEvent.click(ui.getByRole('button',{name:'读取邮件'}));current={...state,method:'microsoft',account:null};changed();
 await waitFor(()=>assert.ok(ui.queryByRole('button',{name:'登录 Microsoft 365'})));
 release(ok({messages:[{id:'old',subject:'旧连接邮件',sender:'old',receivedAt:'2026-10-30T00:00:00Z',preview:'old',unread:false,webUrl:'',imported:false}],limited:false}));
 await waitFor(()=>assert.ok(!(ui.getByRole('button',{name:'登录 Microsoft 365'}) as HTMLButtonElement).disabled));assert.ok(ui.queryByText('旧连接邮件')===null);assert.ok(ui.queryByLabelText('授权码或密码')===null);
});
