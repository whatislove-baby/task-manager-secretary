import { matchesMailFilter } from '../shared/mail';
import { ImapFlow, type ImapFlowOptions, type FetchMessageObject, type ListResponse, type MessageStructureObject } from 'imapflow';
import { simpleParser, type ParsedMail } from 'mailparser';
import { createHash } from 'node:crypto';
import { z } from 'zod';
import { localDay, shiftDay } from '../shared/planner';
import { imapConfigSchema, mailFilterSchema, type ImapConfigInput, type MailAccount, type MailAttachment, type MailFilter, type MailFolder, type MailMessage, type MailReadResult, type MailSummary } from '../shared/mail';
import { MailReadError } from './mail-errors';

export type ImapCredentials={config:ImapConfigInput;account:MailAccount};
export type ImapClient=Pick<ImapFlow,'connect'|'logout'|'close'|'on'|'list'|'getMailboxLock'|'search'|'fetch'|'fetchOne'|'download'|'mailbox'>;
export type ImapFactory=(options:ImapFlowOptions)=>ImapClient;
class MailboxError extends Error {}
const pathSchema=z.string().min(1).max(1500).refine(value=>!/[\u0000-\u001f\u007f]/.test(value),'文件夹标识无效');
const uidSchema=z.number().int().min(1).max(0xffffffff);
const validitySchema=z.string().regex(/^\d{1,10}$/).refine(value=>BigInt(value)>0n&&BigInt(value)<=0xffffffffn);
const identitySchema=z.tuple([pathSchema,validitySchema,uidSchema]);
/** 报告大小只作诊断参考，不再作为拒收依据。 */
const LARGE_RAW_BYTES=2*1024*1024;
const SEGMENT_BYTES=256*1024;
const BODY_READ_LIMIT=10*1024*1024;
const query={uid:true,envelope:true,internalDate:true,flags:true,size:true} as const;
function key(path:string,validity:string,uid:number){const value=JSON.stringify(identitySchema.parse([path,validity,uid]));if(value.length>2048)throw new MailboxError('文件夹路径过长，无法安全标识邮件。');return value;}
function validMailbox(client:ImapClient){if(!client.mailbox)throw new MailboxError('无法打开邮件文件夹。');return validitySchema.parse(String(client.mailbox.uidValidity));}
function plain(value:string){return value.replace(/<script\b[^>]*>[\s\S]*?<\/script>/gi,'').replace(/<style\b[^>]*>[\s\S]*?<\/style>/gi,'').replace(/<(?:br\b[^>]*|\/p|\/div|\/li)>/gi,'\n').replace(/<[^>]*>/g,'').replace(/&(#x[0-9a-f]+|#\d+|amp|lt|gt|quot|apos|nbsp);/gi,(_,entity:string)=>{const map:Record<string,string>={amp:'&',lt:'<',gt:'>',quot:'"',apos:"'",nbsp:' '};if(entity[0]!=='#')return map[entity.toLowerCase()]??'';const n=entity[1].toLowerCase()==='x'?parseInt(entity.slice(2),16):Number(entity.slice(1));return n>0&&n<=0x10ffff?String.fromCodePoint(n):'';}).trim();}
function summary(row:FetchMessageObject,path:string,validity:string):MailSummary{
 const at=new Date(row.internalDate??'');if(!Number.isFinite(at.getTime())||!row.envelope)throw new MailboxError('邮件摘要或收件时间无效，请重试。');
 const subject=row.envelope.subject||'（无主题）';const sender=(row.envelope.from??[]).map(a=>a.name&&a.address?`${a.name} <${a.address}>`:a.address||a.name||'').join(', ');
 if(subject.length>10000||sender.length>10000)throw new MailboxError('邮件摘要过长，无法读取。');
 return {id:key(path,validity,row.uid),subject,sender,receivedAt:at.toISOString(),preview:'',unread:!row.flags?.has('\Seen'),webUrl:'',imported:false};
}
type TextPart={key:string;type:'text/plain'|'text/html';size:number|null};
interface StructureInfo {text:TextPart|null;html:TextPart|null;attachments:MailAttachment[]}
function nodeName(node:MessageStructureObject):string {
 const raw=node.dispositionParameters?.filename||node.parameters?.name||'';
 return raw.replace(/[\u0000-\u001f]/g,'').slice(0,300)||'附件';
}
/** 只读取结构元数据：文字正文、HTML 和附件名称/类型/大小；不下载附件或内嵌图片。 */
function structureInfo(root?:MessageStructureObject):StructureInfo {
 const info:StructureInfo={text:null,html:null,attachments:[]};
 if(!root)return info;
 const stack:[MessageStructureObject,string|undefined][]=[[root,root.part]];
 const seen=new Set<MessageStructureObject>();
 while(stack.length){
  const [node,fallbackKey]=stack.pop()!;
  if(seen.has(node)||seen.size>200)continue;seen.add(node);
  const type=String(node.type||'').toLowerCase();
  if(type.startsWith('multipart/')){
   const children=node.childNodes??[];
   for(let i=children.length-1;i>=0;i--)stack.push([children[i]!,children[i]!.part??String(i+1)]);
   continue;
  }
  const key=node.part??fallbackKey??'1';
  const disposition=String(node.disposition||'').toLowerCase();
  const filename=node.dispositionParameters?.filename||node.parameters?.name;
  if(type==='text/plain'&&disposition!=='attachment'&&!filename){
   if(!info.text)info.text={key,type:'text/plain',size:node.size??null};
   continue;
  }
  if(type==='text/html'&&disposition!=='attachment'&&!filename){
   if(!info.html)info.html={key,type:'text/html',size:node.size??null};
   continue;
  }
  const inline=disposition==='inline'&&!!node.id;
  if(disposition==='attachment'||filename||inline||type.startsWith('image/')||type.startsWith('application/')||type.startsWith('audio/')||type.startsWith('video/')){
   info.attachments.push({name:nodeName(node),type:type||'application/octet-stream',size:Number.isFinite(node.size)?Math.max(0,Number(node.size)):0,inline});
  }
 }
 return info;
}
interface SourceRead {buffer:Buffer;bytes:number;ended:boolean}
/**
 * 按实际接收位置分段读取原始邮件。每段完成后从 offset 继续，并在短段后再确认没有后续内容；
 * 不使用服务器报告大小提前截断。达到单次上限时 ended=false，明确标记为未完整。
 */
async function readSource(client:ImapClient,uid:number,limit=BODY_READ_LIMIT):Promise<SourceRead>{
 const chunks:Buffer[]=[];let offset=0;
 const fetchSegment=async(start:number,take:number)=>{
  const row=await client.fetchOne(String(uid),{uid:true,source:{start,maxLength:take}},{uid:true});
  if(!row||row.uid!==uid)throw new MailReadError('这封邮件在读取期间已不存在，请刷新列表。','identity','MAIL_GONE');
  const origin=(row as FetchMessageObject & {partialOrigins?:ReadonlyMap<string,number>}).partialOrigins?.get('');
  if(origin!==undefined&&origin!==start)throw new MailReadError('服务器返回的分段位置不一致，未保存正文。','transfer','SEGMENT_MISMATCH');
  if(!Buffer.isBuffer(row.source))throw new MailReadError('服务器没有返回请求的邮件分段，未保存正文。','transfer','MISSING_SEGMENT');
  if(row.source.length>take)throw new MailReadError('服务器返回了超出请求范围的正文，已停止读取。','transfer','SEGMENT_MISMATCH');
  return row.source;
 };
 while(offset<limit){
  const take=Math.min(SEGMENT_BYTES,limit-offset),buffer=await fetchSegment(offset,take);
  if(!buffer.length){
   if(!offset)throw new MailReadError('服务器未返回邮件内容，未保存正文。','transfer','EMPTY_SOURCE');
   return {buffer:Buffer.concat(chunks),bytes:offset,ended:true};
  }
  chunks.push(buffer);offset+=buffer.length;
  if(buffer.length<take&&!(await fetchSegment(offset,1)).length)return {buffer:Buffer.concat(chunks),bytes:offset,ended:true};
 }
 return {buffer:Buffer.concat(chunks),bytes:offset,ended:false};
}
interface PartRead {text:string;bytes:number;complete:boolean}
async function readTextPart(client:ImapClient,uid:number,part:TextPart,limit=BODY_READ_LIMIT):Promise<PartRead|null>{
 const downloaded=await client.download(String(uid),part.key,{uid:true,maxBytes:limit+1});
 if(!downloaded.content)return null;
 const chunks:Buffer[]=[];let bytes=0,over=false;
 try{
  for await(const value of downloaded.content){
   const buffer=Buffer.isBuffer(value)?value:Buffer.from(value);
   bytes+=buffer.length;
   if(bytes>limit){over=true;chunks.push(buffer.subarray(0,Math.max(0,limit-(bytes-buffer.length))));break;}
   chunks.push(buffer);
  }
 }finally{downloaded.content.destroy();}
 const text=Buffer.concat(chunks).toString('utf8').slice(0,limit);
 return {text,bytes:Math.min(bytes,limit),complete:!over};
}
function parsedBody(parsed:ParsedMail):{body:string;hasHtml:boolean}{
 const html=typeof parsed.html==='string'?parsed.html:'';
 const body=(parsed.text??'').trim()||(html?plain(html):'');
 return {body,hasHtml:!!html};
}
interface FolderNode extends MailFolder {selectable:boolean}
function folderNodes(rows:ListResponse[]):Map<string,FolderNode>{
 if(rows.length>500)throw new MailboxError('邮箱文件夹超过500个，请缩小范围。');const nodes=new Map<string,FolderNode>(),delimiters=new Map<string,string>();
 for(const row of rows){const path=pathSchema.parse(row.path);const delimiter=row.delimiter||'';delimiters.set(path,delimiter);let parent=row.parentPath||null;
  if(!parent&&delimiter&&path.includes(delimiter))parent=path.slice(0,path.lastIndexOf(delimiter))||null;
  if(parent===path)throw new MailboxError('邮箱文件夹层级存在循环。');
  if(nodes.has(path))throw new MailboxError('邮箱返回重复文件夹。');
  nodes.set(path,{id:path,name:row.name||path,parentId:parent,childCount:0,unreadCount:row.status?.unseen??0,selectable:!row.flags?.has('\Noselect')&&!row.flags?.has('\NonExistent')});
 }
 // A server may list a child without its non-selectable parent.
 for(const node of [...nodes.values()]){
  const delimiter=delimiters.get(node.id)||'';let path=node.parentId;
  while(path&&!nodes.has(path)){pathSchema.parse(path);const boundary=delimiter?path.lastIndexOf(delimiter):-1;const parent=boundary>0?path.slice(0,boundary):null;
   nodes.set(path,{id:path,name:boundary>=0?path.slice(boundary+delimiter.length):path,parentId:parent,childCount:0,unreadCount:0,selectable:false});path=parent;
   if(nodes.size>500)throw new MailboxError('邮箱文件夹超过500个，请缩小范围。');
  }
 }
 if(nodes.size>500)throw new MailboxError('邮箱文件夹超过500个，请缩小范围。');
 for(const node of nodes.values()){if(node.parentId)nodes.get(node.parentId)!.childCount++;const seen=new Set<string>();let cursor:FolderNode|undefined=node;while(cursor){if(seen.has(cursor.id))throw new MailboxError('邮箱文件夹层级存在循环。');seen.add(cursor.id);cursor=cursor.parentId?nodes.get(cursor.parentId):undefined;}}
 return nodes;
}
function dates(filter:MailFilter,now:Date){const today=localDay(now),start=filter.dateMode==='recent'?shiftDay(today,1-filter.recentDays):filter.since,end=filter.dateMode==='range'?shiftDay(filter.until,1):filter.dateMode==='recent'?shiftDay(today,1):null;return {start:new Date(start+'T00:00:00').getTime(),end:end?new Date(end+'T00:00:00').getTime():null,searchStart:new Date(shiftDay(start,-1)+'T00:00:00Z'),searchEnd:end?new Date(shiftDay(end,1)+'T00:00:00Z'):undefined};}

export class ImapMailbox {
 private pending=new Set<()=>void>();private cancelEpoch=0;
 cancelPending(){this.cancelEpoch++;for(const stop of [...this.pending])stop();}
 constructor(private getCredentials:()=>Promise<ImapCredentials>,private factory:ImapFactory=options=>new ImapFlow(options),private clock:()=>Date=()=>new Date(),private timeoutMs=60000){}
 private async job<T>(action:(client:ImapClient)=>Promise<T>):Promise<T>{
  const generation=this.cancelEpoch;let cancel:(()=>void)|undefined;
  let client:ImapClient|undefined,timer:ReturnType<typeof setTimeout>|undefined;
  try{
   const before=await this.getCredentials(),config=imapConfigSchema.parse(before.config);if(!config.password)throw new MailboxError('请先填写IMAP授权码或密码。');
   if(generation!==this.cancelEpoch)throw new MailboxError('邮件读取已暂停。');
   const identity=JSON.stringify([before.account.id,config]);
   client=this.factory({host:config.host,port:config.port,secure:config.security==='tls',doSTARTTLS:config.security==='starttls',tls:{rejectUnauthorized:true,minVersion:'TLSv1.2'},auth:{user:config.username||config.email,pass:config.password},logger:false,logRaw:false,emitLogs:false,disableAutoIdle:true,disableCompression:true,clientInfo:{name:'Task Manager',version:'0.1.0'},connectionTimeout:15000,greetingTimeout:15000,socketTimeout:20000});
   client.on('error',()=>{});const active=client;
   const canceled=new Promise<never>((_,reject)=>{cancel=()=>{active.close();reject(new MailboxError('邮件读取已暂停。'));};this.pending.add(cancel);});
   const work=(async()=>{await active.connect();if(generation!==this.cancelEpoch)throw new MailboxError('邮件读取已暂停。');const value=await action(active);const after=await this.getCredentials();if(JSON.stringify([after.account.id,imapConfigSchema.parse(after.config)])!==identity)throw new MailboxError('邮箱连接已变化，请重新读取。');return value;})();
   const timeout=new Promise<never>((_,reject)=>{timer=setTimeout(()=>{active.close();reject(new MailboxError('IMAP操作超时，请重试或缩小范围。'));},this.timeoutMs);});
   return await Promise.race([work,timeout,canceled]);
  }catch(error){if(error instanceof MailboxError||error instanceof MailReadError)throw error;if((error as {authenticationFailed?:boolean})?.authenticationFailed)throw new MailboxError('IMAP登录失败，请检查登录用户名、授权码及邮箱是否已开启IMAP服务。');throw new MailboxError('无法读取IMAP邮箱，请检查服务器、端口、加密方式和授权码。');}
  finally{if(cancel)this.pending.delete(cancel);if(timer)clearTimeout(timer);if(client){const current=client;let cleanup:ReturnType<typeof setTimeout>|undefined;try{await Promise.race([current.logout().catch(()=>{}),new Promise<void>(resolve=>{cleanup=setTimeout(resolve,500);})]);}catch{}finally{if(cleanup)clearTimeout(cleanup);current.close();}}}
 }
 testConnection():Promise<void>{return this.job(async()=>{});}
 async listFolders(parentId?:string):Promise<MailFolder[]>{if(parentId!==undefined)pathSchema.parse(parentId);return this.job(async client=>{
  const nodes=folderNodes(await client.list({statusQuery:{unseen:true}}));if(parentId!==undefined&&!nodes.has(parentId))throw new MailboxError('文件夹已不存在，请刷新文件夹列表。');
  return [...nodes.values()].filter(node=>node.parentId===(parentId??null)).map(({selectable,...node})=>node);
 });}
 async read(value:MailFilter):Promise<MailReadResult>{const filter=mailFilterSchema.parse(value),range=dates(filter,this.clock());return this.job(async client=>{
  const nodes=folderNodes(await client.list({statusQuery:{unseen:true}}));const roots=filter.folderIds.length?filter.folderIds:[...nodes.keys()].find(path=>path.toUpperCase()==='INBOX')?[...nodes.keys()].filter(path=>path.toUpperCase()==='INBOX'):['INBOX'];
  const paths=new Set<string>();for(const root of roots){if(!nodes.has(root))throw new MailboxError('所选文件夹已不存在，请刷新后重新选择。');const queue=[root];while(queue.length){const path=queue.shift()!;if(paths.has(path))continue;paths.add(path);if(filter.includeChildren)for(const node of nodes.values())if(node.parentId===path)queue.push(node.id);}}
  const messages=new Map<string,MailSummary>();let scanned=0,limited=false;
  outer:for(const path of paths){if(!nodes.get(path)?.selectable)continue;const lock=await client.getMailboxLock(path,{readOnly:true});try{
   const validity=validMailbox(client);const found=await client.search({since:range.searchStart,...(range.searchEnd?{before:range.searchEnd}:{})},{uid:true});if(!found)continue;
   if(found.length>50000)throw new MailboxError('符合日期范围的邮件超过50000封，请缩小范围。');
   const uids=[...new Set(found)].map(uid=>uidSchema.parse(uid)).sort((a,b)=>b-a);
   for(let offset=0;offset<uids.length;offset+=100){const page=uids.slice(offset,offset+100);const rows:FetchMessageObject[]=[];
    // Finish this fetch completely before issuing another IMAP command.
    for await(const row of client.fetch(page,query,{uid:true})){if(++scanned>10000)throw new MailboxError('本次扫描超过10000封邮件，请缩小范围。');rows.push(row);}
    for(const row of rows){const m=summary(row,path,validity),at=Date.parse(m.receivedAt);if(at<range.start||range.end!==null&&at>=range.end)continue;
     if(!matchesMailFilter(m,filter))continue;
     messages.set(m.id,m);if(messages.size>=200){limited=true;break outer;}
    }
   }
  }finally{lock.release();}}
  return {messages:[...messages.values()].sort((a,b)=>Date.parse(b.receivedAt)-Date.parse(a.receivedAt)),limited};
 });}
 /** 传输失败后重新连接，取消操作不发起重试。 */
 private async readWithRetry<T>(work:()=>Promise<T>):Promise<T>{
  const generation=this.cancelEpoch;
  try{return await work();}catch(error){
   if(generation!==this.cancelEpoch)throw error;
   if(error instanceof MailReadError&&!error.retryable)throw error;
   if(error instanceof MailboxError)throw error;
   try{return await work();}catch(second){
    if(second instanceof MailReadError&&second.code!=='TRANSFER_FAILED')throw second;
    if(second instanceof MailboxError)throw second;
    throw new MailReadError('邮件传输中断，已自动重试一次仍失败；摘要和导入记录保留，可再次重试。','transfer','TRANSFER_RETRY_FAILED');
   }
  }
 }
 async getMessage(id:string,options?:{maxBytes?:number}):Promise<MailMessage>{let path:string,validity:string,uid:number;try{if(id.length>2048)throw new Error();[path,validity,uid]=identitySchema.parse(JSON.parse(id));}catch{throw new MailboxError('邮件标识无效，请重新读取列表。');}
  return this.readWithRetry(()=>this.job(async client=>{const lock=await client.getMailboxLock(path,{readOnly:true});try{
   if(validMailbox(client)!==validity)throw new MailReadError('文件夹邮件标识已重置，请重新读取列表，避免打开错误邮件。','identity','UIDVALIDITY_RESET',false);
   const row=await client.fetchOne(String(uid),{uid:true,envelope:true,internalDate:true,flags:true,size:true,bodyStructure:true},{uid:true});
   if(!row||row.uid!==uid)throw new MailReadError('这封邮件已不存在，请刷新列表。','identity','MAIL_GONE',false);
   const base=summary(row,path,validity),info=structureInfo(row.bodyStructure);
   const limit=Math.min(Math.max(options?.maxBytes??BODY_READ_LIMIT,BODY_READ_LIMIT),25*1024*1024);
   const reportedSize=typeof row.size==='number'&&Number.isFinite(row.size)&&row.size>=0?row.size:null;
   const diagnostics:string[]=[];let body='',bodyStatus:'complete'|'pending'|'partial'|'unavailable'='complete',receivedBytes=0,bodyNote='',hasHtml=!!info.html;
   const large=reportedSize!==null&&reportedSize>LARGE_RAW_BYTES;
   let handled=false;
   if(large&&(info.text||info.html)){
    const part=info.text??info.html!;
    const value=await readTextPart(client,uid,part,limit);
    if(value){
     body=(part.type==='text/html'?plain(value.text):value.text).trim();
     bodyStatus=value.complete?'complete':'partial';receivedBytes=value.bytes;handled=true;
     if(!value.complete)bodyNote='正文超过单次读取上限（'+Math.round(limit/1024/1024)+'MB），已保存前'+Math.round(limit/1024/1024)+'MB；可继续读取或打开原邮箱查看完整内容。';
     else if(!info.text&&info.html)bodyNote='邮件只有 HTML 正文，已转换为纯文本；不执行脚本，也不自动加载外部图片。';
     diagnostics.push('大邮件流程：只读取文字正文，附件未下载。');
    }
   }
   if(!handled&&!large){
    const read=await readSource(client,uid,limit);
    receivedBytes=read.bytes;
    if(reportedSize!==null&&read.bytes!==reportedSize)diagnostics.push('服务器报告 '+reportedSize+' 字节，实际读取 '+read.bytes+' 字节；以传输与结构校验为准，不因差值拒收。');
    try{
     const parsed=await simpleParser(read.buffer,{skipHtmlToText:true,skipTextToHtml:true,skipImageLinks:true});
     const boundary=row.bodyStructure?.parameters?.boundary;
     if(read.ended&&boundary&&!read.buffer.toString('latin1').split(/\r?\n/).some(line=>line.trimEnd()===`--${boundary}--`))throw new Error('MIME closing boundary missing');
     const extracted=parsedBody(parsed);body=extracted.body;hasHtml=hasHtml||extracted.hasHtml;
     bodyStatus=read.ended?'complete':'partial';
     if(!read.ended)bodyNote='正文超过单次读取上限（'+Math.round(limit/1024/1024)+'MB），已保存已读取部分；可继续读取或打开原邮箱查看完整内容。';
    }catch{
     const second=await readSource(client,uid,limit);
     if(createHash('sha256').update(read.buffer).digest('hex')!==createHash('sha256').update(second.buffer).digest('hex'))throw new MailReadError('两次读取的正文内容不一致，未保存正文，请重试。','transfer','CONTENT_UNSTABLE');
     diagnostics.push('原邮件结构解析异常；两次读取内容一致，按格式问题单独记录，不认定为网络下载失败。');
     const part=info.text??info.html;
     const value=part?await readTextPart(client,uid,part,limit):null;
     if(value){body=(part!.type==='text/html'?plain(value.text):value.text).trim();bodyStatus=value.complete?'complete':'partial';receivedBytes=value.bytes;bodyNote='原邮件结构异常，已单独读取文字正文；可读正文已保留。';}
     else {bodyStatus='unavailable';bodyNote='原邮件格式异常，服务器也无法单独提供正文；摘要已保存，可在原邮箱查看。';}
    }
   }
   if(!handled&&large){bodyStatus='unavailable';bodyNote='服务器无法单独提供文字正文；摘要、附件信息和原邮箱入口保留。';}
   if(validMailbox(client)!==validity)throw new MailReadError('文件夹邮件标识在读取期间变化，请重新读取。','identity','UIDVALIDITY_CHANGED',false);
   const after=await client.fetchOne(String(uid),{uid:true},{uid:true});
   if(!after||after.uid!==uid)throw new MailReadError('邮件在读取期间被移动或删除，未保存本次正文。','identity','MAIL_GONE',false);
   return {...base,body,bodyStatus,bodyBytes:Buffer.byteLength(body,'utf8'),receivedBytes,reportedSize,attachments:info.attachments,bodyNote:bodyNote||undefined,hasHtml,diagnostics};
  }catch(error){if(error instanceof MailReadError||error instanceof MailboxError)throw error;throw new MailReadError('邮件正文读取中断，请重试；摘要和导入记录仍保留。','transfer','TRANSFER_FAILED');}
  finally{lock.release();}}));
 }
}
