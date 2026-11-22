import React from 'react';
import {test,afterEach} from 'node:test';
import assert from 'node:assert/strict';
import {JSDOM} from 'jsdom';
import {createRequire} from 'node:module';
import {SkillReview,SkillLibrary} from '../src/renderer/SkillReview';
import {AgentApp} from '../src/renderer/AgentApp';
import {AgentMessageBody} from '../src/renderer/AgentMessageBody';
import {FileCard} from '../src/renderer/MailFiles';
import {dispatchPython} from '../src/main/python-desktop';
import type {SkillRecord,SkillsAPI} from '../src/shared/skills';
import type {AgentAPI} from '../src/shared/agent';
const dom=new JSDOM('<!doctype html><html><body></body></html>',{url:'http://localhost'});
for(const [key,value] of Object.entries({window:dom.window,document:dom.window.document,navigator:dom.window.navigator,HTMLElement:dom.window.HTMLElement}))Object.defineProperty(globalThis,key,{value,configurable:true});
const {render,fireEvent,waitFor,cleanup,within}=createRequire(process.cwd()+'/package.json')('@testing-library/react') as typeof import('@testing-library/react');
afterEach(()=>{cleanup();for(const key of ['skills','agent','mailFiles'])Reflect.deleteProperty(window,key);});
const ok=<T,>(data:T)=>({ok:true as const,data});
const date='2026-11-21T03:00:00Z';
const scope={kind:'customer' as const,label:'Harbor',emails:['orders@harbor.example'],contextKey:''};
const sample:SkillRecord={id:'skill-1',kind:'skill_draft',title:'Harbor 订单脚本',scope,scopeId:'scope-harbor',version:2,versionHash:'b'.repeat(64),parentId:'skill-0',status:'draft',createdAt:date,canApprove:true,canRun:false,packagePath:'skills/harbor/orders/v2',
 proposal:{title:'Harbor 订单脚本',kind:'hybrid',scope,rationale:'列映射和金额计算反复出现；缺价时先沟通。',instruction:'## 规则\n保留料号前导零。遇到缺价请澄清。',markdown:'---\nname: harbor-order\ndescription: Process approved orders\n---\n## 规则\n保留料号前导零。',code:'print("<script>not_browser_code()</script>")',inputs:[{name:'order.csv',description:'客户原订单'}],outputs:[{name:'quote.xlsx',description:'内部确认清单',comparison:'table'}],examples:[],sources:[{kind:'file',id:'reference-1',name:'已完成样例.xlsx'}],teaching:{source:'conversation',conversationId:'chat-1',runId:'run-1',turns:[{id:'user-1',runId:'run-1',createdAt:date,content:'先试两个旧订单，再把缺价单也检查一下。\n<img src=x onerror=alert(1)>',textSha256:'a'.repeat(64)}],omittedUserTurns:1,sourceHash:'c'.repeat(64)},packageManifest:{'main.py':'e'.repeat(64)}},
 validation:{passed:true,status:'passed',normalExamples:2,failureExamples:1,cases:[{name:'首单',kind:'success',passed:true,detail:'所有单元格与独立完成样例一致',exitCode:0},{name:'第二单',kind:'success',passed:true,exitCode:0},{name:'缺价单',kind:'failure',passed:true,detail:'PRICE is required',exitCode:2}]}};
function api(overrides:Partial<SkillsAPI>={}){window.skills={get:async()=>ok(sample),list:async()=>ok([sample]),importPackage:async()=>{throw Error('Unexpected direct import');},approve:async()=>ok({...sample,status:'approved',kind:'skill',canApprove:false,canRun:true}),disable:async()=>ok({...sample,status:'disabled',canApprove:false,canRun:false}),...overrides};}

test('技能审核显示分类、原文、实际校验和代码；显式批准携带预览hash且不重复提交',async()=>{
 const calls:unknown[]=[];api({approve:async value=>{calls.push(value);await new Promise(resolve=>setTimeout(resolve,10));return ok({...sample,status:'approved',canApprove:false,canRun:true});}});
 const sources:unknown[]=[];const ui=render(<SkillReview id={sample.id} onSource={v=>sources.push(v)}/>);
 await waitFor(()=>assert.ok(ui.getByRole('button',{name:'批准并保存到技能库'})));
 assert.equal(calls.length,0);assert.ok(ui.getByText(sample.proposal.rationale));assert.ok(ui.getByText(scope.emails[0]));assert.ok(ui.getByText(/PRICE is required/));
 assert.equal(ui.getByLabelText('技能原始教学').querySelector('blockquote')?.textContent,sample.proposal.teaching!.turns[0].content);
 assert.equal(ui.container.querySelector('script'),null);assert.equal(ui.container.querySelector('img'),null);assert.ok(ui.getByText(sample.proposal.code!));assert.equal(ui.getByLabelText('技能说明').querySelector('pre')?.textContent,sample.proposal.markdown);assert.ok(ui.getByText(sample.versionHash));
 fireEvent.click(ui.getByRole('button',{name:'已完成样例.xlsx'}));assert.deepEqual(sources,[{kind:'file',id:'reference-1',title:'已完成样例.xlsx'}]);
 const button=ui.getByRole('button',{name:'批准并保存到技能库'});fireEvent.click(button);fireEvent.click(button);
 await waitFor(()=>assert.ok(ui.getByRole('button',{name:'停用此技能'})));assert.deepEqual(calls,[{id:sample.id,expectedHash:sample.versionHash}]);
});

test('服务端不允许批准的草稿不可启用，即使个别样例passed=true',async()=>{
 let calls=0;api({get:async()=>ok({...sample,canApprove:false,validation:{...sample.validation,passed:false,status:'needs_examples',summary:'需要两个独立正常样例和一个失败样例。',normalExamples:1,cases:[{...sample.validation.cases[0],stderr:'<script>错误信息原文</script>'}]}}),approve:async()=>{calls++;return ok(sample);}});
 const ui=render(<SkillReview id={sample.id}/>);await waitFor(()=>assert.ok(ui.getByText('样例验证未通过')));
 const button=ui.getByRole('button',{name:'批准并保存到技能库'}) as HTMLButtonElement;assert.equal(button.disabled,true);fireEvent.click(button);assert.equal(calls,0);assert.ok(ui.getByText(/此版本暂不能批准/));assert.ok(ui.getByText('需要两个独立正常样例和一个失败样例。'));assert.ok(ui.getByText('<script>错误信息原文</script>'));assert.equal(ui.container.querySelector('script'),null);
});

test('hash冲突保留原审核内容；停用和被替代版本不提供重新批准',async()=>{
 api({approve:async()=>({ok:false,error:'技能包已变化，请重新生成并核对'})});const ui=render(<SkillReview id={sample.id}/>);await waitFor(()=>assert.ok(ui.getByRole('button',{name:'批准并保存到技能库'})));fireEvent.click(ui.getByRole('button',{name:'批准并保存到技能库'}));await waitFor(()=>assert.match(ui.getByRole('alert').textContent??'',/技能包已变化/));assert.equal(ui.queryByRole('button',{name:'停用此技能'}),null);
 api({get:async id=>ok({...sample,id,status:'superseded',canApprove:false})});ui.rerender(<SkillReview id="older"/>);await waitFor(()=>assert.ok(ui.getByText(/此版本保留用于追溯/)));assert.equal(ui.queryByRole('button',{name:'批准并保存到技能库'}),null);
 api({get:async id=>ok({...sample,id,status:'disabled',canApprove:false})});ui.rerender(<SkillReview id="disabled"/>);await waitFor(()=>assert.ok(ui.getByText(/此版本已停用/)));assert.equal(ui.queryByRole('button',{name:'批准并保存到技能库'}),null);
});

test('文字经验不冒充脚本测试通过，缺失原文不会伪造教学',async()=>{
 const memory:SkillRecord={...sample,proposal:{...sample.proposal,kind:'memory',code:null,inputs:[],outputs:[],examples:[],teaching:{source:'direct-input',turns:[],omittedUserTurns:0,sourceHash:'a'.repeat(64)}},validation:{passed:true,status:'manual_review_required',cases:[]}};
 api({get:async()=>ok(memory)});const ui=render(<SkillReview id={memory.id}/>);await waitFor(()=>assert.ok(ui.getByText(/没有自动验证语义正确/)));
 assert.equal(ui.queryByLabelText('技能脚本'),null);assert.equal(ui.queryByText('样例验证通过'),null);assert.ok(ui.getByText('直接输入；没有可验证的原始对话。'));assert.equal(ui.getByRole('button',{name:'批准并保存到技能库'}).hasAttribute('disabled'),false);
});

test('换技能后忽略旧读取；列表默认仅显示已批准技能',async()=>{
 let finish!:(v:ReturnType<typeof ok<SkillRecord>>)=>void;api({get:id=>id==='old'?new Promise(resolve=>{finish=resolve;}):Promise.resolve(ok({...sample,id,title:'当前技能'}))});const ui=render(<SkillReview id="old"/>);ui.rerender(<SkillReview id="new"/>);await waitFor(()=>assert.ok(ui.getByRole('heading',{name:'当前技能'})));finish(ok({...sample,id:'old',title:'过时技能'}));await new Promise(resolve=>setTimeout(resolve,0));assert.equal(ui.queryByText('过时技能'),null);ui.unmount();
 api({list:async()=>ok([sample,{...sample,id:'active',title:'已批准的技能',status:'approved',canApprove:false}])});const list=render(<SkillLibrary onOpen={()=>{}}/>);await waitFor(()=>assert.ok(list.getByText('已批准的技能')));assert.equal(list.queryByText(sample.title),null);fireEvent.change(list.getByLabelText('技能状态'),{target:{value:'all'}});assert.ok(list.getByText(sample.title));
});

test('技能Markdown链接只允许已查阅来源；不会导航伪造技能ID或执行链接',()=>{
 const opened:unknown[]=[];const source={kind:'skill' as const,id:'approved-1',title:'已批准技能'};
 const ui=render(<AgentMessageBody text="[已批准技能](skill://approved-1) [伪造](skill://other) [执行](javascript:alert(1))" sources={[source]} onSource={v=>opened.push(v)}/>);
 fireEvent.click(ui.getByRole('button',{name:'已批准技能'}));assert.deepEqual(opened,[source]);assert.equal(ui.queryByRole('button',{name:'伪造'}),null);assert.equal(ui.container.querySelector('a'),null);
});

test('技能卡进入正确审核器；设置新增技能库且兼容旧方法入口',async()=>{
 api();let mailReads=0;window.desktop={getSettings:async()=>ok({baseUrl:'https://example.invalid',model:'fixture',hasApiKey:true,dataDirectory:'data'}),listTodos:async()=>ok([])} as any;window.mailWorkspace={getAccounts:async()=>ok([])} as any;
 window.agent={listConversations:async()=>ok([]),listPending:async()=>ok([{kind:'skill_draft',id:sample.id,title:sample.title,conversationId:'c',runId:'r',createdAt:date}]),getMailDraft:async()=>{mailReads++;throw Error('wrong route');}} as unknown as AgentAPI;
 const ui=render(<AgentApp/>);await waitFor(()=>assert.ok(ui.getByText('fixture')));fireEvent.click(within(ui.getByRole('navigation',{name:'主导航'})).getByRole('button',{name:/待确认/}));fireEvent.click(ui.getByRole('button',{name:/Harbor 订单脚本/}));await waitFor(()=>assert.ok(ui.getByLabelText('技能审核')));assert.equal(mailReads,0);fireEvent.click(ui.getByRole('button',{name:'关闭技能与版本'}));fireEvent.click(ui.getByRole('button',{name:'设置'}));assert.ok(ui.getByRole('button',{name:/学习方法.*已批准的经验/}));fireEvent.click(ui.getByRole('button',{name:/技能库.*经验证的脚本/}));await waitFor(()=>assert.ok(ui.getByLabelText('已保存的技能库')));
});

test('技能产物卡可追溯真正的版本，查看不会运行脚本',async()=>{
 const file={id:'output-1',name:'quote.xlsx',extension:'xlsx',byteSize:200,source:'generated',sha256:'f'.repeat(64),warnings:[],skill:{versionId:sample.id,versionHash:sample.versionHash,scopeId:sample.scopeId,approvedVersion:2,inputIds:['input-1','input-2'],inputHashes:{'input-1':'a'.repeat(64),'input-2':'e'.repeat(64)},outputSha256:'f'.repeat(64)}};
 window.mailFiles={get:async()=>ok(file)} as any;const opened:string[]=[];const ui=render(<FileCard id={file.id} onSkill={id=>opened.push(id)}/>);await waitFor(()=>assert.ok(ui.getByLabelText('应用的技能')));assert.ok(ui.getByText('按已批准技能生成 · 版本 2'));assert.match(ui.getByLabelText('应用的技能').textContent??'',/2 份输入/);fireEvent.click(ui.getByRole('button',{name:'查看生成技能'}));assert.deepEqual(opened,[sample.id]);
});

test('技能IPC开放现成包导入和审核；读取不广播，执行不由renderer直接调用',async()=>{
 const calls:unknown[]=[];let changed=0;const backend={call:async(...args:unknown[])=>{calls.push(args);return ok({});}};
 for(const channel of ['skill:list','skill:get'])assert.equal((await dispatchPython(backend,true,channel,[],()=>changed++)).ok,true);assert.equal(changed,0);
 for(const channel of ['skill:approve','skill:disable'])assert.equal((await dispatchPython(backend,true,channel,[{id:'s',expectedHash:'h'}],()=>changed++)).ok,true);assert.equal(changed,2);
 const request={fileId:'zip-1',scope,instruction:'只用于采购单'};assert.equal((await dispatchPython(backend,true,'skill:importPackage',[request],()=>changed++)).ok,true);assert.equal(changed,3);assert.deepEqual(calls.at(-1),['skill:importPackage',request]);
 const before=calls.length;for(const channel of ['skill:propose','skill:run','skill:execute','skill:write'])assert.equal((await dispatchPython(backend,true,channel,[{}],()=>changed++)).ok,false);assert.equal((await dispatchPython(backend,false,'skill:approve',[{}],()=>changed++)).ok,false);assert.equal(calls.length,before);
});


test('导入技能审核展示原包、运行资源和补充用途，不冒称模型开发',async()=>{
 const imported:SkillRecord={...sample,proposal:{...sample.proposal,outputs:[{name:'result.xlsx',description:'内部标准订单',comparison:'workbook'}],importedPackage:{schemaVersion:1,sourceFileId:'package-1',sourceSha256:'a'.repeat(64),originalFiles:{'app/converter.py':'c'.repeat(64)},runtimeFiles:['app/converter.py'],supplement:'只用于指定客户的采购单'}}};
 api({get:async()=>ok(imported)});const sources:unknown[]=[];
 const ui=render(<SkillReview id={sample.id} onSource={v=>sources.push(v)}/>);
 await waitFor(()=>assert.ok(ui.getByLabelText('导入技能来源')));
 assert.match(ui.getByLabelText('导入技能来源').textContent??'',/业务程序未经助手重写/);
 assert.match(ui.getByLabelText('技能输入输出').textContent??'',/核对工作表、数据类型与模板结构/);
 assert.ok(ui.getByText(/只用于指定客户的采购单/));
 fireEvent.click(ui.getByRole('button',{name:'查看原始技能包'}));
 assert.deepEqual(sources,[{kind:'file',id:'package-1',title:'原始技能 ZIP'}]);
});
