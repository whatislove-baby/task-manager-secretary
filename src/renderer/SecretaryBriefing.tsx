import React,{useEffect,useState} from 'react';
import {ArrowUpRight,Mail,RefreshCw} from 'lucide-react';
import type {AgentBriefing,BriefingCategory} from '../shared/agent';

const groups:{key:BriefingCategory;label:string;expanded:boolean}[]=[
 {key:'action',label:'需要处理',expanded:true},
 {key:'important',label:'重要通知',expanded:true},
 {key:'review',label:'需要核对',expanded:true},
 {key:'normal',label:'其他邮件',expanded:false},
 {key:'spam',label:'疑似推广或垃圾邮件',expanded:false},
];

/** A saved result from one inbox pass, never a substitute for the entire inbox. */
export function SecretaryBriefing({id,onRead,onUse}:{id:string;onRead(id:string):void;onUse(id:string):void}){
 const [value,setValue]=useState<AgentBriefing|null>(null),[error,setError]=useState(''),[retry,setRetry]=useState(0);
 useEffect(()=>{let active=true;setValue(null);setError('');
  void window.agent!.getBriefing(id).then(result=>{if(!active)return;if(!result.ok)throw Error(result.error);setValue(result.data);}).catch(e=>{if(active)setError(e instanceof Error?e.message:'无法读取本次整理。');});
  return()=>{active=false;};
 },[id,retry]);
 return <section className="ag-briefing" aria-label="本次收件整理">
  <div className="ag-briefing-heading"><Mail size={17}/><strong>本次收件整理</strong></div>
  {!value&&!error&&<p className="ag-muted" role="status">正在读取整理结果…</p>}
  {error&&<div className="ag-briefing-error" role="alert"><span>{error}</span><button onClick={()=>setRetry(n=>n+1)}><RefreshCw size={13}/>重新读取整理</button></div>}
  {value&&<><p className="ag-briefing-note">这是本轮整理记录，后续更正不会改写旧卡。邮件仍全部保留在收件箱。</p>{value.warning&&<p className="ag-briefing-warning">{value.warning}</p>}
   {groups.map(group=>{const items=value.items.filter(item=>item.category===group.key);if(!items.length&&!value.counts[group.key])return null;return <details key={group.key} className="ag-briefing-group" open={group.expanded}>
    <summary>{group.label}<small>{value.counts[group.key]??items.length}</small></summary>
    <ul>{items.map(item=><li key={item.mailId}><button className="ag-briefing-mail" aria-label={'阅读邮件：'+item.subject} onClick={()=>onRead(item.mailId)}><strong>{item.subject||'无主题'}</strong>{item.sender&&<span>{item.sender}</span>}{(item.summary||item.reason)&&<p>{item.summary||item.reason}</p>}</button><button className="ag-briefing-use" aria-label={'带入对话：'+item.subject} title="带入当前对话" onClick={()=>onUse(item.mailId)}><ArrowUpRight size={16}/></button></li>)}</ul>
   </details>;})}
   {!value.items.length&&<p className="ag-muted">这次没有可展示的整理结果；可查看收件箱或执行记录。</p>}
  </>}
 </section>;
}
