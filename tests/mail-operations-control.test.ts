import {test} from 'node:test';
import assert from 'node:assert/strict';
import {mkdtempSync,rmSync} from 'node:fs';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {MailOperations} from '../src/main/mail-operations';
import {MailAccountManager,MailWorkspaceStore,type MailConnectionFactory} from '../src/main/mail-workspace';
import {TaskRepository} from '../src/main/repository';
import {SettingsStore} from '../src/main/settings';
import {defaultMailFilter,type MailMessage} from '../src/shared/mail';
import {MailReadError} from '../src/main/mail-errors';
import type {Result} from '../src/shared/contracts';
const data=<T,>(v:Result<T>):T=>{if(!v.ok)throw new Error(v.error);return v.data;};
const vault={available:async()=>true,encrypt:async(v:string)=>Buffer.from('encrypted:'+v),decrypt:async(v:Buffer)=>({result:v.toString().slice(10),shouldReEncrypt:false})};
const target={baseUrl:'https://api.deepseek.com',model:'deepseek-flash'};
const item:MailMessage={id:'remote',subject:'School notice',sender:'teacher@school.edu',receivedAt:'2026-10-30T00:00:00Z',preview:'',body:'Read announcement',unread:true,webUrl:'',imported:false};
const reply=(notes='Summary')=>new Response(JSON.stringify({choices:[{finish_reason:'stop',message:{content:JSON.stringify({drafts:[],notes})}}]}));
async function until<T>(read:()=>Promise<T>,predicate:(value:T)=>boolean,timeout=6000){const limit=Date.now()+timeout;while(Date.now()<limit){const value=await read();if(predicate(value))return value;await new Promise(resolve=>setTimeout(resolve,5));}throw new Error('等待后台状态超时');}
function auth(){const state={clientId:'',account:{id:'identity',email:'a@example.com',name:'A'},filter:defaultMailFilter};return {getState:async()=>state,connect:async()=>state,configure:async()=>state,disconnect:async()=>state,saveFilter:async()=>state};}
async function fixture(factory:MailConnectionFactory,request:typeof fetch=async()=>reply()){
 const dir=mkdtempSync(join(tmpdir(),'mail-control-')),store=new MailWorkspaceStore(dir),repo=new TaskRepository(join(dir,'tasks.sqlite')),settings=new SettingsStore(dir,vault);
 await settings.save({...target,apiKey:'fixture-key'});
 const manager=new MailAccountManager(dir,vault,async()=>{},factory);let completed=0;
 const ops=new MailOperations(dir,manager,store,repo,settings,()=>{},()=>{completed++;},request);
 const account=await manager.addAccount('imap');
 return {store,repo,manager,ops,account,get completed(){return completed;},close:()=>{ops.close();store.close();repo.close();rmSync(dir,{recursive:true,force:true});}};
}
test('取消导入立即结束，迟到响应不写入，也不产生完成提示或允许继续',async()=>{
 let release:(()=>void)|undefined;const messages=[{...item,id:'one'},{...item,id:'two'},{...item,id:'three'}];
 const factory:MailConnectionFactory=()=>({auth:auth(),reader:{read:async()=>({messages,limited:false}),listFolders:async()=>[],getMessage:async(id:string)=>{if(id==='two')return new Promise<MailMessage>(resolve=>{release=()=>resolve({...item,id:'two'});});return {...item,id};},cancelPending:()=>{}}});
 const f=await fixture(factory);try{
  const job=data(await f.ops.api.startImport({bindingIds:[f.account.id],onlyNew:true}));
  await until(async()=>!!release,Boolean);
  data(await f.ops.api.cancelJob(job.id));
  const canceled=await until(async()=>data(await f.ops.api.getJob(job.id)),j=>j.state==='canceled');
  assert.equal(canceled.state,'canceled');assert.equal(f.completed,0);
  // 取消前已保存摘要的邮件保留为“正文待读取”，不会假装完整导入。
  assert.equal(f.store.listLocal({}).total,2);
  const pendingMail=f.store.listLocal({}).messages.find(m=>m.subject==='School notice'&&m.bodyStatus==='pending');assert.ok(pendingMail);
  release!();await new Promise(resolve=>setTimeout(resolve,30));
  assert.equal(f.store.getLocal(pendingMail!.localId).body,'');assert.equal(f.store.getLocal(pendingMail!.localId).bodyStatus,'pending');assert.equal(f.completed,0);
  await assert.rejects(()=>f.ops.api.resumeJob(job.id).then(result=>data(result)),/已取消/);
 }finally{f.close();}
});

test('导入失败明细可定位并单封重试，成功后才计入新增',async()=>{
 let bad=true;const messages=[{...item,id:'good',subject:'Good mail'},{...item,id:'bad',subject:'Broken mail',sender:'sender@example.org'}];
 const factory:MailConnectionFactory=()=>({auth:auth(),reader:{read:async()=>({messages,limited:false}),listFolders:async()=>[],getMessage:async(id:string)=>{if(id==='bad'&&bad)throw new MailReadError('下载正文中断，已重试一次仍失败。','transfer','TRANSFER_RETRY_FAILED');return messages.find(m=>m.id===id)!;},cancelPending:()=>{}}});
 const f=await fixture(factory);try{
  const job=data(await f.ops.api.startImport({bindingIds:[f.account.id],onlyNew:true}));
  const done=await until(async()=>data(await f.ops.api.getJob(job.id)),j=>j.state==='completed');
  assert.equal(done.report?.added,1);assert.equal(done.report?.failed,1);assert.equal(f.store.listLocal({}).total,2);
  assert.equal(f.store.listLocal({}).messages.find(m=>m.subject==='Broken mail')?.bodyStatus,'pending');
  const failure=done.report?.failures?.[0];assert.ok(failure);assert.equal(failure!.subject,'Broken mail');assert.equal(failure!.mailboxEmail,'a@example.com');assert.equal(failure!.sender,'sender@example.org');assert.equal(failure!.stage,'transfer');assert.equal(failure!.code,'TRANSFER_RETRY_FAILED');assert.ok(failure!.reason.includes('下载正文'));
  bad=false;const retry=data(await f.ops.api.retryImportFailures({jobId:job.id,keys:[failure!.key]}));
  const retried=await until(async()=>data(await f.ops.api.getJob(retry.id)),j=>j.state==='completed');
  assert.equal(retried.report?.added,1);assert.equal(retried.report?.failed,0);assert.equal(f.store.listLocal({}).total,2);
  assert.equal(f.store.listLocal({}).messages.find(m=>m.subject==='Broken mail')?.bodyStatus,'complete');
 }finally{f.close();}
});

test('仅导入新邮件不阻止正文补读，摘要保留且状态可分别记录',async()=>{
 let partial=true;const messages=[{...item,id:'large',subject:'Large mail'}];
 const factory:MailConnectionFactory=()=>({auth:auth(),reader:{read:async()=>({messages,limited:false}),listFolders:async()=>[],getMessage:async()=>({...messages[0]!,body:partial?'First part':'Full body',bodyStatus:partial?'partial':'complete',bodyNote:partial?'只读取了前10MB':'',attachments:[]}),cancelPending:()=>{}}});
 const f=await fixture(factory);try{
  const job=data(await f.ops.api.startImport({bindingIds:[f.account.id],onlyNew:true}));
  const done=await until(async()=>data(await f.ops.api.getJob(job.id)),j=>j.state==='completed');
  assert.equal(done.report?.added,1);assert.equal(done.report?.bodyPending,1);assert.equal(done.report?.pendingLocalIds?.length,1);
  assert.equal(f.store.getLocal(done.report!.pendingLocalIds![0]!).bodyStatus,'partial');
  const again=data(await f.ops.api.startImport({bindingIds:[f.account.id],onlyNew:true}));const still=await until(async()=>data(await f.ops.api.getJob(again.id)),j=>j.state==='completed');
  assert.equal(still.report?.failed,0);assert.equal(f.store.listLocal({}).total,1);
  partial=false;const retry=data(await f.ops.api.retryBodies({localIds:done.report!.pendingLocalIds!}));
  const retried=await until(async()=>data(await f.ops.api.getJob(retry.id)),j=>j.state==='completed');
  assert.equal(retried.report?.bodyRead,1);assert.equal(f.store.getLocal(done.report!.pendingLocalIds![0]!).bodyStatus,'complete');assert.equal(f.store.getLocal(done.report!.pendingLocalIds![0]!).body,'Full body');
 }finally{f.close();}
});

test('取消分析保留已完成邮件结果，后续迟到响应不落库',async()=>{
 let secondRelease:(()=>void)|undefined;let calls=0;
 const request=(async(_url,options)=>{calls++;const payload=JSON.parse(String(options?.body));const text=payload.messages.at(-1).content as string;if(text.includes('SECOND-MAIL'))return new Promise<Response>(()=>{secondRelease=()=>{};});return reply('First done');}) as typeof fetch;
 const factory:MailConnectionFactory=()=>({auth:auth(),reader:{read:async()=>({messages:[],limited:false}),listFolders:async()=>[],getMessage:async()=>item,cancelPending:()=>{}}});
 const f=await fixture(factory,request);try{
  const first=f.store.put('binding','identity','a@example.com',{...item,id:'first',body:'FIRST-MAIL'}).localId;
  const second=f.store.put('binding','identity','a@example.com',{...item,id:'second',body:'SECOND-MAIL'}).localId;
  const job=data(await f.ops.api.startAnalysis({messageIds:[first,second],instructions:'分析',target}));
  await until(async()=>calls,n=>n===2);
  data(await f.ops.api.cancelJob(job.id));const canceled=await until(async()=>data(await f.ops.api.getJob(job.id)),j=>j.state==='canceled');
  assert.equal(canceled.state,'canceled');assert.equal(f.completed,0);
  assert.equal(f.store.getLocal(first).status,'analyzed');assert.equal(f.store.getLocal(second).status,'unanalyzed');
  secondRelease?.();await new Promise(resolve=>setTimeout(resolve,30));assert.equal(f.store.getLocal(second).status,'unanalyzed');
 }finally{f.close();}
});
test('长邮件分段合并去重，重用的编号不破坏父子关系或一键保存',async()=>{
 let calls=0;
 const request:typeof fetch=async()=>{
  const drafts=[
   {tempId:'1',parentTempId:null,title:'同一项任务',notes:'共同依据',source:'原文'},
   {tempId:'2',parentTempId:'1',title:`分段子任务 ${++calls}`,notes:'分段依据',source:'原文'},
  ];
  return new Response(JSON.stringify({choices:[{finish_reason:'stop',message:{content:JSON.stringify({drafts,notes:'分段摘要'})}}]}));
 };
 const factory:MailConnectionFactory=()=>({auth:auth(),reader:{read:async()=>({messages:[],limited:false}),listFolders:async()=>[],getMessage:async()=>item,cancelPending:()=>{}}});
 const f=await fixture(factory,request);try{
  const id=f.store.put('binding','identity','a@example.com',{...item,id:'long-merge',body:'内容'.repeat(6000)}).localId;
  const job=data(await f.ops.api.startAnalysis({messageIds:[id],instructions:'合并去重',target}));
  const done=await until(async()=>{const current=data(await f.ops.api.getJob(job.id));if(current.state==='failed')throw new Error(current.error);return current;},j=>j.state==='completed');
  assert.ok(done.total>1);
  const analyses=f.store.listAnalyses();assert.equal(analyses.length,1);
  const merged=analyses[0]!;
  assert.deepEqual(done.analysisIds,[merged.id]);assert.deepEqual(merged.messageIds,[id]);
  assert.equal(merged.drafts.length,calls+1);
  assert.ok(merged.drafts.every(d=>d.sourceMailIds?.[0]===id&&d.notes?.includes(merged.id)));
  assert.equal(f.store.getLocal(id).analysisId,merged.id);
  const saved=f.repo.saveDrafts(merged.drafts,'merge-fixture');assert.equal(saved.value,calls+1);
  const todos=f.repo.list(),parent=todos.find(todo=>todo.title==='同一项任务');
  assert.ok(parent);assert.equal(todos.filter(todo=>todo.parentId===parent.id).length,calls);
 }finally{f.close();}
});
