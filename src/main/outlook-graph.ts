import { matchesMailFilter } from '../shared/mail';
import { z } from 'zod';
import { localDay, shiftDay } from '../shared/planner';
import { mailFilterSchema, type MailAccount, type MailAttachment, type MailFilter, type MailFolder, type MailMessage, type MailReadResult, type MailSummary } from '../shared/mail';

class OutlookGraphError extends Error {}
type Auth = { getToken(): Promise<{token:string;account:MailAccount}> };
type Session = {token:string;account:MailAccount;requests:number;cancelEpoch:number};
const ROOT='https://graph.microsoft.com/v1.0/me/';
const MAX_RESPONSE=5*1024*1024;
/** 单封正文单独放宽响应上限，但仍限制在 16MB；正文超过 10MB 时明确标记部分。 */
const MAX_MESSAGE_RESPONSE=32*1024*1024, BODY_READ_LIMIT=10*1024*1024;
const idSchema=z.string().min(1).max(2048).refine(value=>!/[\u0000-\u001f]/.test(value)&&value!=='.'&&value!=='..');
const folderSchema=z.object({id:idSchema,displayName:z.string().max(1000),childFolderCount:z.number().int().nonnegative(),unreadItemCount:z.number().int().nonnegative(),parentFolderId:z.string().optional()});
const messageSchema=z.object({id:idSchema,subject:z.string().max(2000).nullable().optional(),from:z.object({emailAddress:z.object({name:z.string().optional(),address:z.string().optional()})}).nullable().optional(),receivedDateTime:z.string().datetime({offset:true}),bodyPreview:z.string().max(20000).optional(),isRead:z.boolean(),webLink:z.string().optional()});
function safeUrl(value:string|undefined) { try { const u=new URL(value??''); return u.protocol==='https:'&&!u.username&&!u.password&&['outlook.office.com','outlook.office365.com','outlook.live.com'].includes(u.hostname)&&u.href.length<=8000 ? u.href : ''; }catch{return '';} }
function plain(value:string) { return value.replace(/<script\b[^>]*>[\s\S]*?<\/script>/gi,'').replace(/<style\b[^>]*>[\s\S]*?<\/style>/gi,'').replace(/<(?:br\b[^>]*|\/p|\/div|\/li)>/gi,'\n').replace(/<[^>]*>/g,'').replace(/&(#x[0-9a-f]+|#\d+|amp|lt|gt|quot|apos|nbsp);/gi,(_,key:string)=>{ const entities:Record<string,string>={amp:'&',lt:'<',gt:'>',quot:'"',apos:"'",nbsp:' '};if(key[0]!=='#')return entities[key.toLowerCase()]??'';const n=key[1].toLowerCase()==='x'?parseInt(key.slice(2),16):Number(key.slice(1));return n>0&&n<=0x10ffff?String.fromCodePoint(n):''; }).trim(); }
function summary(value:unknown):MailSummary { const m=messageSchema.parse(value);return {id:m.id,subject:m.subject||'（无主题）',sender:m.from?.emailAddress.address??m.from?.emailAddress.name??'',receivedAt:m.receivedDateTime,preview:plain(m.bodyPreview??''),unread:!m.isRead,webUrl:safeUrl(m.webLink),imported:false}; }
function segment(id:string) { return encodeURIComponent(idSchema.parse(id)); }
function graphUrl(value:string) { const url=new URL(value);if(url.origin!=='https://graph.microsoft.com'||url.username||url.password||!url.pathname.startsWith('/v1.0/me/')||url.hash)throw new OutlookGraphError('邮件分页地址无效，已停止读取。');return url.href; }
function boundaries(filter:MailFilter,now:Date) { const today=localDay(now); const start=filter.dateMode==='recent'?shiftDay(today,1-filter.recentDays):filter.since;const end=filter.dateMode==='range'?shiftDay(filter.until,1):filter.dateMode==='recent'?shiftDay(today,1):null;return {start:new Date(start+'T00:00:00').toISOString(),end:end?new Date(end+'T00:00:00').toISOString():null}; }
export class OutlookGraph {
 private pending=new Set<AbortController>();private cancelEpoch=0;
 cancelPending(){this.cancelEpoch++;for(const controller of this.pending)controller.abort();}
 constructor(private auth:Auth,private request:typeof fetch=fetch,private clock:()=>Date=()=>new Date()) {}
 private async session():Promise<Session> {const generation=this.cancelEpoch;const value=await this.auth.getToken();if(generation!==this.cancelEpoch)throw new OutlookGraphError('邮件读取已暂停。');return {...value,requests:0,cancelEpoch:generation};}
 private async confirm(session:Session) { if((await this.auth.getToken()).account.id!==session.account.id)throw new OutlookGraphError('邮箱账户已变化，请重新读取。'); }
 private async json(url:string,session:Session,maxResponse=MAX_RESPONSE):Promise<unknown> {
  if(session.cancelEpoch!==this.cancelEpoch)throw new OutlookGraphError('邮件读取已暂停。');
  const target=graphUrl(url);if(++session.requests>500)throw new OutlookGraphError('邮件读取请求过多，请缩小文件夹或日期范围。');
  const controller=new AbortController(),timer=setTimeout(()=>controller.abort(),20000);
  this.pending.add(controller);
  const aborted=new Promise<never>((_,reject)=>controller.signal.addEventListener('abort',()=>reject(new OutlookGraphError('邮件读取已暂停或超时，请重试。')),{once:true}));
  try {
   const response=await Promise.race([this.request(target,{method:'GET',redirect:'error',signal:controller.signal,headers:{Authorization:'Bearer '+session.token,Accept:'application/json',Prefer:'IdType="ImmutableId", outlook.body-content-type="text"'}}),aborted]);
   if(response.status===401)throw new OutlookGraphError('邮箱授权已失效，请重新登录。');
   if(response.status===403)throw new OutlookGraphError('学校或邮箱未允许读取，请检查授权或联系管理员。');
   if(response.status===429)throw new OutlookGraphError('邮箱请求过于频繁，请稍后重试。');
   if(!response.ok)throw new OutlookGraphError('邮箱服务暂时不可用，请稍后重试。');
   if(Number(response.headers.get('content-length'))>maxResponse)throw new OutlookGraphError('邮件响应过大，请缩小读取范围。');
   const reader=response.body?.getReader();if(!reader)throw new OutlookGraphError('邮箱返回空响应。');
   const chunks:Uint8Array[]=[];let bytes=0;
   try {while(true){const entry=await Promise.race([reader.read(),aborted]);if(entry.done)break;bytes+=entry.value.byteLength;if(bytes>maxResponse)throw new OutlookGraphError('邮件响应过大，请缩小读取范围。');chunks.push(entry.value);}}
   finally{void reader.cancel().catch(()=>{});reader.releaseLock();}
   try{return JSON.parse(Buffer.concat(chunks).toString('utf8'));}catch{throw new OutlookGraphError('邮箱响应格式无效，请重试。');}
  }catch(error){if(error instanceof OutlookGraphError)throw error;throw new OutlookGraphError(controller.signal.aborted?'邮箱读取超时，请重试。':'邮箱网络连接失败，请重试。');}
  finally{clearTimeout(timer);this.pending.delete(controller);}
 }
 private async *pages(url:string,session:Session):AsyncGenerator<unknown[]> {
  const seen=new Set<string>();let next:string|undefined=url;
  while(next){const current=graphUrl(next);if(seen.has(current)||seen.size>=100)throw new OutlookGraphError('邮件分页过多或循环，请缩小读取范围。');seen.add(current);
   const raw=await this.json(current,session);const parsed=z.object({value:z.array(z.unknown()).max(1000),'@odata.nextLink':z.string().optional()}).safeParse(raw);
   if(!parsed.success)throw new OutlookGraphError('邮箱列表格式无效，请重试。');
   next=parsed.data['@odata.nextLink'];if(next)graphUrl(next);yield parsed.data.value;
  }
 }
 private async folders(parent:string|undefined,session:Session):Promise<MailFolder[]> {
  const url=new URL(parent?ROOT+'mailFolders/'+segment(parent)+'/childFolders':ROOT+'mailFolders');url.searchParams.set('$select','id,displayName,childFolderCount,unreadItemCount,parentFolderId');url.searchParams.set('$top','100');
  const result:MailFolder[]=[];const seen=new Set<string>();
  for await(const page of this.pages(url.href,session))for(const value of page){const parsed=folderSchema.safeParse(value);if(!parsed.success)throw new OutlookGraphError('邮箱文件夹格式无效。');const f=parsed.data;if(seen.has(f.id))throw new OutlookGraphError('邮箱文件夹存在重复，请重新读取。');seen.add(f.id);result.push({id:f.id,name:f.displayName,childCount:f.childFolderCount,unreadCount:f.unreadItemCount,parentId:parent??null});if(result.length>500)throw new OutlookGraphError('邮箱文件夹超过500个，请缩小范围。');}
  return result;
 }
 async listFolders(parentId?:string):Promise<MailFolder[]> {if(parentId!==undefined)segment(parentId);const session=await this.session();const result=await this.folders(parentId,session);await this.confirm(session);return result;}
 async read(value:MailFilter):Promise<MailReadResult> {
  const parsed=mailFilterSchema.safeParse(value);if(!parsed.success)throw new OutlookGraphError('邮件筛选条件无效，请检查日期和文件夹。');const filter=parsed.data,session=await this.session(),dates=boundaries(filter,this.clock());
  const edges=new Map<string,string[]>();
  const queue=[...(filter.folderIds.length?filter.folderIds:['inbox'])];queue.forEach(segment);const visited=new Set<string>();const folders:string[]=[];
  while(queue.length){const id=queue.shift()!;if(visited.has(id))continue;visited.add(id);folders.push(id);if(visited.size>500)throw new OutlookGraphError('邮箱文件夹超过500个，请缩小范围。');if(filter.includeChildren){const children=await this.folders(id,session);edges.set(id,children.map(folder=>folder.id));for(const folder of children){if(folder.id===id)throw new OutlookGraphError('邮箱文件夹存在循环。');if(!visited.has(folder.id))queue.push(folder.id);}}}
  const completed=new Set<string>();
  for(const root of folders){const stack:Array<{id:string;exit:boolean}>=[{id:root,exit:false}],active=new Set<string>();while(stack.length){const entry=stack.pop()!;if(entry.exit){active.delete(entry.id);completed.add(entry.id);continue;}if(active.has(entry.id))throw new OutlookGraphError('邮箱文件夹存在循环。');if(completed.has(entry.id))continue;active.add(entry.id);stack.push({id:entry.id,exit:true});for(const child of edges.get(entry.id)??[])stack.push({id:child,exit:false});}}
  const result=new Map<string,MailSummary>();let limited=false;
  outer:for(const folder of folders){const url=new URL(ROOT+'mailFolders/'+segment(folder)+'/messages');url.searchParams.set('$select','id,subject,from,receivedDateTime,bodyPreview,isRead,webLink');url.searchParams.set('$top','100');url.searchParams.set('$orderby','receivedDateTime desc');url.searchParams.set('$filter','receivedDateTime ge '+dates.start+(dates.end?' and receivedDateTime lt '+dates.end:''));
   for await(const page of this.pages(url.href,session))for(const raw of page){let m:MailSummary;try{m=summary(raw);}catch{throw new OutlookGraphError('邮件摘要格式无效，请重试。');}
    const at=Date.parse(m.receivedAt);if(at<Date.parse(dates.start)||dates.end&&at>=Date.parse(dates.end))continue;
    if(!matchesMailFilter(m,filter))continue;
    result.set(m.id,m);if(result.size>=200){limited=true;break outer;}
   }
  }
  await this.confirm(session);return {messages:[...result.values()].sort((a,b)=>Date.parse(b.receivedAt)-Date.parse(a.receivedAt)),limited};
 }
 async getMessage(id:string,options?:{maxBytes?:number}):Promise<MailMessage> {
  const encoded=segment(id),session=await this.session();const url=new URL(ROOT+'messages/'+encoded);
  url.searchParams.set('$select','id,subject,from,receivedDateTime,bodyPreview,isRead,webLink,body,hasAttachments');
  url.searchParams.set('$expand','attachments($select=name,contentType,size,isInline)');
  let raw:unknown;
  try{raw=await this.json(url.href,session,MAX_MESSAGE_RESPONSE);}
  catch(error){
   // 网络/超时重试一次；授权、暂停和频率限制不重试，避免放大问题。
   if(error instanceof OutlookGraphError&&/授权|未允许|暂停|频繁|账户|正文/.test(error.message))throw error;
   raw=await this.json(url.href,session,MAX_MESSAGE_RESPONSE);
  }
  try {
   const limit=Math.min(Math.max(options?.maxBytes??BODY_READ_LIMIT,BODY_READ_LIMIT),25*1024*1024);
   const base=summary(raw);if(base.id!==id)throw new OutlookGraphError('邮件正文标识不匹配，请重新读取。');
   const content=z.object({
    body:z.object({contentType:z.enum(['text','html']),content:z.string()}).optional(),
    attachments:z.array(z.object({name:z.string().max(1000).optional(),contentType:z.string().max(300).optional(),size:z.number().int().nonnegative().optional(),isInline:z.boolean().optional()})).max(200).optional(),
   }).parse(raw);
   const attachments:MailAttachment[]=(content.attachments??[]).map(item=>({name:(item.name||'附件').replace(/[\u0000-\u001f]/g,'').slice(0,300),type:item.contentType||'application/octet-stream',size:item.size??0,inline:!!item.isInline}));
   let body='',bodyStatus:'complete'|'partial'|'unavailable'='complete',bodyNote='',receivedBytes=0;
   if(content.body&&typeof content.body.content==='string'){
    receivedBytes=Buffer.byteLength(content.body.content,'utf8');
    const html=content.body.contentType==='html';
    body=html?plain(content.body.content):content.body.content;
    if(receivedBytes>limit){
     body=Buffer.from(body,'utf8').subarray(0,limit).toString('utf8');
     bodyStatus='partial';bodyNote='正文超过单次读取上限（'+Math.round(limit/1024/1024)+'MB），已保存前'+Math.round(limit/1024/1024)+'MB；可继续读取或打开原邮箱查看完整内容。';
    }else if(html)bodyNote='邮件只有 HTML 正文，已转换为纯文本；不执行脚本，也不自动加载外部图片。';
   }else{
    body=base.preview;bodyStatus=body?'partial':'unavailable';
    bodyNote=body?'服务器只返回了正文预览，完整正文可在原邮箱查看。':'服务器未返回正文；摘要与附件信息已保留，可在原邮箱查看。';
   }
   const diagnostics=[`Graph 返回正文 ${receivedBytes} 字节；附件只保存名称、类型和大小，不下载。`];
   await this.confirm(session);
   return {...base,body,bodyStatus,bodyBytes:Buffer.byteLength(body,'utf8'),receivedBytes,reportedSize:null,attachments,bodyNote:bodyNote||undefined,hasHtml:content.body?.contentType==='html',diagnostics};
  }catch(error){if(error instanceof OutlookGraphError)throw error;throw new OutlookGraphError('邮件正文格式无效，请重试。');}
 }
}
