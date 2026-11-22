import { test } from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, rmSync, readFileSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { createServer } from 'node:http';
import type { Configuration, AccountInfo, AuthenticationResult, InteractiveRequest } from '@azure/msal-node';
import { OutlookAuth, type OutlookAuthOptions } from '../src/main/outlook-auth';
import { defaultMailFilter } from '../src/shared/mail';
import type { KeyVault } from '../src/main/settings';
const client='11111111-1111-4111-8111-111111111111';
const vault:KeyVault={available:async()=>true,encrypt:async value=>Buffer.from(value.split('').reverse().join('')),decrypt:async value=>({result:value.toString().split('').reverse().join(''),shouldReEncrypt:false})};
const account=(id='student-one'):AccountInfo=>({homeAccountId:id,localAccountId:id,environment:'login.microsoftonline.com',tenantId:'school',username:id+'@school.edu',name:'Student'});
function setup(){
 const dir=mkdtempSync(join(tmpdir(),'outlook-auth-'));let chosen=account(),calls=0,silent=0,browser=0,failSilent=false;
 let interactive:((request:InteractiveRequest)=>Promise<AuthenticationResult>)|null=null;
 const configurations:Configuration[]=[];
 const factory:NonNullable<OutlookAuthOptions['factory']>=config=>{
  configurations.push(config);let cache={accounts:[] as AccountInfo[],secret:'private-refresh-token'};
  return {
   getTokenCache:()=>({serialize:()=>JSON.stringify(cache),deserialize:value=>{cache=JSON.parse(value);},getAllAccounts:async()=>cache.accounts}),
   acquireTokenInteractive:async request=>{calls++;if(interactive)return interactive(request);cache.accounts=[chosen];return {account:chosen,accessToken:'private-access-token'} as AuthenticationResult;},
   acquireTokenSilent:async()=>{silent++;if(failSilent)throw new Error('private-msal-error');return {account:chosen,accessToken:'private-access-token'} as AuthenticationResult;},
  };
 };
 const create=(customVault=vault)=>new OutlookAuth(dir,customVault,async()=>{browser++;},{factory});
 return {dir,create,configurations,get calls(){return calls;},get browser(){return browser;},get silent(){return silent;},set chosen(value:AccountInfo){chosen=value;},set interactive(value:typeof interactive){interactive=value;},set failSilent(value:boolean){failSilent=value;},clean:()=>rmSync(dir,{recursive:true,force:true})};
}
test('Outlook配置、登录缓存加密、重启静默刷新，状态不返回凭据且getState不联网',async()=>{
 const f=setup();try{
  const auth=f.create();assert.equal((await auth.getState()).account,null);assert.equal(f.configurations.length,0);
  await assert.rejects(auth.connect(),/应用/);assert.throws(()=>auth.configure('bad'),/应用/);
  await auth.configure(client);const state=await auth.connect();assert.equal(state.account?.email,'student-one@school.edu');
  assert.equal(f.configurations[0].auth.authority,'https://login.microsoftonline.com/organizations');
  const file=readFileSync(join(f.dir,'outlook-auth.bin'),'utf8');assert.equal(file.includes('private-refresh-token'),false);assert.equal(file.includes('@school.edu'),false);
  assert.equal(JSON.stringify(state).includes('token'),false);
  const restarted=f.create();assert.equal((await restarted.getState()).account?.id,'student-one');assert.equal(f.calls,1);
  assert.equal((await restarted.getToken()).token,'private-access-token');assert.equal(f.silent,1);assert.equal(f.browser,0);
  f.failSilent=true;await assert.rejects(restarted.getToken(),error=>String(error).includes('重新连接')&&!String(error).includes('private-msal-error'));
 }finally{f.clean();}
});
test('筛选条件按账户隔离、断开保留默认条件，换client清会话，非法筛选不覆盖',async()=>{
 const f=setup();try{
  const auth=f.create();await auth.configure(client);await auth.connect();
  const filter={...defaultMailFilter,recentDays:30,folderIds:['course-folder'],includeChildren:true};await auth.saveFilter(filter);
  assert.throws(()=>auth.saveFilter({...filter,recentDays:0}));assert.deepEqual((await auth.getState()).filter,filter);
  await auth.disconnect();await assert.rejects(auth.getToken(),/连接/);await assert.rejects(auth.saveFilter(filter),/连接/);
  f.chosen=account('student-two');await auth.connect();assert.deepEqual((await auth.getState()).filter,defaultMailFilter);
  f.chosen=account('student-one');await auth.connect();assert.deepEqual((await auth.getState()).filter,filter);
  await auth.configure('22222222-2222-4222-8222-222222222222');assert.equal((await auth.getState()).account,null);
 }finally{f.clean();}
});
test('并发连接共用登录，断开后迟登录不重新持久化授权，错误不泄露微软详情',async()=>{
 const f=setup();try{
  const auth=f.create();await auth.configure(client);
  let resolve!:(value:AuthenticationResult)=>void;
  f.interactive=()=>new Promise(done=>{resolve=done;});
  const first=auth.connect(),second=auth.connect();assert.ok(first===second);
  while(!resolve)await new Promise<void>(done=>setImmediate(done));
  await auth.disconnect();resolve({account:account(),accessToken:'late-private-token'} as AuthenticationResult);
  await assert.rejects(first,/取消/);assert.equal((await auth.getState()).account,null);
  f.interactive=async()=>{throw {errorCode:'access_denied',errorMessage:'secret-user@school.edu'};};
  await assert.rejects(auth.connect(),error=>String(error).includes('学校')&&!String(error).includes('secret-user'));
 }finally{f.clean();}
});
test('只允许微软登录HTTPS页面与loopback回调；不可加密/损坏配置明确失败',async()=>{
 const f=setup();try{
  const auth=f.create();await auth.configure(client);
  for(const url of ['https://evil.example/login?redirect_uri=http://localhost:1234','http://login.microsoftonline.com/?redirect_uri=http://localhost:1234','https://login.microsoftonline.com/?redirect_uri=https://evil.example']){
   f.interactive=async request=>{await request.openBrowser(url);return {account:account()} as AuthenticationResult;};await assert.rejects(auth.connect());
  }
  assert.equal(f.browser,0);
  await assert.rejects(f.create({...vault,available:async()=>false}).configure(client),/加密/);
  writeFileSync(join(f.dir,'outlook-auth.bin'),'broken');await assert.rejects(auth.getState(),/配置无法/);
 }finally{f.clean();}
});


test('登录超时通过本机回调取消等待，不保存迟授权且仅请求只读邮件权限',async()=>{
 const f=setup();let rejectLogin:(error:Error)=>void=()=>{};let receivedError='',opened=0;
 const server=createServer((request,response)=>{let body='';request.on('data',chunk=>{body+=chunk;});request.on('end',()=>{receivedError=new URLSearchParams(body).get('error')??'';response.end('cancelled');rejectLogin(new Error('cancelled'));});});
 await new Promise<void>(done=>server.listen(0,'127.0.0.1',done));
 const address=server.address();if(!address||typeof address==='string')throw Error('server');
 const redirect=`http://127.0.0.1:${address.port}/`;
 const factory:NonNullable<OutlookAuthOptions['factory']>=()=>({
  getTokenCache:()=>({serialize:()=>'',deserialize:()=>{},getAllAccounts:async()=>[]}),
  acquireTokenSilent:async()=>{throw Error('unexpected');},
  acquireTokenInteractive:async request=>{
   assert.deepEqual(request.scopes,['https://graph.microsoft.com/Mail.Read']);
   await request.openBrowser('https://login.microsoftonline.com/organizations/oauth2/v2.0/authorize?'+new URLSearchParams({redirect_uri:redirect,state:'test-state'}));
   return await new Promise<AuthenticationResult>((_resolve,reject)=>{rejectLogin=reject;});
  },
 });
 try {
  const auth=new OutlookAuth(f.dir,vault,async()=>{opened++;},{factory,timeoutMs:15});await auth.configure(client);
  await assert.rejects(auth.connect(),/超时/);assert.equal(receivedError,'access_denied');assert.equal(opened,1);assert.equal((await auth.getState()).account,null);
 }finally{server.closeAllConnections();await new Promise<void>(done=>server.close(()=>done()));f.clean();}
});
