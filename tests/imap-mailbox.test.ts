import { test } from 'node:test';
import assert from 'node:assert/strict';
import { EventEmitter } from 'node:events';
import { Readable } from 'node:stream';
import type { FetchMessageObject, ImapFlowOptions, ListResponse } from 'imapflow';
import { ImapMailbox, type ImapClient, type ImapCredentials } from '../src/main/imap-mailbox';
import { defaultMailFilter } from '../src/shared/mail';
const credentials:ImapCredentials={config:{email:'user@example.test',username:'user',host:'imap.example.test',port:993,security:'tls',password:'secret-value'},account:{id:'imap-account',email:'user@example.test',name:'User'}};
const folder=(path:string,parentPath='',flags:string[]=[])=>({path,parentPath,name:path.split('/').at(-1),delimiter:'/',flags:new Set(flags),status:{unseen:2}} as ListResponse);
const row=(uid:number,subject='Assignment',date='2026-10-30T00:00:00Z',seen=false)=>({seq:uid,uid,envelope:{subject,from:[{name:'Teacher',address:'teacher@school.test'}]},internalDate:new Date(date),flags:new Set(seen?['\Seen']:[]),size:0} as FetchMessageObject);
class Client extends EventEmitter {
 options:ImapFlowOptions|undefined;rows=new Map<string,FetchMessageObject[]>([['INBOX',[row(1)]]]);folders=[folder('INBOX')];mailbox:any=false;validity='42';opens:string[]=[];fetches:number[][]=[];searches:any[]=[];downloads=0;closed=0;loggedOut=0;connected=0;
 source=Buffer.from('From: teacher@school.test\r\nSubject: Assignment\r\nContent-Type: text/plain; charset=utf-8\r\n\r\nRead the book.');
 downloadPart:unknown;downloadMax:number|undefined;failSource=false;
 async connect(){this.connected++;}
 async logout(){this.loggedOut++;}
 close(){this.closed++;}
 async list(){return this.folders;}
 async getMailboxLock(path:string,options:any){assert.equal(options.readOnly,true);this.opens.push(path);this.mailbox={path,uidValidity:BigInt(this.validity)};return {release:()=>{}};}
 async search(value:any,options:any){assert.equal(options.uid,true);this.searches.push(value);return (this.rows.get(this.mailbox.path)??[]).map(r=>r.uid);}
 async *fetch(uids:number[],query:any,options:any){assert.equal(options.uid,true);assert.equal(query.source,undefined);assert.equal(query.envelope,true);this.fetches.push(uids);for(const value of this.rows.get(this.mailbox.path)??[])if(uids.includes(value.uid))yield value;}
 async fetchOne(uid:string,query:any,options:any){assert.equal(options.uid,true);const result=(this.rows.get(this.mailbox.path)??[]).find(r=>r.uid===Number(uid));if(!result)return false;
  if(query.source){if(this.failSource)throw new Error('socket closed');const start=query.source.start??0,max=query.source.maxLength??this.source.length;return {...result,size:result.size||this.source.length,source:this.source.subarray(start,start+max)};}
  return {...result,size:result.size||this.source.length};}
 async download(uid:string,part:unknown,options:any){assert.equal(options.uid,true);this.downloads++;this.downloadPart=part;this.downloadMax=options.maxBytes;return {meta:{},content:Readable.from([this.source])};}
}
function setup(client=new Client(),options:{config?:Partial<ImapCredentials['config']>;creds?:()=>Promise<ImapCredentials>;timeoutMs?:number}={}){
 const value={...credentials,config:{...credentials.config,...options.config}};
 const adapter=new ImapMailbox(options.creds??(async()=>value),next=>{client.options=next;return client as unknown as ImapClient;},()=>new Date('2026-10-30T12:00:00Z'),options.timeoutMs??60000);
 return {client,adapter};
}

test('IMAP连接强制TLS或STARTTLS、证书验证、禁日志与IDLE；每次finally退出',async()=>{
 const one=setup();await one.adapter.testConnection();assert.equal(one.client.options?.secure,true);assert.equal(one.client.options?.tls?.rejectUnauthorized,true);assert.equal(one.client.options?.logger,false);assert.equal(one.client.options?.disableAutoIdle,true);assert.equal(one.client.options?.clientInfo?.name,'Task Manager');assert.equal(one.client.options?.auth?.user,'user');assert.equal(one.client.loggedOut,1);assert.equal(one.client.closed,1);
 const two=setup(new Client(),{config:{security:'starttls',port:143}});await two.adapter.testConnection();assert.equal(two.client.options?.secure,false);assert.equal(two.client.options?.doSTARTTLS,true);
});

test('Unicode文件夹按层级返回，Noselect父目录可展开，含子目录并集不重复读取',async()=>{
 const {client,adapter}=setup();client.folders=[folder('INBOX'),folder('课程','',['\Noselect']),folder('课程/作业','课程')];client.rows.set('课程/作业',[row(8)]);
 const roots=await adapter.listFolders();assert.deepEqual(roots.map(f=>f.id),['INBOX','课程']);assert.equal(roots[1].childCount,1);assert.equal((await adapter.listFolders('课程'))[0].name,'作业');
 const read=await adapter.read({...defaultMailFilter,folderIds:['课程','课程/作业'],includeChildren:true});assert.equal(read.messages.length,1);assert.deepEqual(client.opens,['课程/作业']);assert.equal(client.downloads,0);
 client.folders=[folder('A/B/作业','A/B')];assert.deepEqual((await adapter.listFolders()).map(f=>f.id),['A']);assert.equal((await adapter.listFolders('A'))[0].id,'A/B');
});

test('INTERNALDATE按本机日历日精确过滤，SEARCH边界放宽一天避免UTC遗漏',async()=>{
 const prior=process.env.TZ;process.env.TZ='Asia/Shanghai';
 try{const {client,adapter}=setup();client.rows.set('INBOX',[row(1,'before','2026-10-29T15:59:00Z'),row(2,'midnight','2026-10-29T16:00:00Z'),row(3,'end','2026-10-30T15:59:00Z'),row(4,'next','2026-10-30T16:00:00Z')]);
 const result=await adapter.read({...defaultMailFilter,dateMode:'range',since:'2026-10-30',until:'2026-10-30'});assert.deepEqual(result.messages.map(m=>m.subject),['end','midnight']);assert.equal(client.searches[0].since.toISOString(),'2026-10-29T00:00:00.000Z');assert.equal(client.searches[0].before.toISOString(),'2026-11-01T00:00:00.000Z');
 }finally{if(prior===undefined)delete process.env.TZ;else process.env.TZ=prior;}
});

test('关键词可匹配较旧分页邮件，只读取摘要；未读条件使用Seen标志，200匹配明确limited',async()=>{
 const {client,adapter}=setup();client.rows.set('INBOX',Array.from({length:350},(_,i)=>row(i+1,i===0?'Target':'Other')));
 const result=await adapter.read({...defaultMailFilter,subject:'target',sender:'TEACHER'});assert.equal(result.messages.length,1);assert.ok(client.fetches.length>=4);assert.equal(client.downloads,0);
 client.rows.set('INBOX',[row(1,'Unread'),row(2,'Read','2026-10-30T00:00:00Z',true)]);assert.deepEqual((await adapter.read({...defaultMailFilter,unreadOnly:true})).messages.map(m=>m.subject),['Unread']);
 client.rows.set('INBOX',Array.from({length:220},(_,i)=>row(i+1)));const limited=await adapter.read(defaultMailFilter);assert.equal(limited.messages.length,200);assert.equal(limited.limited,true);
});

test('UIDVALIDITY与UID共同定位全文，正文优先纯文本，HTML不会执行或获取图片',async()=>{
 const {client,adapter}=setup();const result=await adapter.read(defaultMailFilter);const id=result.messages[0].id;assert.deepEqual(JSON.parse(id),['INBOX','42',1]);
 const full=await adapter.getMessage(id);assert.equal(full.body,'Read the book.');assert.equal(full.webUrl,'');assert.equal(full.bodyStatus,'complete');assert.equal(client.downloads,0);
 client.validity='43';await assert.rejects(()=>adapter.getMessage(id),/重置/);client.validity='42';
 client.source=Buffer.from('Content-Type: text/html; charset=utf-8\r\n\r\n<p>Hello &amp; goodbye</p><script>evil()</script><img src="https://private.test/a">');const html=await adapter.getMessage(id);assert.equal(html.body,'Hello & goodbye');assert.equal(client.downloads,0);assert.equal(html.hasHtml,true);
 await assert.rejects(()=>adapter.getMessage('["INBOX","42",0]'),/标识无效/);
});

test('报告大小与实际不同不拒收；真实传输中断仍拦截',async()=>{
 const {client,adapter}=setup();const id=(await adapter.read(defaultMailFilter)).messages[0].id;
 const raw=Buffer.from('From: teacher@school.test\r\nSubject: Big report\r\nContent-Type: text/plain; charset=utf-8\r\n\r\n'+'a'.repeat(258837-96));
 client.source=raw;client.rows.set('INBOX',[{...row(1,'Big report'),size:253287}]);
 const result=await adapter.getMessage(id);
 assert.equal(result.bodyStatus,'complete');assert.equal(result.reportedSize,253287);assert.equal(result.receivedBytes,raw.length);
 assert.ok(result.diagnostics?.some(value=>value.includes('253287')&&value.includes(String(raw.length))));
 assert.ok(result.body.startsWith('aaaa'));
 const fetch=client.fetchOne.bind(client);
 client.fetchOne=async(uid,query,options)=>{
  const value=await fetch(uid,query,options);
  return value&&query.source&&query.source.start>0?{...value,partialOrigins:new Map([['',0]])}:value;
 };
 await assert.rejects(()=>adapter.getMessage(id),/分段位置不一致/);
 client.fetchOne=fetch;
 client.failSource=true;await assert.rejects(()=>adapter.getMessage(id),/传输中断/);
});

test('大邮件只读取文字正文和附件元数据，不下载附件；正文超上限标记部分',async()=>{
 const {client,adapter}=setup();const id=(await adapter.read(defaultMailFilter)).messages[0].id;
 const structure={type:'multipart/mixed',childNodes:[{part:'1',type:'text/plain',size:16,encoding:'7bit'},{part:'2',type:'application/pdf',size:3*1024*1024,disposition:'attachment',dispositionParameters:{filename:'report.pdf'}},{part:'3',type:'image/png',size:2048,disposition:'inline',id:'<img>'}]};
 client.rows.set('INBOX',[{...row(1,'Large mail'),size:4*1024*1024,bodyStructure:structure}]);
 client.source=Buffer.from('Short readable body');
 const large=await adapter.getMessage(id);
 assert.equal(large.bodyStatus,'complete');assert.equal(large.body,'Short readable body');assert.equal(client.downloadPart,'1');
 assert.deepEqual(large.attachments?.map(a=>[a.name,a.inline]),[['report.pdf',false],['附件',true]]);
 client.download=async()=>({meta:{},content:Readable.from([Buffer.alloc(10*1024*1024+1,97)])});
 const partial=await adapter.getMessage(id);
 assert.equal(partial.bodyStatus,'partial');assert.ok(partial.body.length<=10*1024*1024);assert.ok(partial.bodyNote?.includes('10MB'));
});

test('服务器错误不回显凭据，超时主动关连接；账号变更的迟读取拒绝',async()=>{
 const client=new Client();client.connect=async()=>{throw Object.assign(new Error('server says secret-value user@example.test'),{authenticationFailed:true});};const {adapter}=setup(client);await assert.rejects(()=>adapter.testConnection(),error=>String(error).includes('登录失败')&&!String(error).includes('secret-value'));assert.equal(client.closed,1);
 const hanging=new Client();hanging.connect=()=>new Promise(()=>{});const timed=setup(hanging,{timeoutMs:10});await assert.rejects(()=>timed.adapter.testConnection(),/超时/);assert.ok(hanging.closed>=1);
 let reads=0;const stale=setup(new Client(),{creds:async()=>({...credentials,account:{...credentials.account,id:++reads===1?'one':'two'}})});await assert.rejects(()=>stale.adapter.read(defaultMailFilter),/连接已变化/);
});

test('目录循环、UID搜索资源上限和坏选择明确失败',async()=>{
 const {client,adapter}=setup();client.folders=[folder('one','two'),folder('two','one')];await assert.rejects(()=>adapter.listFolders(),/循环/);client.folders=[folder('INBOX')];
 await assert.rejects(()=>adapter.read({...defaultMailFilter,folderIds:['missing']}),/不存在/);
 client.search=async()=>Array.from({length:50001},(_,i)=>i+1);await assert.rejects(()=>adapter.read(defaultMailFilter),/50000/);
});
