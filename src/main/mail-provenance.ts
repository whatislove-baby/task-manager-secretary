import type {TodoDraft} from '../shared/todo';
import type {LocalMail} from '../shared/mail-workspace';
const normalize=(text:string)=>text.toLowerCase().replace(/[^\p{L}\p{N}]/gu,'');
export function draftMailSources(draft:TodoDraft,messages:LocalMail[]):LocalMail[]{
 if(messages.length===1)return messages;
 const pieces=(draft.source||'').replace(/^邮件\s*\d+\s*[:：]/,'').split(/\.{3,}|…+/).map(normalize).filter(s=>s.length>=8);
 if(!pieces.length)return [];
 const candidates=messages.filter(m=>pieces.every(p=>normalize(m.subject+' '+m.body).includes(p)));
 const cited=messages.filter(m=>draft.sourceMailIds?.includes(m.localId));
 if(cited.length&&cited.length===draft.sourceMailIds?.length&&pieces.every(p=>cited.some(m=>normalize(m.subject+' '+m.body).includes(p)))&&cited.every(m=>pieces.some(p=>normalize(m.subject+' '+m.body).includes(p))))return cited;
 return candidates.length===1?candidates:[];
}
export function provenanceNotes(draft:TodoDraft,messages:LocalMail[],analysisId:string):string{
 const sources=draftMailSources(draft,messages);
 const label=sources.length?'邮件分析来源：\n'+sources.map(m=>`${m.accountEmail} · ${m.subject}\n本地邮件：${m.localId}\n收件时间：${m.receivedAt}${m.webUrl?'\n原邮件：'+m.webUrl:''}`).join('\n'):'邮件分析来源：待核实，未将同批其他邮件自动认定为本待办来源。';
 const suffix=label+'\n分析记录：'+analysisId;
 return (draft.notes?.length??0)+suffix.length+2<=10000?[draft.notes,suffix].filter(Boolean).join('\n\n'):(draft.notes??'');
}
