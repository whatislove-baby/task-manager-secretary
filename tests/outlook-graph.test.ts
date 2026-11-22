import { test } from 'node:test';
import assert from 'node:assert/strict';
import { OutlookGraph } from '../src/main/outlook-graph';
import { defaultMailFilter } from '../src/shared/mail';
const root='https://graph.microsoft.com/v1.0/me/';
const account={id:'account-one',email:'student@example.edu',name:'Student'};
const auth={getToken:async()=>({token:'private-token',account})};
const ok=(value:unknown)=>new Response(JSON.stringify(value),{headers:{'content-type':'application/json'}});
const message=(id:string,extra:Record<string,unknown>={})=>({id,subject:'Assignment '+id,from:{emailAddress:{address:'professor@example.edu'}},receivedDateTime:'2026-10-30T01:00:00Z',bodyPreview:'Please finish the project',isRead:false,webLink:'https://outlook.office.com/mail/id/'+id,...extra});
const folder=(id:string,count=0)=>({id,displayName:id,childFolderCount:count,unreadItemCount:2});
const filter={...defaultMailFilter,dateMode:'since' as const,since:'2026-10-01'};
const now=()=>new Date('2026-10-30T12:00:00Z');

test('根文件夹和子文件夹分页完整读取，不读取邮件正文，使用只读请求头',async()=>{
 const seen:string[]=[];
 const graph=new OutlookGraph(auth,(async(input,options)=>{
  const url=new URL(String(input));seen.push(url.href);assert.equal(options?.method,'GET');assert.equal(options?.redirect,'error');assert.ok(String((options?.headers as Record<string,string>).Prefer).includes('ImmutableId'));
  if(url.searchParams.get('$skiptoken')==='next')return ok({value:[folder('courses',2)]});
  if(url.pathname.endsWith('/childFolders'))return ok({value:[folder('math')]});
  return ok({value:[folder('inbox')],'@odata.nextLink':root+'mailFolders?$skiptoken=next'});
 }) as typeof fetch,now);
 assert.deepEqual((await graph.listFolders()).map(f=>f.id),['inbox','courses']);
 assert.equal((await graph.listFolders('courses'))[0].parentId,'courses');assert.equal(seen.length,3);
});

test('多选和子文件夹取并集去重，关键词在后续页面也匹配，仅返回摘要',async()=>{
 const folders:string[]=[];const graph=new OutlookGraph(auth,(async input=>{
  const url=new URL(String(input));
  if(url.pathname.endsWith('/childFolders'))return ok({value:url.pathname.includes('/a/')?[folder('b')]:[]});
  assert.ok(!url.searchParams.get('$select')?.split(',').includes('body'));
  const id=url.pathname.includes('/a/')?'a':'b';folders.push(id);
  if(id==='a'&&!url.searchParams.has('$skiptoken'))return ok({value:[message('no-match',{subject:'Newsletter'})],'@odata.nextLink':root+'mailFolders/a/messages?$skiptoken=2'});
  return ok({value:[message('duplicate'),message(id)]});
 }) as typeof fetch,now);
 const result=await graph.read({...filter,folderIds:['a','b'],includeChildren:true,subject:'assignment',sender:'PROFESSOR',unreadOnly:true});
 assert.deepEqual(result.messages.map(m=>m.id).sort(),['a','b','duplicate']);assert.equal(result.limited,false);assert.equal(folders.filter(id=>id==='b').length,1);assert.equal(result.messages[0].imported,false);
});

test('筛选日期按本机日历日，日期范围含结束整日，最近天数动态计算',async()=>{
 const previous=process.env.TZ;process.env.TZ='Asia/Shanghai';
 try{
  const queries:string[]=[];const graph=new OutlookGraph(auth,(async input=>{const url=new URL(String(input));queries.push(url.searchParams.get('$filter')!);return ok({value:[message('before',{receivedDateTime:'2026-10-29T15:59:00Z'}),message('midnight',{receivedDateTime:'2026-10-29T16:00:00Z'}),message('last',{receivedDateTime:'2026-10-30T15:59:00Z'}),message('after',{receivedDateTime:'2026-10-30T16:00:00Z'})]});}) as typeof fetch,now);
  const result=await graph.read({...filter,dateMode:'range',since:'2026-10-30',until:'2026-10-30'});
  assert.deepEqual(result.messages.map(m=>m.id),['last','midnight']);assert.ok(queries[0].includes('ge 2026-10-29T16:00:00.000Z'));assert.ok(queries[0].includes('lt 2026-10-30T16:00:00.000Z'));
  await graph.read({...defaultMailFilter,recentDays:2});assert.ok(queries[1].includes('ge 2026-10-28T16:00:00.000Z'));
 }finally{if(previous===undefined)delete process.env.TZ;else process.env.TZ=previous;}
});

test('200封上限明确limited，空文件夹选择默认收件箱',async()=>{
 let path='';const graph=new OutlookGraph(auth,(async input=>{path=new URL(String(input)).pathname;return ok({value:Array.from({length:201},(_,i)=>message(String(i)))});}) as typeof fetch,now);
 const result=await graph.read(filter);assert.equal(result.messages.length,200);assert.equal(result.limited,true);assert.ok(path.includes('/inbox/'));
});

test('外站分页、用户凭据、循环分页和文件夹循环拒绝，不向外站发送token',async()=>{
 for(const link of ['https://evil.example/steal','https://x@graph.microsoft.com/v1.0/me/mailFolders','https://graph.microsoft.com/v1.0/users/other/messages']){
  let calls=0;const graph=new OutlookGraph(auth,(async()=>{calls++;return ok({value:[],'@odata.nextLink':link});}) as typeof fetch);
  await assert.rejects(()=>graph.listFolders(),/分页地址/);assert.equal(calls,1);
 }
 let first='';const loop=new OutlookGraph(auth,(async input=>{first||=String(input);return ok({value:[],'@odata.nextLink':first});}) as typeof fetch);await assert.rejects(()=>loop.listFolders(),/分页过多或循环/);
 const cycle=new OutlookGraph(auth,(async input=>{const url=String(input);return ok({value:[folder(url.includes('/a/')?'b':'a',1)]});}) as typeof fetch);
 await assert.rejects(()=>cycle.read({...filter,folderIds:['a'],includeChildren:true}),/循环/);
});

test('全文HTML转换纯文本，原文只允许Outlook链接，危险ID不可逃逸路径',async()=>{
 let requested='';const graph=new OutlookGraph(auth,(async(input,options)=>{requested=String(input);assert.ok((options?.headers as Record<string,string>).Prefer.includes('body-content-type="text"'));return ok({...message('a/b?query'),webLink:'javascript:bad()',body:{contentType:'html',content:'<p>First &amp; next</p><script>bad()</script><p>End</p>'}});}) as typeof fetch);
 const result=await graph.getMessage('a/b?query');assert.ok(requested.includes('a%2Fb%3Fquery'));assert.equal(result.webUrl,'');assert.equal(result.body,'First & next\nEnd');
 await assert.rejects(()=>graph.getMessage('..'));await assert.rejects(()=>graph.listFolders('.'));
});

test('网络错误与401/403/429使用固定信息，响应和正文资源超限拒绝',async()=>{
 for(const [status,pattern] of [[401,/重新登录/],[403,/学校/],[429,/频繁/]] as const){const graph=new OutlookGraph(auth,(async()=>new Response('private-token',{status})) as typeof fetch);await assert.rejects(()=>graph.listFolders(),pattern);}
 const network=new OutlookGraph(auth,(async()=>{throw new Error('邮箱 private-token body');}) as typeof fetch);await assert.rejects(()=>network.listFolders(),error=>!String(error).includes('private-token'));
 const oversized=new OutlookGraph(auth,(async()=>new Response('{}',{headers:{'content-length':String(6*1024*1024)}})) as typeof fetch);await assert.rejects(()=>oversized.listFolders(),/响应过大/);
 const body=new OutlookGraph(auth,(async()=>ok({...message('a'),body:{contentType:'text',content:'x'.repeat(2*1024*1024+1)}})) as typeof fetch);const accepted=await body.getMessage('a');assert.equal(accepted.bodyStatus,'complete');assert.equal(accepted.body.length,2*1024*1024+1);
 const huge=new OutlookGraph(auth,(async()=>ok({...message('a'),body:{contentType:'text',content:'x'.repeat(10*1024*1024+1)},attachments:[{name:'big.pdf',contentType:'application/pdf',size:12345,isInline:false}]})) as typeof fetch);const partial=await huge.getMessage('a');assert.equal(partial.bodyStatus,'partial');assert.equal(partial.body.length,10*1024*1024);assert.deepEqual(partial.attachments?.map(a=>[a.name,a.size]),[['big.pdf',12345]]);
 const oversizedMessage=new OutlookGraph(auth,(async()=>new Response('{}',{headers:{'content-length':String(33*1024*1024)}})) as typeof fetch);await assert.rejects(()=>oversizedMessage.getMessage('a'),/响应过大/);
});

test('账户中途切换不返回旧账户邮件，畸形响应和无效筛选不静默成功',async()=>{
 let current=account;const switching={getToken:async()=>({token:'token',account:current})};
 const graph=new OutlookGraph(switching,(async()=>{current={...account,id:'another'};return ok({value:[message('a')]});}) as typeof fetch,now);
 await assert.rejects(()=>graph.read(filter),/账户已变化/);
 const malformed=new OutlookGraph(auth,(async()=>ok({unexpected:[]})) as typeof fetch);await assert.rejects(()=>malformed.listFolders(),/格式无效/);
 await assert.rejects(()=>malformed.read({...filter,since:'2026-02-30'}),/筛选条件无效/);
});
