import { z } from 'zod';
import type { Result, ExtractedDrafts } from './contracts';
import { shiftDay } from './planner';
const day = z.string().refine(value => { try { shiftDay(value,0); return true; } catch { return false; } }, '请输入有效日期');
export const mailFilterSchema = z.strictObject({
  dateMode:z.enum(['recent','since','range']), recentDays:z.number().int().min(1).max(3650),
  since:z.union([day,z.literal('')]), until:z.union([day,z.literal('')]),
  folderIds:z.array(z.string().min(1).max(2048)).max(100).refine(ids=>new Set(ids).size===ids.length),
  senderExclude:z.string().trim().max(320).optional(),subjectExclude:z.string().trim().max(300).optional(),
  senders:z.array(z.string().trim().min(1).max(1000)).max(500).optional(),excludeSenders:z.array(z.string().trim().min(1).max(1000)).max(500).optional(),
  includeChildren:z.boolean(),sender:z.string().trim().max(320),subject:z.string().trim().max(300),unreadOnly:z.boolean(),
}).refine(value=>value.dateMode==='recent'||!!value.since,'请选择开始日期').refine(value=>value.dateMode!=='range'||!!value.until&&value.until>=value.since,'结束日期不能早于开始日期');
export type MailFilter = z.infer<typeof mailFilterSchema>;
export const defaultMailFilter:MailFilter = {dateMode:'recent',recentDays:7,since:'',until:'',folderIds:[],includeChildren:false,sender:'',subject:'',unreadOnly:false};

/** 旧版单值发件人条件并入多值列表；旧字段只为读取既有配置，合并后清空。 */
export function mergeLegacySenderFilter(filter:MailFilter):MailFilter {
  const senders=[...(filter.senders??[])],excludeSenders=[...(filter.excludeSenders??[])];
  const add=(list:string[],value:string|undefined)=>{const text=value?.trim();if(text&&!list.some(item=>item.trim().toLowerCase()===text.toLowerCase()))list.push(text);};
  add(senders,filter.sender);add(excludeSenders,filter.senderExclude);
  const next:MailFilter={...filter,sender:''};
  delete next.senderExclude;
  if(senders.length)next.senders=senders;else delete next.senders;
  if(excludeSenders.length)next.excludeSenders=excludeSenders;else delete next.excludeSenders;
  return next;
}
export interface MailAccount { id:string; email:string; name:string }
export type MailMethod = 'imap' | 'microsoft';
export const imapConfigSchema = z.strictObject({
  email:z.string().trim().email().max(320), username:z.string().trim().max(320).default(''),
  host:z.string().trim().min(1).max(253).refine(value=>!/[\s/@\\?#]/.test(value)&&!value.includes('://'),'请只填写IMAP服务器地址，不含协议或路径'),
  port:z.number().int().min(1).max(65535),security:z.enum(['tls','starttls']),password:z.string().max(1000).default(''),
});
export type ImapConfigInput = z.infer<typeof imapConfigSchema>;
export type ImapPublicConfig = Omit<ImapConfigInput,'password'> & {hasSecret:boolean};
export interface MailState { method?:MailMethod; imap?:ImapPublicConfig; clientId:string; account:MailAccount|null; filter:MailFilter }
export interface MailFolder { id:string; name:string; childCount:number; unreadCount:number; parentId:string|null }
export interface MailSummary { id:string; subject:string; sender:string; receivedAt:string; preview:string; unread:boolean; webUrl:string; imported:boolean }
/** 正文可用状态与摘要导入相互独立；旧数据没有该字段时按 complete 读取。 */
export type MailBodyStatus = 'complete' | 'pending' | 'partial' | 'unavailable';
export interface MailAttachment { name:string; type:string; size:number; inline:boolean }
export interface MailMessage extends MailSummary {
  body:string;
  /** complete=正文完整；pending=仅摘要；partial=只取得部分正文；unavailable=服务器无法单独提供正文。 */
  bodyStatus?:MailBodyStatus;
  bodyBytes?:number;
  receivedBytes?:number;
  reportedSize?:number|null;
  attachments?:MailAttachment[];
  bodyNote?:string;
  hasHtml?:boolean;
  /** 仅诊断信息，不含凭据、完整正文或密码。 */
  diagnostics?:string[];
}

/** 读取失败阶段，用于导入失败明细。 */
export type MailFailureStage = 'list' | 'identity' | 'transfer' | 'structure' | 'parse' | 'save';
export interface MailReadResult { messages:MailSummary[]; limited:boolean }
export interface MailExtraction extends ExtractedDrafts { sourceName:string }
export interface MailAPI {
  getState():Promise<Result<MailState>>;
  selectMethod(method:MailMethod):Promise<Result<MailState>>;
  configureImap(input:ImapConfigInput):Promise<Result<MailState>>;
  configure(clientId:string):Promise<Result<MailState>>;
  connect():Promise<Result<MailState>>;
  disconnect():Promise<Result<MailState>>;
  listFolders(parentId?:string):Promise<Result<MailFolder[]>>;
  saveFilter(filter:MailFilter):Promise<Result<MailState>>;
  read(filter:MailFilter):Promise<Result<MailReadResult>>;
  getMessage(id:string):Promise<Result<MailMessage>>;
  openMessage(id:string):Promise<Result<void>>;
  extract(id:string, target?:{baseUrl:string;model:string}):Promise<Result<MailExtraction>>;
}

export function matchesMailFilter(item:Pick<MailSummary,'sender'|'subject'|'unread'>,filter:MailFilter):boolean {
 const sender=item.sender.toLowerCase(),subject=item.subject.toLowerCase();
 const has=(text:string,needle:string|undefined)=>!!needle&&text.includes(needle.toLowerCase());
 return !(filter.unreadOnly&&!item.unread)
  &&(!filter.sender||has(sender,filter.sender))&&(!filter.subject||has(subject,filter.subject))
  &&!has(sender,filter.senderExclude)&&!has(subject,filter.subjectExclude)
  &&(!filter.senders?.length||filter.senders.some(value=>has(sender,value)))
  &&!filter.excludeSenders?.some(value=>has(sender,value));
}
