import React,{useState} from 'react';
import type {ReactNode} from 'react';
import {CheckCheck,ChevronRight,Search} from 'lucide-react';
import type {AgentPendingItem} from '../shared/agent';
import {shortDate} from './AgentPages';

const typeLabels={
 task_draft:'待办草稿',
 task_change:'待办变更',
 mail_draft:'邮件草稿',
 learning_draft:'学习方法',
 skill_draft:'技能草稿',
 file:'文件',
 conversation:'任务对话',
 briefing:'邮件简报',
 other:'其他',
} satisfies Record<AgentPendingItem['kind']|'other',string>;

type PendingType=keyof typeof typeLabels;
type SortOrder='newest'|'oldest';
const pendingTypes=Object.keys(typeLabels) as PendingType[];

function compareCreatedAt(a:AgentPendingItem,b:AgentPendingItem,order:SortOrder){
 const first=Date.parse(a.createdAt),second=Date.parse(b.createdAt);
 if(!Number.isFinite(first))return Number.isFinite(second)?1:0;
 if(!Number.isFinite(second))return -1;
 return order==='newest'?second-first:first-second;
}

interface PendingListProps {
 items:AgentPendingItem[];
 renderItem(item:AgentPendingItem):ReactNode;
 onOpenOrigin(conversationId:string):void;
}

export function AgentPendingList({items,renderItem,onOpenOrigin}:PendingListProps){
 const [type,setType]=useState<PendingType|'all'>('all');
 const [query,setQuery]=useState('');
 const [order,setOrder]=useState<SortOrder>('newest');

 if(!items.length)return <div className="ag-empty-page"><CheckCheck size={30} aria-hidden="true"/><h3>暂时没有待确认事项</h3><p>对话产生的待办和邮件草稿会出现在这里。</p></div>;

 const groups=new Map<PendingType,AgentPendingItem[]>();
 for(const item of items){
  const group=Object.hasOwn(typeLabels,item.kind)?item.kind:'other';
  const members=groups.get(group);
  if(members)members.push(item);
  else groups.set(group,[item]);
 }
 const availableTypes=pendingTypes.filter(group=>groups.has(group)||group===type);
 const titleQuery=query.trim().toLowerCase();
 const results=(type==='all'?availableTypes:[type]).flatMap(group=>{
  const matches=(groups.get(group)??[])
   .filter(item=>item.title.toLowerCase().includes(titleQuery))
   .sort((a,b)=>compareCreatedAt(a,b,order));
  return matches.length?[{group,matches}]:[];
 });
 const visibleCount=results.reduce((count,{matches})=>count+matches.length,0);
 const canReset=type!=='all'||query!==''||order!=='newest';

 function resetFilters(){
  setType('all');
  setQuery('');
  setOrder('newest');
 }

 return <div className="ag-pending-list">
  <div className="ag-pending-tools">
   <label className="ag-search ag-pending-search">
    <Search size={16} aria-hidden="true"/>
    <input type="search" aria-label="搜索事项标题" placeholder="搜索事项标题" value={query} onChange={event=>setQuery(event.target.value)}/>
   </label>
   <label className="ag-pending-sort">组内排序
    <select value={order} onChange={event=>setOrder(event.target.value as SortOrder)}>
     <option value="newest">最新优先</option>
     <option value="oldest">最旧优先</option>
    </select>
   </label>
   <button type="button" className="ag-secondary" disabled={!canReset} onClick={resetFilters}>清空筛选</button>
  </div>
  <div className="ag-pending-types" role="group" aria-label="事项类型">
   <button type="button" aria-pressed={type==='all'} onClick={()=>setType('all')}>全部 <span>{items.length}</span></button>
   {availableTypes.map(group=><button type="button" key={group} aria-pressed={type===group} onClick={()=>setType(group)}>
    {typeLabels[group]} <span>{groups.get(group)?.length??0}</span>
   </button>)}
  </div>
  <p className="ag-pending-result-count" role="status">显示 {visibleCount} / {items.length} 项</p>
  {!visibleCount&&<div className="ag-empty-page ag-pending-no-results"><h3>没有符合筛选条件的待确认事项</h3></div>}
  {results.map(({group,matches})=><section className="ag-pending-group" key={group} aria-label={typeLabels[group]}>
   <h2>{typeLabels[group]}</h2>
   <div className="ag-pending-entries">
    {matches.map(item=>{
     const date=shortDate(item.createdAt);
     return <article key={`${item.kind}:${item.id}:${item.runId}`}>
      {group==='other'?<div className="ag-artifact"><strong>{item.title}</strong></div>:renderItem(item)}
      <div className="ag-pending-item-meta">
       <button type="button" className="ag-pending-origin" onClick={()=>onOpenOrigin(item.conversationId)}>回到原对话 <ChevronRight size={13} aria-hidden="true"/></button>
       {date&&<time dateTime={item.createdAt} title="生成时间">{date}</time>}
      </div>
     </article>;
    })}
   </div>
  </section>)}
 </div>;
}
