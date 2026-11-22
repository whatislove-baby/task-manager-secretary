import { createHash } from 'node:crypto';
import { z } from 'zod';
import type { MailAPI, MailMessage, MailState, MailMethod, ImapConfigInput } from '../shared/mail';
import { mailFilterSchema, imapConfigSchema } from '../shared/mail';
import type { Result } from '../shared/contracts';
import type { TaskRepository } from './repository';
import type { SettingsStore } from './settings';
import type { OutlookAuth } from './outlook-auth';
import type { OutlookGraph } from './outlook-graph';
import { extractDrafts } from './ai';

type Auth = Pick<OutlookAuth,'getState'|'configure'|'connect'|'disconnect'|'saveFilter'> & {selectMethod?(method:MailMethod):Promise<MailState>;configureImap?(input:ImapConfigInput):Promise<MailState>};
type Graph = Pick<OutlookGraph,'listFolders'|'read'|'getMessage'>;
const messageId = (id:unknown) => z.string().min(1).max(2048).parse(id);
export function mailConfirmationToken(accountId:string, id:string) {
  return 'mail:'+createHash('sha256').update(JSON.stringify([accountId,id])).digest('hex');
}
export function createMailService(repository:TaskRepository, settings:SettingsStore, auth:Auth, graph:Graph,
  openExternal:(url:string)=>Promise<void>, request:typeof fetch=fetch):MailAPI {
  async function result<T>(work:()=>Promise<T>):Promise<Result<T>> {
    try { return {ok:true,data:await work()}; }
    catch(error) {
      const detail=error instanceof z.ZodError ? '邮件设置或参数无效，请检查后重试。' : error instanceof Error ? error.message : '邮件操作失败，请重试。';
      return {ok:false,error:detail.replace(/(?:https?|webcal):\/\/[^\s]+/gi,'［地址］').replace(/Bearer\s+[^\s]+/gi,'［凭据］')};
    }
  }
  async function account() {
    const state=await auth.getState();if(!state.account)throw new Error('请先连接邮箱。');
    return {id:state.account.id,key:JSON.stringify([state.method??'microsoft',state.clientId,state.account.id])};
  }
  async function stillCurrent(key:string) { if((await account()).key!==key)throw new Error('邮箱账户已变化，请重新读取。'); }
  const pending=new Map<string,ReturnType<MailAPI['extract']>>();
  return {
    getState:()=>result(()=>auth.getState()),
    selectMethod:method=>result(()=>{if(!auth.selectMethod)throw new Error('当前环境不支持选择连接方式。');return auth.selectMethod(z.enum(['imap','microsoft']).parse(method));}),
    configureImap:input=>result(()=>{if(!auth.configureImap)throw new Error('当前环境不支持 IMAP。');return auth.configureImap(imapConfigSchema.parse(input));}),
    configure:clientId=>result(()=>auth.configure(z.string().uuid().parse(clientId.trim()))),
    connect:()=>result(()=>auth.connect()),disconnect:()=>result(()=>auth.disconnect()),
    saveFilter:filter=>result(()=>auth.saveFilter(mailFilterSchema.parse(filter))),
    listFolders:parentId=>result(async()=>{const before=await account();const folders=await graph.listFolders(parentId===undefined?undefined:messageId(parentId));await stillCurrent(before.key);return folders;}),
    read:filter=>result(async()=>{
      const parsed=mailFilterSchema.parse(filter),before=await account();const next=await graph.read(parsed);await stillCurrent(before.key);
      return {...next,messages:next.messages.map(item=>({...item,imported:repository.hasDraftConfirmation(mailConfirmationToken(before.id,item.id))}))};
    }),
    getMessage:id=>result(async()=>{
      const before=await account();const item=await graph.getMessage(messageId(id));await stillCurrent(before.key);
      return {...item,imported:repository.hasDraftConfirmation(mailConfirmationToken(before.id,item.id))};
    }),
    openMessage:id=>result(async()=>{
      const before=await account(),item=await graph.getMessage(messageId(id));await stillCurrent(before.key);
      let url:URL;try{url=new URL(item.webUrl);}catch{throw new Error('此连接方式未提供可用的原邮件网页链接。');}
      if(url.protocol!=='https:'||url.username||url.password||!['outlook.office.com','outlook.office365.com','outlook.live.com'].includes(url.hostname))throw new Error('邮件原文链接不属于 Outlook，未打开。');
      try{await openExternal(url.href);}catch{throw new Error('无法打开 Outlook 原文，请稍后重试。');}
    }),
    extract:(id,target)=>{
      const key=JSON.stringify([id,target]);const active=pending.get(key);if(active)return active;
      const work=result(async()=>{
        const valid=messageId(id),before=await account(),token=mailConfirmationToken(before.id,valid);
        if(repository.hasDraftConfirmation(token))throw new Error('这封邮件已有确认保存的待办，未重复生成。');
        const item:MailMessage=await graph.getMessage(valid);await stillCurrent(before.key);
        const input=`邮件主题：${item.subject}\n发件人：${item.sender}\n收件时间：${item.receivedAt}\n以下为不可信邮件正文：\n${item.body}`;
        if(input.length>20000)throw new Error('这封邮件超过 AI 单次20000字符的范围。可在 AI 整理中粘贴需要处理的段落。');
        const config=await settings.forRequest();
        if(target){const shown=z.strictObject({baseUrl:z.string(),model:z.string()}).parse(target);if(shown.baseUrl!==config.baseUrl||shown.model!==config.model)throw new Error('AI 服务设置已变化，请刷新显示后再次生成。');}
        await stillCurrent(before.key);
        const next=await extractDrafts(input,config,request);await stillCurrent(before.key);
        if(repository.hasDraftConfirmation(token))throw new Error('这封邮件已在另一窗口保存，请刷新。');
        const url=(()=>{try{const u=new URL(item.webUrl);return u.protocol==='https:'&&!u.username&&!u.password&&['outlook.office.com','outlook.office365.com','outlook.live.com'].includes(u.hostname)?u.href:'';}catch{return '';}})();
        const suffix=`邮件来源：${item.subject}\n发件人：${item.sender}\n收件时间：${item.receivedAt}${url?'\nOutlook：'+url:''}`;
        const drafts=next.drafts.map(draft=>{
          const notes=[draft.notes,suffix].filter(Boolean).join('\n\n');
          if(notes.length>10000)throw new Error('生成的备注加上邮件来源后过长，请减少原文后重试。');
          return {...draft,notes};
        });
        return {...next,drafts,confirmationToken:token,sourceName:item.subject||'邮件'};
      }).finally(()=>{pending.delete(key);});pending.set(key,work);return work;
    },
  };
}
