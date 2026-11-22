import { test } from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, rmSync } from 'node:fs';
import { join } from 'node:path';
import { tmpdir } from 'node:os';
import { TaskRepository } from '../src/main/repository';
import { SettingsStore } from '../src/main/settings';
import { MailWorkspaceStore, type MailWorkspaceBase } from '../src/main/mail-workspace';
import { createMailAnalysisService } from '../src/main/mail-analysis-service';
import type { MailMessage } from '../src/shared/mail';
import type { Result } from '../src/shared/contracts';
const data=<T,>(result:Result<T>):T=>{if(!result.ok)throw new Error(result.error);return result.data;};
const vault={available:async()=>true,encrypt:async(text:string)=>Buffer.from('encrypted:'+text),decrypt:async(bytes:Buffer)=>({result:bytes.toString().slice(10),shouldReEncrypt:false})};
const item:MailMessage={id:'remote-one',subject:'Assignment',sender:'teacher@example.edu',receivedAt:'2026-10-30T03:00:00Z',preview:'Complete exercise',body:'Complete exercise by November 5.',unread:true,webUrl:'',imported:false};
const target={baseUrl:'https://model.example/v1',model:'test'};
const draft={tempId:'1',parentTempId:null,title:'Exercise',notes:'',due:{mode:'none'},source:'Complete exercise'};
const reply=(drafts:unknown[]=[draft],notes='Analysis summary')=>new Response(JSON.stringify({choices:[{message:{content:JSON.stringify({drafts,notes})}}]}));
async function fixture(){
 const dir=mkdtempSync(join(tmpdir(),'mail-analysis-'));let repo=new TaskRepository(join(dir,'tasks.sqlite')),store=new MailWorkspaceStore(dir);
 const settings=new SettingsStore(dir,vault);await settings.save({...target,apiKey:'test-key'});
 const put=(binding='binding-a',body=item.body)=>store.put(binding,binding+'-identity',binding+'@example.com',{...item,body}).localId;
 return {settings,put,get repo(){return repo;},get store(){return store;},
  api:(request:typeof fetch)=>createMailAnalysisService({} as MailWorkspaceBase,store,repo,settings,request),
  reopen:()=>{store.close();repo.close();store=new MailWorkspaceStore(dir);repo=new TaskRepository(join(dir,'tasks.sqlite'));},
  clean:()=>{store.close();repo.close();rmSync(dir,{recursive:true,force:true});}};
}

test('跨邮箱批量正文与用户要求一次发送，分析不会创建待办；重启确认保存仅创建一次',async()=>{
 const f=await fixture();let calls=0;let sent:any;
 try{
  const first=f.put(),second=f.put('binding-b','Second mailbox content');
  const request=(async(_url,options)=>{calls++;sent=JSON.parse(String(options?.body));return reply();}) as typeof fetch;
  const api=f.api(request),analysis=data(await api.analyze({messageIds:[first,second],instructions:'请比较两封邮件并整理行动项',target}));
  assert.equal(calls,1);assert.ok(analysis.drafts[0].notes?.includes('binding-a@example.com'));assert.ok(analysis.drafts[0].notes?.includes('Assignment'));assert.ok(sent.messages[0].content.includes('请比较两封邮件并整理行动项'));
  assert.ok(sent.messages[1].content.includes('binding-a@example.com'));assert.ok(sent.messages[1].content.includes('binding-b@example.com'));assert.ok(sent.messages[1].content.includes(item.body));assert.ok(sent.messages[1].content.includes('Second mailbox content'));
  assert.equal(f.repo.list().length,0);assert.equal(f.store.getLocal(first).status,'analyzed');assert.equal(f.store.getLocal(second).status,'analyzed');
  f.reopen();const restarted=f.api(request);assert.equal(data(await restarted.getAnalysis(analysis.id)).instructions,'请比较两封邮件并整理行动项');
  assert.equal(data(await restarted.saveAnalysis(analysis.id,analysis.drafts)).count,1);assert.equal(data(await restarted.saveAnalysis(analysis.id,analysis.drafts)).count,0);assert.equal(f.repo.list().length,1);
  f.reopen();assert.equal(f.store.getLocal(first).status,'analyzed');assert.equal(f.store.getLocal(second).status,'analyzed');assert.equal(f.repo.list().length,1);assert.equal(f.store.getAnalysis(analysis.id).saved,true);
 }finally{f.clean();}
});

test('只总结不生成待办时也保存分析结果，重复并发共享同一次请求',async()=>{
 const f=await fixture();let calls=0;
 try{
  const id=f.put(),request=(async()=>{calls++;await new Promise(resolve=>setTimeout(resolve,5));return reply([],'学校通知总结');}) as typeof fetch;
  const api=f.api(request),input={messageIds:[id],instructions:'只总结，不要生成待办',target};
  const one=api.analyze(input),two=api.analyze(input);assert.equal(one,two);const [a,b]=await Promise.all([one,two]);const generated=data(a);assert.equal(data(b).id,generated.id);assert.equal(calls,1);assert.deepEqual(generated.drafts,[]);assert.equal(generated.notes,'学校通知总结');assert.equal(f.repo.list().length,0);
  f.reopen();assert.equal(f.store.getLocal(id).status,'analyzed');assert.equal(f.store.getAnalysis(generated.id).notes,'学校通知总结');
 }finally{f.clean();}
});

test('长邮件分段完整发送，全部成功后才标记；后段失败不保存部分分析状态',async()=>{
 const f=await fixture();
 try{
  const body='a'.repeat(33000)+'END-OF-MAIL',id=f.put('binding-a',body),payloads:string[]=[];
  const request=(async(_url,options)=>{const payload=JSON.parse(String(options?.body));payloads.push(payload.messages[1].content);return reply([],'part summary');}) as typeof fetch;
  const complete=data(await f.api(request).analyze({messageIds:[id],instructions:'总结全文',target}));assert.equal(payloads.length,2);assert.ok(payloads[0]!.includes('a'.repeat(15000)));assert.ok(payloads[1]!.includes('END-OF-MAIL'));assert.equal(f.store.getLocal(id).analysisId,complete.id);assert.ok(complete.notes.includes('第 2 部分'));
  const failedId=f.put('binding-b',body);let calls=0;
  const fail=(async()=>{calls++;return calls===1?reply([],'first part'):new Response('network failed',{status:503});}) as typeof fetch;
  const result=await f.api(fail).analyze({messageIds:[failedId],instructions:'总结全文',target});assert.equal(result.ok,false);assert.equal(calls,2);assert.equal(f.store.getLocal(failedId).analysisId,null);assert.equal(f.store.getLocal(failedId).status,'unanalyzed');assert.equal(f.repo.list().length,0);
 }finally{f.clean();}
});

test('界面AI目标过期、请求中配置改变或本地原文更新时拒绝标记已分析',async()=>{
 const f=await fixture();let calls=0;
 try{
  const id=f.put(),request=(async()=>{calls++;return reply();}) as typeof fetch;
  const stale=await f.api(request).analyze({messageIds:[id],instructions:'',target:{...target,model:'old'}});assert.equal(stale.ok,false);assert.equal(calls,0);assert.equal(f.store.getLocal(id).analysisId,null);
  const changed=(async()=>{calls++;await f.settings.save({...target,model:'changed',apiKey:''});return reply();}) as typeof fetch;
  const during=await f.api(changed).analyze({messageIds:[id],instructions:'',target});assert.equal(during.ok,false);assert.equal(f.store.getLocal(id).analysisId,null);
  await f.settings.save({...target,apiKey:''});
  const modified=(async()=>{f.put('binding-a','edited during analysis');return reply();}) as typeof fetch;
  const sourceChange=await f.api(modified).analyze({messageIds:[id],instructions:'',target});assert.equal(sourceChange.ok,false);assert.equal(f.store.getLocal(id).analysisId,null);assert.equal(f.repo.list().length,0);
 }finally{f.clean();}
});

test('待办已入库但分析保存标记写入失败，重试利用稳定令牌恢复且不重复创建',async()=>{
 const f=await fixture();
 try{
  const id=f.put(),request=(async()=>reply()) as typeof fetch,normal=f.api(request),analysis=data(await normal.analyze({messageIds:[id],instructions:'',target}));
  let fail=true;const store={getLocal:(key:string)=>f.store.getLocal(key),getAnalysis:(key:string)=>f.store.getAnalysis(key),saveAnalysis:(value:Parameters<MailWorkspaceStore['saveAnalysis']>[0])=>f.store.saveAnalysis(value),markAnalysisSaved:(key:string)=>{if(fail){fail=false;throw new Error('disk temporarily unavailable');}f.store.markAnalysisSaved(key);}};
  const api=createMailAnalysisService({} as MailWorkspaceBase,store,f.repo,f.settings,request);
  assert.equal((await api.saveAnalysis(analysis.id,analysis.drafts)).ok,false);assert.equal(f.repo.list().length,1);assert.equal(f.store.getLocal(id).status,'analyzed');
  data(await api.saveAnalysis(analysis.id,analysis.drafts));assert.equal(f.repo.list().length,1);assert.equal(f.store.getLocal(id).status,'analyzed');
 }finally{f.clean();}
});
test('分批添加保留父子层级且不会重复，全部添加后才标记保存',async()=>{
 const f=await fixture();
 try{
  const id=f.put(),sourceParent={tempId:'p',parentTempId:null,title:'父项',notes:'',due:{mode:'none'},source:'src p'},sourceChild={tempId:'c',parentTempId:'p',title:'子项',notes:'',due:{mode:'inherit'},source:'src c'};
  const request=(async()=>reply([sourceParent,sourceChild],'summary')) as typeof fetch;
  const api=f.api(request);const analysis=data(await api.analyze({messageIds:[id],instructions:'',target}));
  const parent=analysis.drafts.find(d=>d.title==='父项')!,child=analysis.drafts.find(d=>d.title==='子项')!;
  const first=data(await api.saveAnalysis(analysis.id,[parent]));
  assert.equal(first.count,1);assert.equal(f.repo.list().length,1);assert.equal(f.store.getAnalysis(analysis.id).saved,false);
  const second=data(await api.saveAnalysis(analysis.id,[parent,child]));
  assert.equal(second.count,1);assert.equal(f.repo.list().length,2);assert.equal(f.store.getAnalysis(analysis.id).saved,true);
  const parentTodo=f.repo.list().find(t=>t.title==='父项')!,childTodo=f.repo.list().find(t=>t.title==='子项')!;
  assert.equal(childTodo.parentId,parentTodo.id);assert.equal(childTodo.due.mode,'inherit');
  const repeat=data(await api.saveAnalysis(analysis.id,[parent,child]));assert.equal(repeat.count,0);assert.equal(f.repo.list().length,2);
  assert.deepEqual(f.store.getAnalysis(analysis.id).addedTempIds?.sort(),analysis.drafts.map(d=>d.tempId).sort());
 }finally{f.clean();}
});
