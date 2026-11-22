import { test } from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync,readFileSync,rmSync,writeFileSync } from 'node:fs';
import { join } from 'node:path';
import { tmpdir } from 'node:os';
import { ImapAuth } from '../src/main/imap-auth';
import { defaultMailFilter,type ImapConfigInput } from '../src/shared/mail';
const input:ImapConfigInput={email:'user@example.com',username:'',host:'IMAP.Example.COM',port:993,security:'tls',password:'private-secret'};
const vault={available:async()=>true,encrypt:async(value:string)=>Buffer.from(value.split('').reverse().join('')),decrypt:async(value:Buffer)=>({result:value.toString().split('').reverse().join(''),shouldReEncrypt:false})};
const temporary=()=>mkdtempSync(join(tmpdir(),'imap-auth-'));

test('IMAP配置整体加密、公开状态不返凭据、连接验证后重启可读、状态查询不联网',async()=>{
 const dir=temporary();let connects=0;
 try{
  const auth=new ImapAuth(dir,vault,async credentials=>{connects++;assert.equal(credentials.config.host,'imap.example.com');assert.equal(credentials.config.username,input.email);});
  assert.equal((await auth.getState()).account,null);
  const state=await auth.configure(input);assert.equal(connects,0);assert.equal(state.imap?.hasSecret,true);assert.equal(state.account,null);assert.equal(JSON.stringify(state).includes('private-secret'),false);
  assert.equal(readFileSync(join(dir,'imap-auth.bin'),'utf8').includes('private-secret'),false);await assert.rejects(()=>auth.getCredentials(),/连接/);
  const connected=await auth.connect();assert.equal(connects,1);assert.equal(connected.account?.email,input.email);assert.ok(connected.account?.id.startsWith('imap:'));
  const reopened=new ImapAuth(dir,vault,async()=>{throw new Error('should not connect');});assert.deepEqual(await reopened.getState(),connected);assert.equal((await reopened.getCredentials()).config.password,input.password);
  await reopened.getState();assert.equal(connects,1);
 }finally{rmSync(dir,{recursive:true,force:true});}
});

test('空密码只复用同连接，改host需重新填写；默认筛选按账户保存，断开清凭据',async()=>{
 const dir=temporary();
 try{
  const auth=new ImapAuth(dir,vault,async()=>{});await auth.configure(input);const first=await auth.connect();const firstId=first.account!.id;
  const filter={...defaultMailFilter,recentDays:31};await auth.saveFilter(filter);
  await auth.configure({...input,password:''});await auth.connect();assert.equal((await auth.getCredentials()).config.password,input.password);assert.equal((await auth.getState()).account!.id,firstId);
  await assert.rejects(()=>auth.configure({...input,host:'other.example.com',password:''}),/重新填写/);assert.equal((await auth.getState()).account!.id,firstId);
  await auth.configure({...input,email:'other@example.com',password:'another'});assert.deepEqual((await auth.connect()).filter,defaultMailFilter);
  await auth.configure(input);assert.deepEqual((await auth.connect()).filter,filter);
  const disconnected=await auth.disconnect();assert.equal(disconnected.account,null);assert.equal(disconnected.imap?.hasSecret,false);assert.deepEqual(disconnected.filter,filter);await assert.rejects(()=>auth.getCredentials());await assert.rejects(()=>auth.connect());
  await auth.configure({...input,password:'new-secret'});assert.equal((await auth.connect()).account!.id,firstId);assert.deepEqual((await auth.getState()).filter,filter);
 }finally{rmSync(dir,{recursive:true,force:true});}
});

test('并发连接合并，断开/改配置后的迟验证不可恢复旧授权，原始认证错误不泄漏',async()=>{
 const dir=temporary();let release=()=>{};let calls=0;
 try{
  const auth=new ImapAuth(dir,vault,async()=>{calls++;await new Promise<void>(resolve=>{release=resolve;});});await auth.configure(input);
  const first=auth.connect(),second=auth.connect();assert.equal(first,second);await new Promise<void>(resolve=>setImmediate(resolve));await auth.disconnect();release();await assert.rejects(()=>first,/变化|取消/);assert.equal(calls,1);assert.equal((await auth.getState()).account,null);
  const failing=new ImapAuth(dir,vault,async()=>{throw new Error('private-secret AUTH rejected user@example.com');});await failing.configure(input);await assert.rejects(()=>failing.connect(),error=>!String(error).includes('private-secret')&&!String(error).includes('user@example.com'));
  assert.equal((await failing.getState()).account,null);
 }finally{rmSync(dir,{recursive:true,force:true});}
});

test('无系统加密/坏配置明确失败，不允许URL型host或无效筛选覆盖',async()=>{
 const dir=temporary();
 try{
  const auth=new ImapAuth(dir,{...vault,available:async()=>false},async()=>{});await assert.rejects(()=>auth.configure(input),/系统加密/);
  const valid=new ImapAuth(dir,vault,async()=>{});assert.throws(()=>valid.configure({...input,host:'https://imap.example.com/path'}));assert.throws(()=>valid.configure({...input,host:'bad:993'}));
  await valid.configure(input);await valid.connect();assert.throws(()=>valid.saveFilter({...defaultMailFilter,dateMode:'since',since:''}));assert.deepEqual((await valid.getState()).filter,defaultMailFilter);
  writeFileSync(join(dir,'imap-auth.bin'),'invalid');await assert.rejects(()=>valid.getState(),/无法解密|损坏/);
 }finally{rmSync(dir,{recursive:true,force:true});}
});
