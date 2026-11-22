import {DatabaseSync} from 'node:sqlite';
import {createHash} from 'node:crypto';
import {ImapMailbox,type ImapClient} from '../src/main/imap-mailbox';
import {OutlookGraph} from '../src/main/outlook-graph';
import {test} from 'node:test';
import assert from 'node:assert/strict';
import {mkdtempSync,rmSync,readFileSync} from 'node:fs';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {MailWorkspaceStore} from '../src/main/mail-workspace';
import {defaultMailFilter,matchesMailFilter} from '../src/shared/mail';
import type {MailAnalysis} from '../src/shared/mail-workspace';
const item={id:'remote',subject:'School notice',sender:'Teacher <t@school.edu>',receivedAt:'2026-10-30T00:00:00Z',preview:'',body:'UNIQUE-DELETED-EMAIL-BODY-XYZ',unread:true,webUrl:'',imported:false};
const temp=()=>mkdtempSync(join(tmpdir(),'mail-archive-'));
test('包含及排除条件大小写无关，多发件人包含OR且排除优先',()=>{
 assert.ok(matchesMailFilter(item,{...defaultMailFilter,senders:['missing','SCHOOL.EDU'],subject:'notice'}));
 assert.ok(!matchesMailFilter(item,{...defaultMailFilter,senders:['school'],excludeSenders:['Teacher']}));
 assert.ok(!matchesMailFilter(item,{...defaultMailFilter,subjectExclude:'NOTICE'}));
 assert.ok(!matchesMailFilter(item,{...defaultMailFilter,senderExclude:'TEACHER'}));
});
test('预设按账号隔离；归档及删文件夹不删邮件；删除正文保留导入分析记忆和发件人',()=>{
 const dir=temp();let store=new MailWorkspaceStore(dir);
 try{
  const a=store.put('a','identity','a@example.com',item),b=store.put('b','identity','b@example.com',item);
  const folder=store.createLocalFolder('课程');store.moveLocal([a.localId],folder.id);assert.equal(store.listLocal({folderId:folder.id}).total,1);assert.equal(store.listLocal({folderId:null}).total,1);
  store.renameLocalFolder(folder.id,'学校');store.deleteLocalFolder(folder.id);assert.equal(store.listLocal({folderId:null}).total,2);
  const preset=store.savePreset('a',{name:'学校',filter:{...defaultMailFilter,subjectExclude:'广告',senders:[item.sender]}});const bPresets=store.listPresets('b');assert.equal(bPresets.length,1);assert.equal(bPresets[0].name,'默认预设');assert.deepEqual(bPresets[0].filter,defaultMailFilter);assert.equal(store.listPresets('b').length,1);store.deletePreset(bPresets[0].id);assert.equal(store.listPresets('b').length,0);assert.equal(store.listPresets('a')[0].filter.subjectExclude,'广告');assert.throws(()=>store.savePreset('b',{...preset,name:'invalid'}));
  const analysis:MailAnalysis={id:'a1',messageIds:[a.localId],instructions:'summary',notes:'summary only',drafts:[],createdAt:new Date().toISOString(),saved:false,baseUrl:'https://example.com',model:'test'};
  store.saveAnalysis(analysis);store.deleteLocal([a.localId]);assert.equal(store.has('a','identity','remote'),true);assert.equal(store.isDeleted('a','identity','remote'),true);assert.throws(()=>store.getLocal(a.localId));
  assert.equal(store.put('a','identity','a@example.com',item).added,false);assert.equal(store.listLocal({}).total,1);assert.deepEqual(store.knownSenders('a'),[item.sender]);
  store.deleteLocal([b.localId]);store.close();assert.ok(!readFileSync(join(dir,'mail.sqlite')).includes(Buffer.from(item.body)));
  store=new MailWorkspaceStore(dir);assert.ok(store.has('a','identity','remote'));assert.equal(store.getAnalysis('a1').notes,'summary only');assert.equal(store.listPresets('a')[0].id,preset.id);assert.equal(store.listLocal({}).total,0);
 }finally{store.close();rmSync(dir,{recursive:true,force:true});}
});
test('多个分析提交原子化且同标识可幂等重试，不因末批缺项留下成功状态',()=>{
 const dir=temp(),store=new MailWorkspaceStore(dir);try{
  const a=store.put('a','i','a@example.com',item),b=store.put('a','i','a@example.com',{...item,id:'r2'});
  const base={instructions:'',notes:'',drafts:[],createdAt:new Date().toISOString(),saved:false,baseUrl:'https://example.com',model:'test'};
  const x={...base,id:'x',messageIds:[a.localId]},y={...base,id:'y',messageIds:['missing']};
  assert.throws(()=>store.saveAnalyses([x,y]));assert.equal(store.getLocal(a.localId).status,'unanalyzed');assert.equal(store.listAnalyses().length,0);
  store.saveAnalyses([x,{...y,messageIds:[b.localId]}]);store.saveAnalyses([x]);assert.equal(store.listAnalyses().length,2);assert.throws(()=>store.saveAnalyses([{...x,notes:'conflict'}]));
 }finally{store.close();rmSync(dir,{recursive:true,force:true});}
});

test('旧邮件库迁移保留正文、分析状态并回填独立导入收据',()=>{
 const dir=temp(),db=new DatabaseSync(join(dir,'mail.sqlite'));
 const id=createHash('sha256').update(JSON.stringify(['old','identity','remote'])).digest('hex');
 db.exec('CREATE TABLE local_mail(local_id TEXT PRIMARY KEY,binding_id TEXT NOT NULL,identity TEXT NOT NULL,remote_id TEXT NOT NULL,payload TEXT NOT NULL,fingerprint TEXT NOT NULL,analysis_id TEXT,analyzed_at TEXT,saved_at TEXT,UNIQUE(binding_id,identity,remote_id))');
 db.prepare('INSERT INTO local_mail VALUES(?,?,?,?,?,?,?,?,?)').run(id,'old','identity','remote',JSON.stringify({...item,localId:id,bindingId:'old',mailboxIdentity:'identity',accountEmail:'a@example.com',cachedAt:'2026-10-30T00:00:00Z'}),'fingerprint','analysis','2026-10-30T00:00:00Z','2026-10-30T00:00:00Z');db.close();
 const store=new MailWorkspaceStore(dir);try{assert.equal(store.getLocal(id).body,item.body);assert.equal(store.getLocal(id).status,'analyzed');assert.equal(store.getLocal(id).folderId,null);store.deleteLocal([id]);assert.ok(store.has('old','identity','remote'));assert.ok(store.isDeleted('old','identity','remote'));}finally{store.close();rmSync(dir,{recursive:true,force:true});}
});
test('暂停Graph时即使请求适配器忽略Abort也立即结束当前等待',async()=>{
 let called=false;
 const graph=new OutlookGraph({getToken:async()=>({token:'fixture',account:{id:'a',name:'A',email:'a@example.com'}})},(async()=>{called=true;return await new Promise<Response>(()=>{});}) as typeof fetch);
 const work=graph.getMessage('id');await new Promise<void>(resolve=>setImmediate(resolve));assert.ok(called);graph.cancelPending();await assert.rejects(work,/暂停/);
});
test('暂停IMAP关闭连接并结束忽略close的等待，后续新操作正常',async()=>{
 let calls=0,closed=0;
 const creds={config:{email:'a@example.com',username:'',host:'imap.example.com',port:993,security:'tls' as const,password:'fixture'},account:{id:'a',email:'a@example.com',name:'A'}};
 const mailbox=new ImapMailbox(async()=>creds,()=>({on:()=>{},connect:async()=>{calls++;if(calls===1)await new Promise<void>(()=>{});},close:()=>{closed++;},logout:async()=>{}} as unknown as ImapClient));
 const work=mailbox.testConnection();await new Promise<void>(resolve=>setImmediate(resolve));mailbox.cancelPending();await assert.rejects(work,/暂停/);assert.ok(closed>0);await mailbox.testConnection();assert.equal(calls,2);
});
