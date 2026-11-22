import { test } from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, rmSync, readFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { TaskRepository } from '../src/main/repository';
import { SettingsStore, KeyVault } from '../src/main/settings';
import { createBackendService } from '../src/main/service';
import { parseImportedText } from '../src/main/import';
import { extractDrafts } from '../src/main/ai';
import { validateDrafts } from '../src/shared/drafts';
import type { Result } from '../src/shared/contracts';
const config={baseUrl:'https://example.com/v1',model:'test-model',apiKey:'private-test-key'};
// 仅测试替身，不是产品加密实现；桌面适配器注入Electron系统加密。
const vault:KeyVault={available:async()=>true,encrypt:async v=>Buffer.from('test-cipher:'+Buffer.from(v).toString('base64')),decrypt:async v=>({result:Buffer.from(v.toString().slice(12),'base64').toString(),shouldReEncrypt:false})};
function data<T>(result:Result<T>):T { if(!result.ok) throw new Error(result.error); return result.data; }
const mock=(content:string,status=200)=>(async()=>new Response(JSON.stringify({choices:[{message:{content}}]}),{status})) as typeof fetch;
const one={tempId:'a',parentTempId:null,title:'准备报告',notes:'',due:{mode:'value' as const,value:{kind:'date' as const,date:'2026-11-05'}},source:'11月5日提交报告'};

test('服务层：继承显示分离、陈旧修改失败、取消父项保留后代、配置持久化',async()=>{
 const directory=mkdtempSync(join(tmpdir(),'v2-service-'));const repo=new TaskRepository(join(directory,'db.sqlite'));
 try{
  const settings=new SettingsStore(directory,vault);
  const api=createBackendService(repo,settings,{chooseText:async()=>null,writeBackup:async()=>true,chooseBackup:async()=>null});
  const parent=data(await api.createTodo({title:'父项',due:one.due})).value;
  const child=data(await api.createTodo({title:'子项',parentId:parent.id})).value;
  assert.equal(data(await api.effectiveDue(child.id)).displayedDue,null);
  assert.deepEqual(data(await api.effectiveDue(child.id)).deadline,one.due.value);
  const updated=data(await api.updateTodo(parent.id,{due:{mode:'value',value:{kind:'date',date:'2026-11-06'}}},repo.get(parent.id).revision)).value;
  assert.equal((data(await api.effectiveDue(child.id)).deadline as {date:string}).date,'2026-11-06');
  assert.equal((await api.updateTodo(parent.id,{title:'过时编辑'},parent.revision)).ok,false);
  data(await api.setCompleted(parent.id,true,updated.revision));
  data(await api.setCompleted(parent.id,false,repo.get(parent.id).revision));
  assert.equal(repo.get(child.id).completed,true);assert.equal(repo.get(parent.id).completed,false);
  data(await api.setCompletionConfig({cascadeComplete:false,autoCompleteParent:false}));
  assert.equal(repo.getConfig().cascadeComplete,false);
  assert.equal((await api.createTodo({title:'',priority:9} as any)).ok,false);
 }finally{repo.close();rmSync(directory,{recursive:true,force:true});}
});
test('AI提取不写库，确认才写入，重复确认幂等；导出不包含密钥',async()=>{
 const directory=mkdtempSync(join(tmpdir(),'v2-ai-'));const repo=new TaskRepository(join(directory,'db.sqlite'));
 try{
  const settings=new SettingsStore(directory,vault);await settings.save(config);
  let backup='';
  const api=createBackendService(repo,settings,{chooseText:async()=>null,writeBackup:async text=>{backup=text;return true;},chooseBackup:async()=>null},mock(JSON.stringify({drafts:[one],notes:''})));
  const generated=data(await api.extract('11月5日提交报告'));
  assert.equal(repo.list().length,0);assert.ok(generated.confirmationToken);
  assert.equal(data(await api.saveDrafts(generated.drafts,generated.confirmationToken)).value,1);
  assert.equal(data(await api.saveDrafts(generated.drafts,generated.confirmationToken)).value,1);assert.equal(repo.list().length,1);
  assert.equal((await api.saveDrafts([{...one,title:'变化'}],generated.confirmationToken)).ok,false);
  data(await api.exportBackup());assert.equal(backup.includes(config.apiKey),false);assert.equal(backup.includes('encryptedKey'),false);
  assert.equal(JSON.stringify(data(await api.getSettings())).includes(config.apiKey),false);
 }finally{repo.close();rmSync(directory,{recursive:true,force:true});}
});
test('密钥不以明文保存，更换服务禁止复用旧密钥，系统加密不可用不保存',async()=>{
 const directory=mkdtempSync(join(tmpdir(),'v2-key-'));
 try{
  const store=new SettingsStore(directory,vault);await store.save(config);
  assert.equal(readFileSync(join(directory,'settings.json'),'utf8').includes(config.apiKey),false);
  assert.equal((await store.forRequest()).apiKey,config.apiKey);
  await assert.rejects(()=>store.save({...config,baseUrl:'https://other.example/v1',apiKey:''}),/重新输入/);
  await store.save({...config,apiKey:''});assert.equal(store.get().hasApiKey,true);
  store.clearKey();assert.equal(store.get().hasApiKey,false);
  const unavailable=new SettingsStore(directory,{...vault,available:async()=>false});
  await assert.rejects(()=>unavailable.save(config),/不可用/);assert.equal(unavailable.get().hasApiKey,false);
 }finally{rmSync(directory,{recursive:true,force:true});}
});
test('邮件正文解析、取消导入、超大与不支持文件拒绝',async()=>{
 const mail=await parseImportedText('test.eml',Buffer.from('Subject: Meeting\r\nContent-Type: text/plain; charset=utf-8\r\n\r\nPrepare the report'));
 assert.match(mail.text,/Prepare the report/);assert.match(mail.text,/Meeting/);
 const html=await parseImportedText('test.eml',Buffer.from('Content-Type: text/html; charset=utf-8\r\n\r\n<p>Prepare report</p><img src="https://example.invalid/tracker"/>'));
 assert.match(html.text,/Prepare report/);assert.doesNotMatch(html.text,/<img/);
 await assert.rejects(()=>parseImportedText('bad.exe',Buffer.from('x')),/只支持/);
 await assert.rejects(()=>parseImportedText('big.txt',Buffer.alloc(2*1024*1024+1)),/2MB/);
 await assert.rejects(()=>parseImportedText('empty.txt',Buffer.from('  ')),/正文/);
});
test('AI拒绝错误JSON、废弃字段、树循环、缺失父项、超长响应和供应商错误详情',async()=>{
 for(const value of ['not-json',JSON.stringify({drafts:[{...one,priority:1}]}),JSON.stringify({drafts:[{...one,parentTempId:'a'}]})]){
  await assert.rejects(()=>extractDrafts('原文',config,mock(value)));
 }
 assert.throws(()=>validateDrafts([{...one,parentTempId:'missing'}]),/父项/);
 assert.throws(()=>validateDrafts([one,one]),/重复/);
 await assert.rejects(()=>extractDrafts('原文',config,(async()=>new Response('x'.repeat(200001))) as typeof fetch),/过长/);
 await assert.rejects(()=>extractDrafts('原文',config,mock('private details',401)),error=> (error as Error).message.includes('HTTP 401') && !(error as Error).message.includes('private details'));
 const out=await extractDrafts('无待办',config,mock('{"drafts":[],"notes":""}'));assert.equal(out.drafts.length,0);
});
test('备份文件端口取消不改库，错误文件不产生部分导入',async()=>{
 const directory=mkdtempSync(join(tmpdir(),'v2-backup-'));const repo=new TaskRepository(':memory:');
 try{
  const settings=new SettingsStore(directory,vault);
  let content:Buffer|null=null;
  const api=createBackendService(repo,settings,{chooseText:async()=>null,writeBackup:async()=>false,chooseBackup:async()=>content});
  assert.equal(data(await api.importBackup()),false);assert.equal(data(await api.importText()),null);
  content=Buffer.from('{"version":2,"todos":[]}');assert.equal((await api.importBackup()).ok,false);assert.equal(repo.list().length,0);
  content=Buffer.from('bad json');assert.equal((await api.importBackup()).ok,false);
 }finally{repo.close();rmSync(directory,{recursive:true,force:true});}
});

test('密钥异步轮换不能覆盖用户刚保存的服务配置',async()=>{
 const directory=mkdtempSync(join(tmpdir(),'v2-rotation-'));
 try{
  let release!:()=>void;let started!:()=>void;
  const begin=new Promise<void>(resolve=>{started=resolve;});
  const gate=new Promise<void>(resolve=>{release=resolve;});
  let hold=false;
  const rotating:KeyVault={...vault,decrypt:async value=>{const result=await vault.decrypt(value);return {...result,shouldReEncrypt:true};},encrypt:async value=>{if(hold){hold=false;started();await gate;}return vault.encrypt(value);}};
  const store=new SettingsStore(directory,rotating);await store.save(config);
  hold=true;const request=store.forRequest();await begin;
  await store.save({...config,baseUrl:'https://new.example/v1',apiKey:'new-service-key'});
  release();await request;
  assert.equal(store.get().baseUrl,'https://new.example/v1');assert.equal((await store.forRequest()).apiKey,'new-service-key');
 }finally{rmSync(directory,{recursive:true,force:true});}
});

test('详情内容与完成状态一次提交，非法状态不产生部分修改',async()=>{
 const directory=mkdtempSync(join(tmpdir(),'v2-edit-'));const repo=new TaskRepository(':memory:');
 try{
  const api=createBackendService(repo,new SettingsStore(directory,vault),{chooseText:async()=>null,writeBackup:async()=>false,chooseBackup:async()=>null});
  const parent=data(await api.createTodo({title:'原标题'})).value;
  const child=data(await api.createTodo({title:'子项',parentId:parent.id})).value;
  const result=await api.editTodo(parent.id,{title:'错误修改'},'invalid' as any,parent.revision);
  assert.equal(result.ok,false);assert.equal(repo.get(parent.id).title,'原标题');
  data(await api.editTodo(parent.id,{title:'新标题',notes:'一起保存'},true,repo.get(parent.id).revision));
  assert.equal(repo.get(parent.id).title,'新标题');assert.equal(repo.get(child.id).completed,true);
 }finally{repo.close();rmSync(directory,{recursive:true,force:true});}
});
