import { test } from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync,readFileSync,writeFileSync,rmSync } from 'node:fs';
import { join } from 'node:path';
import { tmpdir } from 'node:os';
import { MailConnections } from '../src/main/mail-connections';
import { defaultMailFilter,type MailState,type ImapConfigInput } from '../src/shared/mail';
function fixtures(old=false){
 const dir=mkdtempSync(join(tmpdir(),'mail-router-'));
 const make=(id:string,clientId='')=>{let state:MailState={clientId,account:id?{id,email:id+'@example.com',name:id}:null,filter:structuredClone(defaultMailFilter)};return {getState:async()=>structuredClone(state),configure:async(_input:string|ImapConfigInput)=>state,connect:async()=>state,disconnect:async()=>{state={...state,account:null};return state;},saveFilter:async(filter:typeof defaultMailFilter)=>{state={...state,filter};return state;}};};
 const outlook=make(old?'microsoft-id':'',old?'registered-id':''),imap=make('imap-id');
 const graph={listFolders:async()=>[{id:'ms-folder',name:'Microsoft',childCount:0,unreadCount:0,parentId:null}],read:async()=>({messages:[],limited:false}),getMessage:async()=>{throw new Error('unused');}};
 const mailbox={...graph,listFolders:async()=>[{id:'imap-folder',name:'IMAP',childCount:0,unreadCount:0,parentId:null}]};
 return {dir,outlook,imap,graph,mailbox,router:new MailConnections(dir,outlook,graph,imap,mailbox),clean:()=>rmSync(dir,{recursive:true,force:true})};
}

test('新配置默认IMAP，旧微软配置自动沿用；切换按绑定方式路由且保留默认筛选',async()=>{
 const f=fixtures();
 try{
  assert.equal((await f.router.getState()).method,'imap');assert.equal((await f.router.listFolders())[0].id,'imap-folder');
  await f.router.saveFilter({...defaultMailFilter,recentDays:31});await f.router.selectMethod('microsoft');assert.equal((await f.router.listFolders())[0].id,'ms-folder');assert.equal((await f.router.getState()).filter.recentDays,7);
  await f.router.selectMethod('imap');assert.equal((await f.router.getState()).filter.recentDays,31);assert.equal((await f.router.getState()).account!.id,'imap-id');
  assert.deepEqual(JSON.parse(readFileSync(join(f.dir,'mail-connection.json'),'utf8')),{method:'imap'});
  const reopened=new MailConnections(f.dir,f.outlook,f.graph,f.imap,f.mailbox);assert.equal((await reopened.getState()).method,'imap');await assert.rejects(()=>reopened.configure('id'),/先选择/);
 }finally{f.clean();}
 const old=fixtures(true);try{assert.equal((await old.router.getState()).method,'microsoft');}finally{old.clean();}
});

test('切换绑定方式后的迟读取拒绝，即使切回原方式也不能恢复旧结果',async()=>{
 const f=fixtures();let release:(value:{messages:[];limited:boolean})=>void=()=>{};
 try{
  f.mailbox.read=async()=>new Promise(resolve=>{release=resolve;});const pending=f.router.read(defaultMailFilter);await new Promise<void>(resolve=>setImmediate(resolve));
  await f.router.selectMethod('microsoft');await f.router.selectMethod('imap');release({messages:[],limited:false});await assert.rejects(()=>pending,/方式已变化/);
 }finally{f.clean();}
});

test('损坏的方式配置和未知方式拒绝，不静默覆盖',async()=>{
 const f=fixtures();try{
  assert.throws(()=>f.router.selectMethod('other' as never));writeFileSync(join(f.dir,'mail-connection.json'),'bad-json');await assert.rejects(()=>f.router.getState(),/配置损坏/);assert.throws(()=>f.router.selectMethod('imap'),/配置损坏/);assert.equal(readFileSync(join(f.dir,'mail-connection.json'),'utf8'),'bad-json');
 }finally{f.clean();}
});
