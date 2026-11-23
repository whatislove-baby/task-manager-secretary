import React,{useEffect,useId,useRef,useState} from 'react';
import {Download} from 'lucide-react';
import type {AgentAPI} from '../shared/agent';
import {conversationMarkdown} from '../shared/conversation-export';

interface Props {
 api:Pick<AgentAPI,'getConversation'|'exportConversation'>;
 conversationId?:string;
 disabled:boolean;
}

export function ConversationExport({api,conversationId,disabled}:Props){
 const [open,setOpen]=useState(false),[includeSources,setIncludeSources]=useState(false);
 const [busy,setBusy]=useState(false),[feedback,setFeedback]=useState<{text:string;error?:boolean}|null>(null);
 const panelId=useId();
 const container=useRef<HTMLDivElement>(null),trigger=useRef<HTMLButtonElement>(null);
 const alive=useRef(false);
 useEffect(()=>{alive.current=true;return()=>{alive.current=false;};},[]);
 useEffect(()=>{
  if(!open)return;
  const clickOutside=(event:PointerEvent)=>{
   if(event.target instanceof Node&&!container.current?.contains(event.target))setOpen(false);
  };
  const close=(event:KeyboardEvent)=>{
   if(event.key==='Escape'){setOpen(false);if(container.current?.contains(document.activeElement))trigger.current?.focus();}
  };
  document.addEventListener('pointerdown',clickOutside);
  document.addEventListener('keydown',close);
  return()=>{document.removeEventListener('pointerdown',clickOutside);document.removeEventListener('keydown',close);};
 },[open]);

 async function exportCurrent(action:'copy'|'save'){
  if(!conversationId||busy)return;
  setBusy(true);
  setFeedback(null);
  const report=(text:string,error=false)=>{if(alive.current)setFeedback({text,error});};
  try {
   if(action==='save'){
    const result=await api.exportConversation({id:conversationId,includeSources});
    if(!result.ok)report(result.error,true);
    else report(result.data?'Markdown 已保存。':'已取消保存。');
    return;
   }
   const result=await api.getConversation(conversationId);
   if(!alive.current)return;
   if(!result.ok||result.data.id!==conversationId){report('无法读取这段对话，请重试。',true);return;}
   const markdown=conversationMarkdown(result.data,includeSources);
   if(!markdown){report('这段对话没有可导出的内容。');return;}
   await navigator.clipboard.writeText(markdown);
   report('完整对话已复制。');
  } catch {
   report(action==='save'?'保存对话失败，请重试。':'复制对话失败，请重试。',true);
  } finally {
   if(alive.current)setBusy(false);
  }
 }

 return <div className="ag-conversation-export" ref={container}>
  <button ref={trigger} className="ag-export-trigger" type="button" aria-expanded={open} aria-controls={panelId} disabled={disabled} onClick={()=>setOpen(value=>!value)}><Download size={15} aria-hidden="true"/>导出对话</button>
  {open&&<section className="ag-export-panel" id={panelId} aria-label="导出对话选项" aria-busy={busy}>
   {conversationId?<>
    <label><input type="checkbox" checked={includeSources} disabled={busy} onChange={event=>setIncludeSources(event.target.checked)}/>附上来源与产物名称</label>
    <div className="ag-export-actions">
     <button className="ag-secondary" type="button" disabled={busy} onClick={()=>void exportCurrent('copy')}>复制完整对话</button>
     <button className="ag-secondary" type="button" disabled={busy} onClick={()=>void exportCurrent('save')}>保存 Markdown</button>
    </div>
   </>:<p role="status">这段对话没有可导出的内容。</p>}
   {busy&&<p role="status">正在导出对话…</p>}
   {feedback&&<p role={feedback.error?'alert':'status'}>{feedback.text}</p>}
  </section>}
 </div>;
}
