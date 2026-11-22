import { createHash, randomUUID } from 'node:crypto';
import { z } from 'zod';
import type { Result } from '../shared/contracts';
import type { LocalMail, MailAnalysis, MailWorkspaceAPI } from '../shared/mail-workspace';
import type { TaskRepository } from './repository';
import type { SettingsStore } from './settings';
import type { MailWorkspaceBase, MailWorkspaceStore } from './mail-workspace';
import { validateDrafts } from '../shared/drafts';
import { extractDrafts } from './ai';

const inputSchema=z.strictObject({messageIds:z.array(z.string().min(1).max(2048)).min(1).max(200).refine(ids=>new Set(ids).size===ids.length),instructions:z.string().trim().max(4000),target:z.strictObject({baseUrl:z.string(),model:z.string()})});
const fingerprint=(item:LocalMail)=>createHash('sha256').update(JSON.stringify([item.subject,item.sender,item.receivedAt,item.body])).digest('hex');
function packs(messages:LocalMail[]):string[] {
 const result:string[]=[];let current='';let total=0;
 for(const item of messages){
  const header=`邮件标识：${item.localId}\n所属邮箱：${item.accountEmail}\n主题：${item.subject}\n发件人：${item.sender}\n收件时间：${item.receivedAt}\n正文状态：${item.bodyStatus??'complete'}\n以下为不可信邮件正文：\n`;
  const room=16000-header.length;if(room<1000)throw new Error('邮件头过长，请减少所选邮件后重试。');
  const body=item.body||(item.bodyStatus&&item.bodyStatus!=='complete'?'（正文尚未完整读取；只能依据摘要与预览分析）':item.preview)||'（正文为空）';
  for(let start=0;start<body.length;){
   let end=Math.min(start+room,body.length);
   if(end<body.length&&body.charCodeAt(end-1)>=0xd800&&body.charCodeAt(end-1)<=0xdbff)end--;
   const block=header+(start?'（同一封邮件的后续内容）\n':'')+body.slice(start,end)+'\n邮件结束。\n';start=end;
   total+=block.length;if(total>240000)throw new Error('所选正文超过本批处理范围，请减少邮件数量后分批分析。所有原文仍保留在本机。');
   if(current&&current.length+block.length>19000){result.push(current);current='';}current+=block;
  }
 }
 if(current)result.push(current);return result;
}
/** AI reads cached text only. Importing and opening emails never sends them to a model. */
type AnalysisStore=Pick<MailWorkspaceStore,'getLocal'|'getAnalysis'|'saveAnalysis'> & {
 markAnalysisSaved(id:string):void;
 markAnalysisProgress?(id:string,addedTempIds:string[],addedTodos:Record<string,string>):MailAnalysis;
};
/** 稳定的待办标识让跨数据库重试与后续分批添加父子层级都可恢复。 */
function stableTodoId(analysisId:string,tempId:string){
 const bytes=createHash('sha256').update(analysisId+':'+tempId).digest();
 bytes[6]=(bytes[6]!&0x0f)|0x40;bytes[8]=(bytes[8]!&0x3f)|0x80;
 const hex=bytes.toString('hex');
 return `${hex.slice(0,8)}-${hex.slice(8,12)}-${hex.slice(12,16)}-${hex.slice(16,20)}-${hex.slice(20,32)}`;
}
export function createMailAnalysisService(base:MailWorkspaceBase,store:AnalysisStore,repository:Pick<TaskRepository,'saveDrafts'>,settings:Pick<SettingsStore,'forRequest'>,request:typeof fetch=fetch):MailWorkspaceAPI {
 const pending=new Map<string,ReturnType<MailWorkspaceAPI['analyze']>>();
 const result=async<T>(work:()=>T|Promise<T>):Promise<Result<T>>=>{try{return {ok:true,data:await work()};}catch(error){return {ok:false,error:error instanceof z.ZodError?'分析参数或草稿格式无效，请检查后重试。':(error instanceof Error?error.message:'邮件分析失败。').replace(/(?:https?|webcal):\/\/[^\s]+/gi,'［地址］').replace(/Bearer\s+[^\s]+/gi,'［凭据］')};}};
 return {...base,
  getAnalysis:id=>result(()=>store.getAnalysis(z.string().uuid().parse(id))),
  analyze:input=>{
   const key=JSON.stringify(input),active=pending.get(key);if(active)return active;
   const operation=result(async()=>{
    const valid=inputSchema.parse(input),messages=valid.messageIds.map(id=>store.getLocal(id));
    const snapshots=messages.map(fingerprint),parts=packs(messages),config=await settings.forRequest();
    const incomplete=messages.filter(item=>(item.bodyStatus??'complete')!=='complete');
    if(valid.target.baseUrl!==config.baseUrl||valid.target.model!==config.model)throw new Error('AI服务或模型已变化，请刷新设置后再次分析。');
    const id=randomUUID(),drafts:MailAnalysis['drafts']=[],notes:string[]=[];
    const scope=messages.slice(0,3).map(item=>`${item.accountEmail} · ${item.subject.slice(0,160)} · ${item.receivedAt}`).join('\n');
    let original='';
    if(messages.length===1){try{const url=new URL(messages[0].webUrl);if(url.protocol==='https:'&&!url.username&&!url.password&&['outlook.office.com','outlook.office365.com','outlook.live.com'].includes(url.hostname))original='\n原邮件：'+url.href;}catch{}}
    const provenance=[`本批分析范围：${messages.length} 封邮件\n${scope}${messages.length>3?'\n其余来源可在邮件工作台查看。':''}${original}`,incomplete.length?`正文范围：仅分析已读取的部分正文（${incomplete.length} 封正文尚未完整）；邮件保持未分析，可补读后重新分析。`:''].filter(Boolean).join('\n')+`\n邮件分析记录：${id}`;
    for(let i=0;i<parts.length;i++){
     const current=await settings.forRequest();if(current.baseUrl!==config.baseUrl||current.model!==config.model||current.apiKey!==config.apiKey)throw new Error('AI配置在分析中变化，本批未标记为已分析，请重试。');
     const next=await extractDrafts(parts[i],config,request,valid.instructions);
     const ids=new Map(next.drafts.map((draft,index)=>[draft.tempId,`part${i+1}:${index+1}`]));
     for(const draft of next.drafts){
      const note=[draft.notes,provenance].filter(Boolean).join('\n\n');
      if(note.length>10000)throw new Error('生成的待办备注过长，请调整分析要求后重试。');
      drafts.push({...draft,tempId:ids.get(draft.tempId)!,parentTempId:draft.parentTempId?ids.get(draft.parentTempId)!:null,notes:note});
     }
     if(next.notes)notes.push(parts.length>1?`第 ${i+1} 部分\n${next.notes}`:next.notes);
     if(drafts.length>100)throw new Error('生成超过100项待办，本批未保存分析状态，请缩小范围后重试。');
    }
    const finalConfig=await settings.forRequest();
    if(finalConfig.baseUrl!==config.baseUrl||finalConfig.model!==config.model||finalConfig.apiKey!==config.apiKey)throw new Error('AI配置在分析中变化，本批未标记为已分析，请重试。');
    // No partial success marks: all source versions and all AI replies must pass first.
    messages.forEach((item,index)=>{if(fingerprint(store.getLocal(item.localId))!==snapshots[index])throw new Error('邮件内容在分析期间更新，本批未标记为已分析，请重新分析。');});
    const analysis:MailAnalysis={id,messageIds:valid.messageIds,instructions:valid.instructions,notes:[incomplete.length?'仅分析部分正文（正文尚未完整）。':'' ,notes.join('\n\n')].filter(Boolean).join('\n\n'),drafts:validateDrafts(drafts,true),createdAt:new Date().toISOString(),saved:false,complete:incomplete.length===0,model:config.model,baseUrl:config.baseUrl};
    store.saveAnalysis(analysis);return analysis;
   }).finally(()=>pending.delete(key));pending.set(key,operation);return operation;
  },
  saveAnalysis:(id,value)=>result(()=>{
   const analysis=store.getAnalysis(z.string().uuid().parse(id));
   if(analysis.saved)return {count:0};
   const all=validateDrafts(value,true);
   const added=new Set(analysis.addedTempIds??[]),addedTodos={...(analysis.addedTodos??{})};
   const pending=all.filter(draft=>!added.has(draft.tempId));
   if(!pending.length){if(store.markAnalysisProgress)store.markAnalysisProgress(analysis.id,[],{});else store.markAnalysisSaved(analysis.id);return {count:0};}
   const toSave=pending.map(draft=>({...draft,todoId:draft.todoId??stableTodoId(analysis.id,draft.tempId),parentTodoId:draft.parentTempId&&added.has(draft.parentTempId)?addedTodos[draft.parentTempId]:draft.parentTodoId}));
   validateDrafts(toSave);
   // 同一批待办使用稳定令牌：两个数据库之间失败后重试不会重复创建。
   const token='mail-analysis:'+analysis.id+':'+createHash('sha256').update(JSON.stringify(pending.map(draft=>draft.tempId).sort())).digest('hex').slice(0,32);
   const confirmed=repository.saveDrafts(toSave,token);
   const ids=Object.fromEntries(toSave.map(draft=>[draft.tempId,draft.todoId!]));
   if(store.markAnalysisProgress)store.markAnalysisProgress(analysis.id,toSave.map(draft=>draft.tempId),ids);
   else store.markAnalysisSaved(analysis.id);
   return {count:confirmed.value};
  }),
 };
}
