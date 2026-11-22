import { test } from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, rmSync, writeFileSync, readFileSync, existsSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { MailAccountManager, MailWorkspaceStore, createMailWorkspaceBase, type MailConnectionFactory } from '../src/main/mail-workspace';
import { defaultMailFilter, type MailMessage, type MailState } from '../src/shared/mail';
import type { MailAnalysis } from '../src/shared/mail-workspace';
const vault={available:async()=>true,encrypt:async(value:string)=>Buffer.from(value),decrypt:async(value:Buffer)=>({result:value.toString(),shouldReEncrypt:false})};
const message:MailMessage={id:'remote-1',subject:'School work',sender:'teacher@example.com',receivedAt:'2026-10-30T08:00:00Z',preview:'body',unread:true,webUrl:'',imported:false,body:'Do homework'};
const temp=()=>mkdtempSync(join(tmpdir(),'mail-workspace-'));

test('本地正文离线读取，重启去重，分析记录和已保存状态分别持久化',()=>{
 const dir=temp();let store=new MailWorkspaceStore(dir);
 try{
  const first=store.put('binding-a','identity-a','a@example.com',message);store.put('binding-b','identity-b','b@example.com',message);
  assert.equal(store.listLocal({}).total,2);assert.equal(store.listLocal({bindingIds:['binding-a']}).total,1);assert.equal(store.listLocal({search:'SCHOOL'}).total,2);
  const analysis:MailAnalysis={id:'analysis-1',messageIds:[first.localId],instructions:'extract tasks',notes:'one task',drafts:[],createdAt:'2026-10-30T09:00:00Z',saved:false,model:'test',baseUrl:'https://example.com/v1'};
  store.saveAnalysis(analysis);assert.equal(store.getLocal(first.localId).status,'analyzed');store.markAnalysisSaved(analysis.id);
  store.saveAnalysis({...analysis,id:'analysis-2'});assert.equal(store.getLocal(first.localId).status,'analyzed');
  store.put('binding-a','identity-a','a@example.com',message);assert.equal(store.getLocal(first.localId).analysisId,'analysis-2');
  store.put('binding-a','identity-a','a@example.com',{...message,body:'Changed'});assert.equal(store.getLocal(first.localId).analysisId,null);assert.equal(store.getLocal(first.localId).status,'unanalyzed');
  store.close();store=new MailWorkspaceStore(dir);assert.equal(store.getLocal(first.localId).body,'Changed');assert.equal(store.getAnalysis(analysis.id).saved,true);assert.equal(store.listLocal({status:'unanalyzed'}).total,2);
  assert.throws(()=>store.saveAnalysis({...analysis,id:'analysis-3',messageIds:[first.localId,'missing']}));assert.throws(()=>store.getAnalysis('analysis-3'));assert.equal(store.getLocal(first.localId).analysisId,null);
 }finally{store.close();rmSync(dir,{recursive:true,force:true});}
});

test('迁移两类旧配置引用，多邮箱导入只拉取未缓存正文，部分失败不丢成功邮件',async()=>{
 const dir=temp();writeFileSync(join(dir,'imap-auth.bin'),'untouched-imap');writeFileSync(join(dir,'outlook-auth.bin'),'untouched-ms');
 let bodyCalls=0;let online=true;const states=new Map<string,MailState>();
 const factory:MailConnectionFactory=(entry)=>{const state:MailState={method:entry.method,clientId:'',account:{id:entry.id,email:entry.id+'@example.com',name:entry.id},filter:{...defaultMailFilter}};states.set(entry.id,state);return {
  auth:{getState:async()=>structuredClone(state),configure:async()=>state,connect:async()=>state,disconnect:async()=>{state.account=null;return state;},saveFilter:async filter=>{state.filter=filter;return state;}},
  reader:{listFolders:async()=>[],read:async()=>{if(!online||entry.method==='microsoft')throw new Error('network private-secret');return {messages:[message],limited:false};},getMessage:async()=>{bodyCalls++;return message;}}
 };};
 let store=new MailWorkspaceStore(dir);
 try{
  let manager=new MailAccountManager(dir,vault,async()=>{},factory);const accounts=await manager.getAccounts();assert.equal(accounts.length,2);assert.deepEqual(accounts.map(a=>a.id),['legacy-imap','legacy-microsoft']);
  const added=await manager.addAccount('imap');assert.ok(!added.id.startsWith('legacy-'));assert.equal((await manager.getAccounts()).length,3);
  let api=createMailWorkspaceBase(manager,store,async()=>{},()=>true);const request={bindingIds:accounts.map(a=>a.id),onlyNew:true};
  const first=await api.importMail(request);assert.ok(first.ok);if(!first.ok)return;assert.equal(first.data.added,1);assert.equal(first.data.failed,1);assert.ok(!JSON.stringify(first).includes('private-secret'));assert.equal(bodyCalls,1);
  await api.importMail(request);assert.equal(bodyCalls,1);
  store.close();store=new MailWorkspaceStore(dir);manager=new MailAccountManager(dir,vault,async()=>{},factory);api=createMailWorkspaceBase(manager,store,async()=>{});online=false;
  const local=await api.listLocal({});assert.ok(local.ok);if(local.ok){assert.equal(local.data.messages[0]?.body,'');const full=await api.getLocal(local.data.messages[0]!.localId);assert.ok(full.ok);if(full.ok)assert.equal(full.data.body,message.body);}await api.importMail(request);assert.equal(bodyCalls,1);
  assert.equal(readFileSync(join(dir,'imap-auth.bin'),'utf8'),'untouched-imap');assert.equal(readFileSync(join(dir,'outlook-auth.bin'),'utf8'),'untouched-ms');
  await assert.rejects(()=>manager.getAccount('../escape'));
 }finally{store.close();rmSync(dir,{recursive:true,force:true});}
});

test('损坏的多邮箱注册文件不被覆盖',()=>{const dir=temp();const file=join(dir,'mail-accounts.json');writeFileSync(file,'bad-config');try{assert.throws(()=>new MailAccountManager(dir,vault,async()=>{}));assert.equal(readFileSync(file,'utf8'),'bad-config');}finally{rmSync(dir,{recursive:true,force:true});}});

test('删除邮箱只移除绑定与登录凭据，本地邮件和预设保留',async()=>{
 const dir=temp();writeFileSync(join(dir,'imap-auth.bin'),'legacy-secret');
 const factory:MailConnectionFactory=(entry)=>{const state:MailState={method:entry.method,clientId:'',account:{id:entry.id,email:entry.id+'@example.com',name:entry.id},filter:{...defaultMailFilter}};return {auth:{getState:async()=>structuredClone(state),configure:async()=>state,connect:async()=>state,disconnect:async()=>{state.account=null;return state;},saveFilter:async filter=>{state.filter=filter;return state;}},reader:{listFolders:async()=>[],read:async()=>({messages:[],limited:false}),getMessage:async()=>message}};};
 let store=new MailWorkspaceStore(dir);
 try{
  const manager=new MailAccountManager(dir,vault,async()=>{},factory);
  assert.deepEqual((await manager.getAccounts()).map(account=>account.id),['legacy-imap']);
  const added=await manager.addAccount('imap');
  writeFileSync(join(dir,'mail-accounts',added.id,'imap-auth.bin'),'secret');
  store.put(added.id,'identity','a@example.com',message);
  store.savePreset(added.id,{name:'保留预设',filter:defaultMailFilter});
  await manager.removeAccount(added.id);
  assert.equal((await manager.getAccounts()).length,1);
  assert.equal(existsSync(join(dir,'mail-accounts',added.id)),false);
  assert.equal(store.listLocal({}).total,1);
  assert.ok(store.listPresets(added.id).some(preset=>preset.name==='保留预设'));
  await manager.removeAccount('legacy-imap');
  assert.equal(existsSync(join(dir,'imap-auth.bin')),false);
  assert.equal((await manager.getAccounts()).length,0);
 }finally{store.close();rmSync(dir,{recursive:true,force:true});}
});
