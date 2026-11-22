import { test } from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync,rmSync } from 'node:fs';
import { join } from 'node:path';
import { tmpdir } from 'node:os';
import { TaskRepository } from '../src/main/repository';
import { SettingsStore } from '../src/main/settings';
import { createMailService,mailConfirmationToken } from '../src/main/mail-service';
import { defaultMailFilter, type MailState, type MailMessage } from '../src/shared/mail';
import type { Result } from '../src/shared/contracts';
const data=<T,>(result:Result<T>):T=>{if(!result.ok)throw new Error(result.error);return result.data;};
const vault={available:async()=>true,encrypt:async(text:string)=>Buffer.from('encrypted:'+text),decrypt:async(bytes:Buffer)=>({result:bytes.toString().slice(10),shouldReEncrypt:false})};
const item:MailMessage={id:'immutable-one',subject:'Assignment',sender:'teacher@example.edu',receivedAt:'2026-10-30T03:00:00Z',preview:'Complete exercise',body:'Complete exercise by November 5.',unread:true,webUrl:'https://outlook.office.com/mail/id/immutable-one',imported:false};
function fixture(){
 const dir=mkdtempSync(join(tmpdir(),'mail-service-')),repo=new TaskRepository(join(dir,'tasks.sqlite')),settings=new SettingsStore(dir,vault);
 let state:MailState={clientId:'11111111-1111-4111-8111-111111111111',account:{id:'school-account',email:'student@example.edu',name:'Student'},filter:structuredClone(defaultMailFilter)};
 const auth={getState:async()=>structuredClone(state),configure:async()=>state,connect:async()=>state,disconnect:async()=>{state={...state,account:null};return state;},saveFilter:async()=>state};
 const graph={listFolders:async()=>[],read:async()=>({messages:[structuredClone(item)],limited:false}),getMessage:async()=>structuredClone(item)};
 return {dir,repo,settings,auth,graph,setState:(next:MailState)=>{state=next;},clean:()=>{repo.close();rmSync(dir,{recursive:true,force:true});}};
}

test('读取邮件不调用AI，单封提取不写库，确认保存后持久化防重并保留原文来源',async()=>{
 const f=fixture();let calls=0;
 try{
  await f.settings.save({baseUrl:'https://model.example/v1',model:'test',apiKey:'test-key'});
  const request=(async()=>{calls++;return new Response(JSON.stringify({choices:[{message:{content:JSON.stringify({drafts:[{tempId:'1',parentTempId:null,title:'Exercise',notes:'',due:{mode:'none'},source:'Complete exercise'}],notes:''})}}]}));}) as typeof fetch;
  const opened:string[]=[];const api=createMailService(f.repo,f.settings,f.auth,f.graph,async url=>{opened.push(url);},request);
  assert.equal(data(await api.read(defaultMailFilter)).messages[0].imported,false);data(await api.getMessage(item.id));assert.equal(calls,0);
  const generated=data(await api.extract(item.id));assert.equal(calls,1);assert.equal(f.repo.list().length,0);
  assert.ok(generated.drafts[0].notes?.includes(item.webUrl));assert.equal(generated.confirmationToken,mailConfirmationToken('school-account',item.id));
  f.repo.saveDrafts(generated.drafts,generated.confirmationToken);assert.equal(data(await api.read(defaultMailFilter)).messages[0].imported,true);
  assert.equal((await api.extract(item.id)).ok,false);assert.equal(calls,1);
  const reopened=new TaskRepository(join(f.dir,'tasks.sqlite'));assert.equal(reopened.hasDraftConfirmation(generated.confirmationToken),true);reopened.close();
  data(await api.openMessage(item.id));assert.deepEqual(opened,[item.webUrl]);
 }finally{f.clean();}
});

test('账户断开后的迟读取不能返回旧邮箱内容，错误不泄露token，打开原文只允许Outlook',async()=>{
 const f=fixture();
 try{
  let release:(value:{messages:MailMessage[];limited:boolean})=>void=()=>{};
  f.graph.read=async()=>new Promise(resolve=>{release=resolve;});
  const api=createMailService(f.repo,f.settings,f.auth,f.graph,async()=>{});
  const pending=api.read(defaultMailFilter);await new Promise<void>(resolve=>setImmediate(resolve));data(await api.disconnect());release({messages:[item],limited:false});
  assert.equal((await pending).ok,false);
  f.setState({clientId:'one',account:{id:'school-account',email:'a',name:'A'},filter:defaultMailFilter});
  f.graph.getMessage=async()=>({...item,webUrl:'https://attacker.example/mail'});assert.equal((await api.openMessage(item.id)).ok,false);
  f.graph.getMessage=async()=>{throw new Error('Cannot connect https://secret.example/path Bearer secret-token');};
  const failure=await api.getMessage(item.id);assert.equal(failure.ok,false);assert.equal(JSON.stringify(failure).includes('secret-token'),false);assert.equal(JSON.stringify(failure).includes('secret.example'),false);
  assert.equal((await api.getMessage('')).ok,false);
 }finally{f.clean();}
});

test('长邮件可查看但不静默截断发送AI；未配置模型不生成；同邮件并发生成合并',async()=>{
 const f=fixture();
 try{
  let calls=0;
  const request=(async()=>{calls++;await new Promise(resolve=>setTimeout(resolve,5));return new Response(JSON.stringify({choices:[{message:{content:'{"drafts":[],"notes":""}'}}]}));}) as typeof fetch;
  const api=createMailService(f.repo,f.settings,f.auth,f.graph,async()=>{},request);
  assert.equal((await api.extract(item.id)).ok,false);assert.equal(calls,0);
  await f.settings.save({baseUrl:'https://model.example/v1',model:'test',apiKey:'test-key'});
  f.graph.getMessage=async()=>({...item,body:'x'.repeat(20001)});
  assert.equal(data(await api.getMessage(item.id)).body.length,20001);assert.equal((await api.extract(item.id)).ok,false);assert.equal(calls,0);
  f.graph.getMessage=async()=>structuredClone(item);
  assert.equal((await api.extract(item.id,{baseUrl:'https://old.example/v1',model:'test'})).ok,false);assert.equal(calls,0);
  const [one,two]=await Promise.all([api.extract(item.id),api.extract(item.id)]);assert.equal(one.ok,true);assert.equal(two.ok,true);assert.equal(calls,1);assert.equal(f.repo.list().length,0);
 }finally{f.clean();}
});


test('共用邮件服务支持IMAP配置与草稿来源，不把协议当邮箱品牌',async()=>{
 const f=fixture();
 try {
  const input={email:'person@custom.example',username:'person',host:'mail.custom.example',port:993,security:'tls' as const,password:'private-auth-code'};
  let configured=0;
  const auth=Object.assign(f.auth,{selectMethod:async(method:'imap'|'microsoft')=>{const next={clientId:'',method,account:{id:'imap-account',email:input.email,name:input.email},filter:defaultMailFilter};f.setState(next);return next;},configureImap:async()=>{configured++;return await f.auth.getState();}});
  await f.settings.save({baseUrl:'https://model.example/v1',model:'test',apiKey:'test-key'});
  f.graph.getMessage=async()=>({...item,id:'["INBOX","77",1]',webUrl:''});
  const request=(async()=>new Response(JSON.stringify({choices:[{message:{content:'{"drafts":[{"tempId":"1","parentTempId":null,"title":"待办","notes":"","due":{"mode":"none"},"source":"邮件"}],"notes":""}'}}]}))) as typeof fetch;
  const api=createMailService(f.repo,f.settings,auth,f.graph,async()=>{},request);
  assert.equal(data(await api.selectMethod('imap')).method,'imap');data(await api.configureImap(input));assert.equal(configured,1);
  assert.equal((await api.configureImap({...input,host:'https://mail.custom.example'})).ok,false);assert.equal(configured,1);
  const generated=data(await api.extract('["INBOX","77",1]'));assert.ok(generated.drafts[0].notes?.includes('邮件来源：Assignment'));assert.equal(generated.drafts[0].notes?.includes('Outlook：'),false);
  assert.equal((await api.openMessage('["INBOX","77",1]')).ok,false);
 }finally{f.clean();}
});

test('连接方式改变也隔离迟响应，即使账号标识相同',async()=>{
 const f=fixture();
 try {
  const account={id:'same',email:'person@example.edu',name:'Person'};f.setState({clientId:'',method:'microsoft',account,filter:defaultMailFilter});
  let release:(value:{messages:MailMessage[];limited:boolean})=>void=()=>{};f.graph.read=async()=>new Promise(resolve=>{release=resolve;});
  const api=createMailService(f.repo,f.settings,f.auth,f.graph,async()=>{});const pending=api.read(defaultMailFilter);
  await new Promise<void>(resolve=>setImmediate(resolve));f.setState({clientId:'',method:'imap',account,filter:defaultMailFilter});release({messages:[item],limited:false});assert.equal((await pending).ok,false);
  assert.equal((await api.selectMethod('imap')).ok,false);assert.equal((await api.configureImap({email:'a@b.com',username:'',host:'mail.example',port:993,security:'tls',password:'secret'})).ok,false);
 }finally{f.clean();}
});
