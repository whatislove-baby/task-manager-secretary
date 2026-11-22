import React,{useEffect,useState} from 'react';
import {ArrowUpLeft,FileText,Mail} from 'lucide-react';
import type {AgentConversation,AgentSource} from '../shared/agent';

export function AgentTaskContext({conversation,onSecretary,onParent,onSource}:{conversation:AgentConversation;onSecretary():void;onParent(id:string):void;onSource(source:AgentSource):void}){
 const [sources,setSources]=useState<AgentSource[]>([]);
 const first=conversation.turns.find(turn=>turn.role==='user');
 const mailIds=conversation.mailIds??first?.mailIds??[],fileIds=conversation.fileIds??first?.fileIds??[];
 const key=JSON.stringify([conversation.id,mailIds,fileIds]);
 useEffect(()=>{let alive=true;const initial:AgentSource[]=[...mailIds.map((id,i)=>({kind:'mail' as const,id,title:`参考邮件 ${i+1}`})),...fileIds.map((id,i)=>({kind:'file' as const,id,title:`参考文件 ${i+1}`}))];setSources(initial);
  void Promise.all(initial.map(async source=>{try{if(source.kind==='mail'){const r=await window.mailWorkspace.getLocal(source.id);return r.ok?{...source,title:r.data.subject}:source;}const r=await window.mailFiles!.get(source.id);return r.ok?{...source,title:r.data.name}:source;}catch{return source;}})).then(value=>{if(alive)setSources(value);});
  return()=>{alive=false;};
 },[key]);
 return <section className="ag-task-context" aria-label="任务上下文">
  <div className="ag-task-links"><button onClick={onSecretary}><ArrowUpLeft size={15}/>返回秘书</button>{conversation.parentConversationId&&<button onClick={()=>onParent(conversation.parentConversationId!)}>查看来源对话</button>}</div>
  <details><summary>本次任务</summary><p>{conversation.goal||first?.content||conversation.title}</p>{sources.length>0&&<div className="ag-sources" aria-label="任务参考资料">{sources.map(source=><button key={source.kind+source.id} onClick={()=>onSource(source)} title={source.title}>{source.kind==='mail'?<Mail size={13}/>:<FileText size={13}/>}<span>{source.title}</span></button>)}</div>}</details>
 </section>;
}
