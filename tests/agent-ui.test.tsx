import React from 'react';
import {test,afterEach} from 'node:test';
import assert from 'node:assert/strict';
import {JSDOM} from 'jsdom';
import {createRequire} from 'node:module';
import {AgentApp,RunActivity} from '../src/renderer/AgentApp';
import {AgentMessageBody} from '../src/renderer/AgentMessageBody';
import {SecretaryBriefing} from '../src/renderer/SecretaryBriefing';
import type {AgentAPI,AgentConversation,AgentRun,AgentTurnInput} from '../src/shared/agent';
import type {DesktopAPI} from '../src/shared/contracts';
import type {MailWorkspaceAPI} from '../src/shared/mail-workspace';
const dom=new JSDOM('<!doctype html><html><body></body></html>',{url:'http://localhost'});
for(const [key,value] of Object.entries({window:dom.window,document:dom.window.document,navigator:dom.window.navigator,HTMLElement:dom.window.HTMLElement}))Object.defineProperty(globalThis,key,{value,configurable:true});
const {render,fireEvent,waitFor,cleanup}=createRequire(process.cwd()+'/package.json')('@testing-library/react') as typeof import('@testing-library/react');
afterEach(()=>{cleanup();Reflect.deleteProperty(window,'agent');Reflect.deleteProperty(window,'widget');Reflect.deleteProperty(window,'mailFiles');});
const ok=<T,>(data:T)=>({ok:true as const,data});
const date='2026-11-21T01:00:00Z';
function makeRun(patch:Partial<AgentRun>={}):AgentRun{return {id:'run-a',conversationId:'conv-a',status:'queued',createdAt:date,updatedAt:date,partialText:'',events:[],sources:[],artifacts:[],...patch};}
function makeConversation(run?:AgentRun):AgentConversation{return {id:'conv-a',title:'Project 2 的要求',createdAt:date,updatedAt:date,turnCount:1,turns:[{id:'user-a',role:'user',content:'帮我分析 Project 2',createdAt:date}],...(run?{activeRun:run}:{})};}
function makeSecretary(patch:Partial<AgentConversation>={}):AgentConversation{return {id:'secretary',kind:'secretary',title:'Secretary',createdAt:date,updatedAt:date,turnCount:0,turns:[],children:[],...patch};}
function setup(overrides:Partial<AgentAPI>={}){
 window.desktop={getSettings:async()=>ok({baseUrl:'https://model.example/v1',model:'deepseek/test',hasApiKey:true,dataDirectory:'data'}),listTodos:async()=>ok([])} as unknown as DesktopAPI;
 window.mailWorkspace={getAccounts:async()=>ok([]),listLocal:async()=>ok({messages:[],total:0})} as unknown as MailWorkspaceAPI;
 window.agent={getSecretary:async()=>ok(makeSecretary()),createChild:async()=>{throw Error('unused child');},getBriefing:async()=>{throw Error('unused briefing');},listConversations:async()=>ok([]),listPending:async()=>ok([]),getConversation:async()=>ok(makeConversation()),exportConversation:async()=>{throw Error('unused export');},getRun:async()=>ok(makeRun()),startTurn:async()=>ok(makeRun()),resumeRun:async()=>ok(makeRun()),pauseRun:async()=>ok(makeRun({status:'paused'})),cancelRun:async()=>ok(makeRun({status:'canceled'})),deleteConversation:async()=>ok(undefined),getMailDraft:async()=>{throw Error('unused');},resolveMailDraft:async()=>ok(undefined),...overrides};
}

test('导出复制时重新读取所选对话，附加名称需单独选择，切换对话关闭面板',async t=>{
 const clipboard:string[]=[];
 Object.defineProperty(navigator,'clipboard',{configurable:true,value:{writeText:async(text:string)=>{clipboard.push(text);}}});
 t.after(()=>Reflect.deleteProperty(navigator,'clipboard'));
 const chat=makeConversation();
 const reads:string[]=[];
 const source={kind:'file' as const,id:'private-source',title:'课程参考.pdf'};
 const fresh={...chat,turns:[{...chat.turns[0],content:'最新对话内容',sources:[source]}]};
 setup({listConversations:async()=>ok([chat]),getConversation:async id=>{reads.push(id);return ok(reads.length===1?chat:fresh);}});
 const ui=render(<AgentApp/>);
 fireEvent.click(await ui.findByRole('button',{name:chat.title}));
 await ui.findByText(chat.turns[0].content);
 fireEvent.click(ui.getByRole('button',{name:'导出对话'}));
 const option=ui.getByRole('checkbox',{name:'附上来源与产物名称'}) as HTMLInputElement;
 assert.equal(option.checked,false);
 fireEvent.click(ui.getByRole('button',{name:'复制完整对话'}));
 await ui.findByText('完整对话已复制。');
 assert.deepEqual(reads,[chat.id,chat.id]);
 assert.match(clipboard[0],/最新对话内容/);
 assert.doesNotMatch(clipboard[0],/课程参考|帮我分析/);
 fireEvent.click(option);
 fireEvent.click(ui.getByRole('button',{name:'复制完整对话'}));
 await waitFor(()=>assert.equal(clipboard.length,2));
 assert.match(clipboard[1],/课程参考\.pdf/);
 fireEvent.click(ui.getByRole('button',{name:'秘书'}));
 await ui.findByRole('heading',{name:'今天有什么需要处理？'});
 assert.equal(ui.queryByRole('checkbox',{name:'附上来源与产物名称'}),null);
 fireEvent.click(ui.getByRole('button',{name:'导出对话'}));
 assert.equal((ui.getByRole('checkbox',{name:'附上来源与产物名称'}) as HTMLInputElement).checked,false);
});

test('Markdown 保存发送当前对话 ID 和选项，并反馈取消、失败及成功',async()=>{
 const chat=makeConversation();
 const requests:unknown[]=[];
 setup({listConversations:async()=>ok([chat]),exportConversation:async input=>{requests.push(input);return requests.length===1?ok(false):requests.length===2?{ok:false,error:'保存对话失败，请重试。'}:ok(true);}});
 const ui=render(<AgentApp/>);
 fireEvent.click(await ui.findByRole('button',{name:chat.title}));
 await ui.findByText(chat.turns[0].content);
 fireEvent.click(ui.getByRole('button',{name:'导出对话'}));
 fireEvent.click(ui.getByRole('checkbox',{name:'附上来源与产物名称'}));
 const save=ui.getByRole('button',{name:'保存 Markdown'});
 fireEvent.click(save);
 await ui.findByText('已取消保存。');
 assert.deepEqual(requests,[{id:chat.id,includeSources:true}]);
 fireEvent.click(save);
 await ui.findByRole('alert');
 assert.equal(ui.getByRole('alert').textContent,'保存对话失败，请重试。');
 fireEvent.click(save);
 await ui.findByText('Markdown 已保存。');
});

test('空对话的复制操作显示无内容反馈',async()=>{
 setup({getConversation:async()=>ok(makeSecretary())});
 const ui=render(<AgentApp/>);
 await ui.findByRole('heading',{name:'今天有什么需要处理？'});
 fireEvent.click(ui.getByRole('button',{name:'导出对话'}));
 fireEvent.click(ui.getByRole('button',{name:'复制完整对话'}));
 await ui.findByText('这段对话没有可导出的内容。');
});

test('聊天是主入口，无需选择空间；发送自然语言并展示真实流进度和最终答复',async()=>{
 let current=makeRun(),conversation=makeConversation(current);const sent:AgentTurnInput[]=[];
 setup({startTurn:async value=>{sent.push(value);return ok(current);},getRun:async()=>ok(current),getConversation:async()=>ok(conversation)});
 const ui=render(<AgentApp/>);await waitFor(()=>assert.ok(ui.getByText('test')));
 assert.ok(ui.getByRole('heading',{name:'今天有什么需要处理？'}));assert.equal(ui.queryByLabelText('选择空间'),null);
 fireEvent.change(ui.getByLabelText('发送给助手'),{target:{value:'帮我分析 Project 2'}});fireEvent.click(ui.getByRole('button',{name:'发送消息'}));
 await waitFor(()=>assert.equal(sent.length,1));assert.deepEqual(sent[0],{conversationId:'secretary',text:'帮我分析 Project 2',reasoning:'auto'});
 current=makeRun({status:'running',partialText:'正在核对课程资料。',events:[{id:'tool-1',type:'tool',tool:'search_mail',label:'正在查询课程通知',status:'running',createdAt:date}]});
 await waitFor(()=>assert.ok(ui.getByText('正在核对课程资料。')));assert.ok(ui.getAllByText('正在查询课程通知').length>=1);assert.ok(ui.getByRole('button',{name:'暂停执行'}));
 current={...current,status:'completed',partialText:'**最终结论**：还缺演示稿。'};conversation={...makeConversation(),turnCount:2,turns:[...makeConversation().turns,{id:'answer',runId:current.id,role:'assistant',content:current.partialText,createdAt:date}]};
 await waitFor(()=>assert.ok(ui.getByText('最终结论')));assert.equal(ui.getAllByText('最终结论').length,1);assert.equal(ui.queryByText('正在核对课程资料。'),null);
});

test('澄清选项带问题ID继续同一run，不另建对话',async()=>{
 const waiting=makeRun({status:'waiting',clarification:{id:'question-2',question:'你指哪个项目？',options:['Project 2','期末项目']}});const replies:unknown[]=[];
 setup({listConversations:async()=>ok([{id:'conv-a',title:'项目讨论',turnCount:1,createdAt:date,updatedAt:date}]),getConversation:async()=>ok(makeConversation(waiting)),resumeRun:async value=>{replies.push(value);return ok(makeRun({status:'running'}));},startTurn:async()=>{throw Error('must resume');}});
 const ui=render(<AgentApp/>);await waitFor(()=>assert.ok(ui.getByRole('button',{name:'项目讨论'})));fireEvent.click(ui.getByRole('button',{name:'项目讨论'}));
 await waitFor(()=>assert.ok(ui.getByText('你指哪个项目？')));fireEvent.click(ui.getByRole('button',{name:'Project 2'}));await waitFor(()=>assert.equal(replies.length,1));assert.deepEqual(replies[0],{id:'run-a',answer:'Project 2',questionId:'question-2'});
});

test('IME回车不会意外发送，上传错误保留输入且不伪造附件',async()=>{
 let calls=0;setup({startTurn:async()=>{calls++;return ok(makeRun());}});window.mailFiles={importLocal:async()=>({ok:false,error:'扫描件尚无可提取文字'})} as any;
 const ui=render(<AgentApp/>);await waitFor(()=>assert.ok(ui.getByText('test')));const input=ui.getByLabelText('发送给助手');fireEvent.change(input,{target:{value:'分析这份资料'}});fireEvent.keyDown(input,{key:'Enter',isComposing:true});assert.equal(calls,0);
 fireEvent.click(ui.getByRole('button',{name:'添加参考资料'}));assert.equal(ui.queryByRole('button',{name:'连接本地目录'}),null);fireEvent.click(ui.getByRole('button',{name:'上传本地文件'}));await waitFor(()=>assert.ok(ui.getByRole('alert').textContent?.includes('扫描件')));assert.equal((input as HTMLTextAreaElement).value,'分析这份资料');assert.equal(calls,0);
});

test('暂停仍在收尾时禁继续，状态确认后才允许恢复',async()=>{
 let current=makeRun({status:'paused',stopping:true});setup({listConversations:async()=>ok([{id:'conv-a',title:'暂停的任务',turnCount:1,createdAt:date,updatedAt:date}]),getConversation:async()=>ok(makeConversation(current)),getRun:async()=>ok(current)});
 const ui=render(<AgentApp/>);await waitFor(()=>assert.ok(ui.getByRole('button',{name:'暂停的任务'})));fireEvent.click(ui.getByRole('button',{name:'暂停的任务'}));await waitFor(()=>assert.equal((ui.getByRole('button',{name:'继续执行'}) as HTMLButtonElement).disabled,true));current={...current,stopping:false};await waitFor(()=>assert.equal((ui.getByRole('button',{name:'继续执行'}) as HTMLButtonElement).disabled,false));
});

test('富文本支持表格和代码，HTML与远程图片不执行，来源只能打开已核对ID',async()=>{
 const opened:string[]=[];
 const ui=render(<AgentMessageBody text={'| 内容 | 日期 |\n| --- | --- |\n| 演示 | 24日 |\n\n```python\nprint(1)\n```\n\n<script>alert(1)</script>\n\n![tracking](https://tracker.example/pixel)\n\n[课程通知](mail://mail-a) [伪造](mail://unknown) [脚本](javascript:alert(1))'} sources={[{kind:'mail',id:'mail-a',title:'课程通知'}]} onSource={s=>opened.push(s.id)}/>);
 assert.ok(ui.getByRole('table'));assert.ok(ui.getByText('print(1)'));assert.equal(ui.container.querySelector('script'),null);assert.equal(ui.container.querySelector('img'),null);assert.equal(ui.container.querySelector('a[href^="javascript"]'),null);assert.equal(ui.queryByRole('button',{name:'伪造'}),null);fireEvent.click(ui.getByRole('button',{name:'课程通知'}));assert.deepEqual(opened,['mail-a']);
});

test('邮箱列表读取失败不妨碍加载模型配置和普通聊天',async()=>{
 let calls=0;setup({startTurn:async()=>{calls++;return ok(makeRun());}});
 window.mailWorkspace.getAccounts=async()=>{throw Error('邮箱暂时不可用');};
 const ui=render(<AgentApp/>);await waitFor(()=>assert.ok(ui.getByText('test')));
 await waitFor(()=>assert.match(ui.getByRole('alert').textContent??'',/邮箱暂时不可用/));
 fireEvent.change(ui.getByLabelText('发送给助手'),{target:{value:'你好'}});fireEvent.click(ui.getByRole('button',{name:'发送消息'}));
 await waitFor(()=>assert.equal(calls,1));
});

test('打开旧对话期间切换新对话，不残留加载状态或覆盖新输入',async()=>{
 let finish!:(value:ReturnType<typeof ok<AgentConversation>>)=>void;
 setup({listConversations:async()=>ok([{id:'conv-a',title:'待打开的旧对话',turnCount:1,createdAt:date,updatedAt:date}]),getConversation:()=>new Promise(resolve=>{finish=resolve;})});
 const ui=render(<AgentApp/>);await waitFor(()=>assert.ok(ui.getByRole('button',{name:'待打开的旧对话'})));
 fireEvent.click(ui.getByRole('button',{name:'待打开的旧对话'}));await waitFor(()=>assert.ok(ui.getByText('正在打开对话…')));
 fireEvent.click(ui.getByRole('button',{name:'新对话'}));
 assert.equal((ui.getByLabelText('发送给助手') as HTMLTextAreaElement).disabled,false);
 fireEvent.change(ui.getByLabelText('发送给助手'),{target:{value:'新的问题'}});
 finish(ok(makeConversation()));
 await waitFor(()=>assert.ok(ui.getByRole('heading',{name:'有什么需要一起处理？'})));
 assert.equal((ui.getByLabelText('发送给助手') as HTMLTextAreaElement).value,'新的问题');
 assert.equal(ui.queryByText('帮我分析 Project 2'),null);
});

test('工具开始和完成按调用合并，失败与暂停后不遗留转圈或虚报整轮完成',()=>{
 const events:AgentRun['events']=[{id:'start',type:'tool',callId:'call-a',tool:'search_mail',label:'搜索课程邮件',status:'running',createdAt:date},{id:'end',type:'tool',callId:'call-a',tool:'search_mail',label:'搜索课程邮件',status:'completed',createdAt:date},{id:'old-start',type:'tool',tool:'read_file',label:'读取资料',status:'running',createdAt:date}];
 const ui=render(<RunActivity run={makeRun({status:'failed',events})} onSource={()=>{}}/>);
 assert.equal(ui.getAllByText('搜索课程邮件').length,1);assert.equal(ui.container.querySelector('.ag-spin'),null);
 assert.ok(ui.getByText('本轮未完成 · 1 项工具操作已完成'));assert.ok(ui.getByLabelText('未记录完成状态'));
 ui.rerender(<RunActivity run={makeRun({status:'paused',events})} onSource={()=>{}}/>);
 assert.equal(ui.container.querySelector('.ag-spin'),null);assert.ok(ui.getByText('已暂停 · 1 项工具操作已完成'));
 ui.rerender(<RunActivity run={makeRun({status:'completed',events:events.slice(0,2)})} onSource={()=>{}}/>);
 assert.equal(ui.container.querySelectorAll('li').length,1);assert.ok(ui.getByText('已完成 1 项工具操作'));
});


test('示例只填入输入框，不发送也不覆盖未写完的内容',async()=>{
 let sent=0;setup({startTurn:async()=>{sent++;return ok(makeRun());}});
 const ui=render(<AgentApp/>);await waitFor(()=>assert.ok(ui.getByText('test')));
 const input=ui.getByLabelText('发送给助手') as HTMLTextAreaElement;
 const starter=ui.getByRole('button',{name:'刷新一下'}) as HTMLButtonElement;
 fireEvent.click(starter);assert.equal(input.value,'刷新一下');assert.equal(sent,0);
 fireEvent.change(input,{target:{value:'请保留这个标题，我还没有写完'}});
 assert.equal(starter.disabled,true);fireEvent.click(starter);
 assert.equal(input.value,'请保留这个标题，我还没有写完');assert.equal(sent,0);
 fireEvent.change(input,{target:{value:''}});assert.equal(starter.disabled,false);
});


test('历史任务来源保留已删除状态，不把取消说成完成或仍在日历',async()=>{
 const source={kind:'task' as const,id:'old-task',title:'已取消的小组讨论'};
 setup({listConversations:async()=>ok([{id:'conv-a',title:'回看课程',turnCount:2,createdAt:date,updatedAt:date}]),getConversation:async()=>ok({...makeConversation(),turns:[...makeConversation().turns,{id:'answer',role:'assistant',content:'讨论后来取消了。',createdAt:date,sources:[source]}]})});
 window.desktop.listTodos=async()=>ok([{id:'old-task',title:source.title,notes:'原讨论通知',completed:false,deletedAt:date} as any]);
 const ui=render(<AgentApp/>);await waitFor(()=>assert.ok(ui.getByRole('button',{name:'回看课程'})));fireEvent.click(ui.getByRole('button',{name:'回看课程'}));
 await waitFor(()=>assert.ok(ui.getByRole('button',{name:/已取消的小组讨论/})));fireEvent.click(ui.getByRole('button',{name:/已取消的小组讨论/}));
 await waitFor(()=>assert.ok(ui.getByText('已移入回收站（未标记完成）')));assert.ok(ui.getByRole('button',{name:'查看回收站'}));assert.equal(ui.queryByRole('button',{name:'打开日历与待办'}),null);
});


test('收件箱列表只带摘要时，先读取完整邮件再打开正文',async()=>{
 setup();const mail={localId:'a'.repeat(64),subject:'课程通知正文回归',sender:'周老师',receivedAt:date,preview:'仅列表摘要',body:'',bodyStatus:'complete'} as any;
 let reads=0;window.mailWorkspace.listLocal=async()=>ok({messages:[mail],total:1});
 window.mailWorkspace.getLocal=async id=>{reads++;assert.equal(id,mail.localId);return ok({...mail,body:'正文最多八页，参考文献不计入。'});};
 const ui=render(<AgentApp/>);await waitFor(()=>assert.ok(ui.getByText('test')));fireEvent.click(ui.getByRole('button',{name:'收件箱'}));
 await waitFor(()=>assert.ok(ui.getByText(mail.subject)));fireEvent.click(ui.getByText(mail.subject).closest('button')!);
 await waitFor(()=>assert.ok(ui.getByRole('dialog',{name:mail.subject})));assert.equal(reads,1);assert.ok(ui.getByText('正文最多八页，参考文献不计入。'));assert.equal(ui.queryByText('邮件正文为空。'),null);
});

test('完整邮件读取失败显示错误，不把缺少正文冒充空邮件',async()=>{
 setup();const mail={localId:'b'.repeat(64),subject:'读取失败的课程通知',sender:'周老师',receivedAt:date,preview:'仅列表摘要',body:''} as any;
 window.mailWorkspace.listLocal=async()=>ok({messages:[mail],total:1});window.mailWorkspace.getLocal=async()=>({ok:false,error:'邮件记录暂时无法读取'});
 const ui=render(<AgentApp/>);await waitFor(()=>assert.ok(ui.getByText('test')));fireEvent.click(ui.getByRole('button',{name:'收件箱'}));
 await waitFor(()=>assert.ok(ui.getByText(mail.subject)));fireEvent.click(ui.getByText(mail.subject).closest('button')!);
 await waitFor(()=>assert.match(ui.getByRole('alert').textContent??'',/邮件记录暂时无法读取/));assert.equal(ui.queryByRole('dialog',{name:mail.subject}),null);assert.equal(ui.queryByText('邮件正文为空。'),null);
});

test('启动恢复固定秘书，最近列表不重复秘书且不能删除，普通新对话仍可使用',async()=>{
 const secretary=makeSecretary({turnCount:1,turns:[{id:'s-old',role:'assistant',content:'之前整理过的收件事项',createdAt:date}]});
 setup({getSecretary:async()=>ok(secretary),listConversations:async()=>ok([secretary,{...makeConversation(),title:'另外的讨论'}])});
 const ui=render(<AgentApp/>);await waitFor(()=>assert.ok(ui.getByText('之前整理过的收件事项')));
 assert.equal(ui.getAllByRole('button',{name:'秘书'}).length,1);assert.equal(ui.queryByRole('button',{name:'删除对话：Secretary'}),null);
 assert.equal(ui.getByRole('button',{name:'秘书'}).getAttribute('aria-current'),'page');
 fireEvent.click(ui.getByRole('button',{name:'新对话'}));assert.ok(ui.getByRole('heading',{name:'有什么需要一起处理？'}));assert.equal(ui.queryByRole('button',{name:'单独处理这件事'}),null);
 fireEvent.change(ui.getByLabelText('发送给助手'),{target:{value:'普通对话没写完'}});fireEvent.click(ui.getByRole('button',{name:'秘书'}));await waitFor(()=>assert.ok(ui.getByText('之前整理过的收件事项')));
 fireEvent.click(ui.getByRole('button',{name:'新对话'}));assert.equal((ui.getByLabelText('发送给助手') as HTMLTextAreaElement).value,'普通对话没写完');
});

test('秘书启动响应较慢时，导航到收件箱或新对话不会被抢回',async()=>{
 let resolve!:(r:ReturnType<typeof ok<AgentConversation>>)=>void;
 setup({getSecretary:()=>new Promise(r=>{resolve=r;})});const ui=render(<AgentApp/>);
 await waitFor(()=>assert.ok(ui.getByText('正在打开对话…')));fireEvent.click(ui.getByRole('button',{name:'收件箱'}));
 resolve(ok(makeSecretary({turns:[{id:'stale',role:'assistant',content:'迟到的秘书内容',createdAt:date}]})));
 await waitFor(()=>assert.ok(ui.getByRole('heading',{name:'收件箱'})));assert.equal(ui.queryByText('迟到的秘书内容'),null);
 fireEvent.click(ui.getByRole('button',{name:'新对话'}));fireEvent.change(ui.getByLabelText('发送给助手'),{target:{value:'保留这个新问题'}});
 assert.equal((ui.getByLabelText('发送给助手') as HTMLTextAreaElement).value,'保留这个新问题');
});

test('手动单独处理使用用户原文与附件，只创建一次已排队子会话，返回秘书不重复已提交草稿',async()=>{
 const calls:unknown[]=[];let startCalls=0;
 const goal='用报名表整理一页周报，给我 Word 和 PDF，先别发送。';
 const file={id:'file-report',name:'活动报名.csv',status:'ready'} as any;
 const child:AgentConversation={id:'child-report',kind:'task',title:'报名周报',goal,parentConversationId:'secretary',createdAt:date,updatedAt:date,turnCount:1,turns:[{id:'child-request',role:'user',content:goal,createdAt:date,fileIds:[file.id]}],fileIds:[file.id],activeRun:makeRun({id:'child-run',conversationId:'child-report'})};
 setup({createChild:async input=>{calls.push(input);return ok(child);},startTurn:async()=>{startCalls++;throw Error('duplicate execution');},getRun:async()=>ok(child.activeRun!)});
 window.mailFiles={importLocal:async()=>ok([file]),get:async()=>ok(file)} as any;
 const ui=render(<AgentApp/>);await waitFor(()=>assert.ok(ui.getByRole('heading',{name:'今天有什么需要处理？'})));
 assert.equal((ui.getByRole('button',{name:'单独处理这件事'}) as HTMLButtonElement).disabled,true);
 fireEvent.change(ui.getByLabelText('发送给助手'),{target:{value:goal}});fireEvent.click(ui.getByRole('button',{name:'添加参考资料'}));fireEvent.click(ui.getByRole('button',{name:'上传本地文件'}));await waitFor(()=>assert.ok(ui.getByLabelText('移除资料 '+file.name)));
 fireEvent.click(ui.getByRole('button',{name:'单独处理这件事'}));await waitFor(()=>assert.ok(ui.getByRole('button',{name:'返回秘书'})));
 assert.deepEqual(calls,[{parentConversationId:'secretary',title:goal.slice(0,50),goal,fileIds:[file.id]}]);assert.equal(startCalls,0);
 assert.ok(ui.getByRole('region',{name:'任务上下文'}));assert.ok(ui.getByRole('button',{name:'暂停执行'}));assert.equal(ui.queryByRole('button',{name:'单独处理这件事'}),null);
 fireEvent.click(ui.getByRole('button',{name:'返回秘书'}));await waitFor(()=>assert.ok(ui.getByRole('heading',{name:'今天有什么需要处理？'})));
 assert.equal((ui.getByLabelText('发送给助手') as HTMLTextAreaElement).value,'');assert.equal(ui.queryByLabelText('移除资料 '+file.name),null);
});

test('对话产物进入真实子对话，不误走邮件草稿接口',async()=>{
 let mailDraftReads=0;const child={...makeConversation(),id:'child-a',kind:'task' as const,parentConversationId:'secretary',goal:'核对活动通知'};
 const secretary=makeSecretary({turns:[{id:'artifact-answer',role:'assistant',content:'已另开任务。',createdAt:date,artifacts:[{id:child.id,kind:'conversation',title:'活动核对任务'}]}]});
 setup({getSecretary:async()=>ok(secretary),getConversation:async id=>{assert.equal(id,child.id);return ok(child);},getMailDraft:async()=>{mailDraftReads++;throw Error('wrong route');}});
 const ui=render(<AgentApp/>);await waitFor(()=>assert.ok(ui.getByRole('button',{name:/活动核对任务.*任务对话/})));
 fireEvent.click(ui.getByRole('button',{name:/活动核对任务.*任务对话/}));await waitFor(()=>assert.ok(ui.getByRole('button',{name:'返回秘书'})));assert.equal(mailDraftReads,0);
});

test('分派失败保留父输入和附件；成功迟到不会抢回已切换的普通对话',async()=>{
 let resolve!:(r:ReturnType<typeof ok<AgentConversation>>)=>void,attempt=0;
 const file={id:'source-file',name:'报名表.csv'} as any,goal='请整理这份报名表';
 setup({createChild:async()=>{if(++attempt===1)return {ok:false,error:'任务创建暂时失败'};return new Promise(r=>{resolve=r;});}});window.mailFiles={importLocal:async()=>ok([file])} as any;
 const ui=render(<AgentApp/>);await waitFor(()=>assert.ok(ui.getByRole('heading',{name:'今天有什么需要处理？'})));
 fireEvent.click(ui.getByRole('button',{name:'新对话'}));fireEvent.change(ui.getByLabelText('发送给助手'),{target:{value:'普通对话已有未发送内容'}});fireEvent.click(ui.getByRole('button',{name:'秘书'}));await waitFor(()=>assert.ok(ui.getByRole('heading',{name:'今天有什么需要处理？'})));
 fireEvent.change(ui.getByLabelText('发送给助手'),{target:{value:goal}});fireEvent.click(ui.getByRole('button',{name:'添加参考资料'}));fireEvent.click(ui.getByRole('button',{name:'上传本地文件'}));await waitFor(()=>assert.ok(ui.getByLabelText('移除资料 '+file.name)));
 fireEvent.click(ui.getByRole('button',{name:'单独处理这件事'}));await waitFor(()=>assert.ok(ui.getByText('任务创建暂时失败')));assert.equal((ui.getByLabelText('发送给助手') as HTMLTextAreaElement).value,goal);assert.ok(ui.getByLabelText('移除资料 '+file.name));
 fireEvent.click(ui.getByRole('button',{name:'单独处理这件事'}));await waitFor(()=>assert.ok(resolve));fireEvent.click(ui.getByRole('button',{name:'新对话'}));
 resolve(ok({...makeConversation(),id:'created-late',kind:'task',parentConversationId:'secretary',goal}));await waitFor(()=>assert.equal((ui.getByLabelText('发送给助手') as HTMLTextAreaElement).disabled,false));
 assert.equal((ui.getByLabelText('发送给助手') as HTMLTextAreaElement).value,'普通对话已有未发送内容');assert.equal(ui.queryByRole('button',{name:'返回秘书'}),null);
 fireEvent.click(ui.getByRole('button',{name:'秘书'}));await waitFor(()=>assert.ok(ui.getByRole('heading',{name:'今天有什么需要处理？'})));assert.equal((ui.getByLabelText('发送给助手') as HTMLTextAreaElement).value,'');assert.equal(ui.queryByLabelText('移除资料 '+file.name),null);
});

test('秘书后台子任务状态刷新不覆盖正在输入的下一件事',async()=>{
 const child={...makeConversation(),id:'child-working',kind:'task' as const,title:'正在整理的报告',status:'running' as const};
 setup({getSecretary:async()=>ok(makeSecretary({children:[child]})),getConversation:async id=>{assert.equal(id,child.id);return ok({...child,status:'completed'});}});
 const ui=render(<AgentApp/>);await waitFor(()=>assert.ok(ui.getByLabelText('任务对话')));fireEvent.click(ui.getByText('任务对话'));
 fireEvent.change(ui.getByLabelText('发送给助手'),{target:{value:'这句先别动，我还在补充'}});
 await waitFor(()=>assert.ok(ui.getByRole('button',{name:/正在整理的报告.*已完成/})),{timeout:3500});
 assert.equal((ui.getByLabelText('发送给助手') as HTMLTextAreaElement).value,'这句先别动，我还在补充');
});

test('再次点秘书并切换侧页后，当前run仍继续轮询而非永久停在旧状态',async()=>{
 let current=makeRun({id:'secretary-active',conversationId:'secretary',status:'running'});
 const value=()=>makeSecretary({turns:[{id:'s-request',role:'user',content:'整理新邮件',createdAt:date},...(current.status==='completed'?[{id:'s-answer',role:'assistant' as const,content:'收件整理实际完成',createdAt:date,runId:current.id}]:[])],activeRun:current});
 setup({getSecretary:async()=>ok(value()),getConversation:async()=>ok(value()),getRun:async()=>ok(current)});
 const ui=render(<AgentApp/>);await waitFor(()=>assert.ok(ui.getByRole('button',{name:'暂停执行'})));
 fireEvent.click(ui.getByRole('button',{name:'秘书'}));await waitFor(()=>assert.ok(!ui.queryByText('正在打开对话…')));
 fireEvent.click(ui.getByRole('button',{name:'收件箱'}));fireEvent.click(ui.getByRole('button',{name:'秘书'}));await waitFor(()=>assert.ok(!ui.queryByText('正在打开对话…')));
 current={...current,status:'completed',partialText:'收件整理实际完成'};
 await waitFor(()=>assert.ok(ui.getByText('收件整理实际完成')));assert.equal(ui.queryByRole('button',{name:'暂停执行'}),null);
});

test('发送后等待读回时切换新对话，迟到结果不覆盖新输入',async()=>{
 let resolve!:(r:ReturnType<typeof ok<AgentConversation>>)=>void;
 setup({getConversation:()=>new Promise(r=>{resolve=r;})});const ui=render(<AgentApp/>);await waitFor(()=>assert.ok(ui.getByRole('heading',{name:'今天有什么需要处理？'})));
 fireEvent.change(ui.getByLabelText('发送给助手'),{target:{value:'先看一下最新邮件'}});fireEvent.click(ui.getByRole('button',{name:'发送消息'}));await waitFor(()=>assert.ok(resolve));
 fireEvent.click(ui.getByRole('button',{name:'新对话'}));resolve(ok(makeConversation()));
 await waitFor(()=>assert.equal((ui.getByLabelText('发送给助手') as HTMLTextAreaElement).disabled,false));fireEvent.change(ui.getByLabelText('发送给助手'),{target:{value:'新的独立问题'}});
 assert.ok(ui.getByRole('heading',{name:'有什么需要一起处理？'}));assert.equal(ui.queryByText('帮我分析 Project 2'),null);assert.equal((ui.getByLabelText('发送给助手') as HTMLTextAreaElement).value,'新的独立问题');
});

test('收件整理分组默认展开待办与重要，折叠普通与疑似垃圾，原文和带入对话可用',async()=>{
 const mail={localId:'mail-action',subject:'明天会议要确认',sender:'虚构同事',body:'请确认明天下午是否参加。',receivedAt:date} as any;
 setup({getSecretary:async()=>ok(makeSecretary({turns:[{id:'brief',role:'assistant',content:'已整理本次收件。',createdAt:date,artifacts:[{kind:'briefing',id:'brief-run',title:'本次收件整理'}]}]})),getBriefing:async id=>{assert.equal(id,'brief-run');return ok({id,counts:{action:1,important:1,review:0,normal:1,spam:1},warning:'还有邮件未分析，稍后可以继续。',items:[{mailId:mail.localId,subject:mail.subject,category:'action',summary:'需要确认会议'},{mailId:'mail-important',subject:'系统维护时间',category:'important'},{mailId:'mail-normal',subject:'本周简讯',category:'normal'},{mailId:'mail-spam',subject:'优惠推送',category:'spam'}]});}});
 window.mailWorkspace.getLocal=async()=>ok(mail);const ui=render(<AgentApp/>);await waitFor(()=>assert.ok(ui.getByText('需要确认会议')));
 const groups=[...ui.container.querySelectorAll<HTMLDetailsElement>('.ag-briefing-group')];assert.deepEqual(groups.map(g=>g.open),[true,true,false,false]);
 assert.ok(ui.getByText('还有邮件未分析，稍后可以继续。'));fireEvent.click(ui.getByRole('button',{name:'阅读邮件：'+mail.subject}));await waitFor(()=>assert.ok(ui.getByText(mail.body)));
 fireEvent.click(ui.getByRole('button',{name:'关闭阅读窗格'}));fireEvent.change(ui.getByLabelText('发送给助手'),{target:{value:'先帮我确认这件事'}});fireEvent.click(ui.getByRole('button',{name:'带入对话：'+mail.subject}));
 await waitFor(()=>assert.ok(ui.getByLabelText('移除邮件 '+mail.subject)));assert.equal((ui.getByLabelText('发送给助手') as HTMLTextAreaElement).value,'先帮我确认这件事');
});

test('整理读取失败可重试，切换结果后旧请求不得显示到新卡片',async()=>{
 let resolve!:(r:any)=>void,calls=0;setup({getBriefing:async id=>{calls++;if(id==='old')return new Promise(r=>{resolve=r;});if(calls===2)return {ok:false,error:'整理结果暂时不可用'};return ok({id,counts:{action:0,important:0,normal:0,spam:0,review:0},items:[]});}});
 const ui=render(<SecretaryBriefing id="old" onRead={()=>{}} onUse={()=>{}}/>);await waitFor(()=>assert.ok(resolve));ui.rerender(<SecretaryBriefing id="new" onRead={()=>{}} onUse={()=>{}}/>);
 await waitFor(()=>assert.ok(ui.getByText('整理结果暂时不可用')));resolve(ok({id:'old',counts:{action:1},items:[{category:'action',mailId:'old-mail',subject:'过时内容不应出现'}]}));
 fireEvent.click(ui.getByRole('button',{name:'重新读取整理'}));await waitFor(()=>assert.ok(ui.getByText('这次没有可展示的整理结果；可查看收件箱或执行记录。')));assert.equal(ui.queryByText('过时内容不应出现'),null);
});


test('同一任务的实时卡共用轮询，原答复仍在进行中时卡更新完成并保留输入',async()=>{
 let status:AgentRun['status']='running',reads=0,starts=0;
 const item={kind:'conversation' as const,id:'live-child',title:'实际报告任务'};
 const secretary=makeSecretary({turns:[{id:'task-card-1',role:'assistant',content:'任务仍在进行中，这是当时的回答。',createdAt:date,artifacts:[item]},{id:'task-card-2',role:'assistant',content:'打开同一个任务查看进度。',createdAt:date,artifacts:[item]}]});
 const child=()=>({...makeConversation(),id:item.id,kind:'task' as const,parentConversationId:'secretary',lastRun:makeRun({id:'live-child-run',conversationId:item.id,status})});
 setup({getSecretary:async()=>ok(secretary),getConversation:async id=>{assert.equal(id,item.id);reads++;return ok(child());},startTurn:async()=>{starts++;throw Error('must not rerun');},createChild:async()=>{starts++;throw Error('must not recreate');}});
 const ui=render(<AgentApp/>);await waitFor(()=>assert.equal(ui.getAllByText(/实时状态：进行中/).length,2));assert.equal(reads,1);
 fireEvent.change(ui.getByLabelText('发送给助手'),{target:{value:'下一件事我还没写完'}});status='completed';
 await waitFor(()=>assert.equal(ui.getAllByText(/实时状态：本轮完成/).length,2),{timeout:3500});assert.equal(reads,2);assert.equal(starts,0);
 assert.ok(ui.getByText('任务仍在进行中，这是当时的回答。'));assert.equal((ui.getByLabelText('发送给助手') as HTMLTextAreaElement).value,'下一件事我还没写完');
 fireEvent.click(ui.getAllByRole('button',{name:/实际报告任务.*查看结果/})[0]);await waitFor(()=>assert.ok(ui.getByRole('button',{name:'返回秘书'})));assert.equal(starts,0);
});

test('实时任务卡显示真实失败，仍可打开任务，不声称文件完成或已发信',async()=>{
 const item={kind:'conversation' as const,id:'failed-child',title:'失败的报告任务'};
 const child={...makeConversation(),id:item.id,kind:'task' as const,parentConversationId:'secretary',lastRun:makeRun({id:'failed-child-run',conversationId:item.id,status:'failed',error:'服务暂时不可用'})};
 setup({getSecretary:async()=>ok(makeSecretary({turns:[{id:'failed-card',role:'assistant',content:'先前回复。',createdAt:date,artifacts:[item]}]})),getConversation:async()=>ok(child)});
 const ui=render(<AgentApp/>);await waitFor(()=>assert.ok(ui.getByText(/实时状态：执行失败/)));assert.ok(!ui.queryByText(/实时状态：本轮完成/));
 fireEvent.click(ui.getByRole('button',{name:/失败的报告任务.*执行失败/}));await waitFor(()=>assert.match(ui.getByRole('status',{name:'执行恢复指引'}).textContent??'',/本轮未完成/));
});

test('任务状态读取失败可见；切页后迟到的状态不覆盖新对话或输入',async()=>{
 let resolve!:(r:ReturnType<typeof ok<AgentConversation>>)=>void,reads=0;
 const item={kind:'conversation' as const,id:'slow-child',title:'状态读取任务'};
 setup({getSecretary:async()=>ok(makeSecretary({turns:[{id:'slow-card',role:'assistant',content:'原任务答复。',createdAt:date,artifacts:[item]}]})),getConversation:async()=>{if(++reads===1)return {ok:false,error:'本地读取暂时失败'};return new Promise(r=>{resolve=r;});}});
 const ui=render(<AgentApp/>);await waitFor(()=>assert.ok(ui.getByText(/实时状态：读取失败/)));assert.ok(ui.getByText('本地读取暂时失败'));
 await waitFor(()=>assert.ok(resolve),{timeout:3500});fireEvent.click(ui.getByRole('button',{name:'新对话'}));fireEvent.change(ui.getByLabelText('发送给助手'),{target:{value:'普通对话的新内容'}});
 resolve(ok({...makeConversation(),id:item.id,lastRun:makeRun({status:'completed'})}));await new Promise(r=>setTimeout(r,10));
 assert.ok(!ui.queryByText('原任务答复。'));assert.ok(!ui.queryByText(/实时状态：本轮完成/));assert.equal((ui.getByLabelText('发送给助手') as HTMLTextAreaElement).value,'普通对话的新内容');
});
