import React,{useEffect,useRef,useState,useCallback} from 'react';
import {createPortal} from 'react-dom';
import type {LocalMail} from '../shared/mail-workspace';
import type {Result} from '../shared/contracts';
const unwrap=<T,>(r:Result<T>):T=>{if(!r.ok)throw new Error(r.error);return r.data;};
function MailReader({ids,exact,notes,onClose}:{ids:string[];exact:boolean;notes:string;onClose():void}){
 const overlay=useRef<HTMLDivElement>(null),panel=useRef<HTMLElement>(null);
 const [messages,setMessages]=useState<{id:string;mail?:LocalMail}[]>([]),[selected,setSelected]=useState<string|null>(exact&&ids.length===1?ids[0]:null),[loading,setLoading]=useState(true),[resolvedExact,setResolvedExact]=useState(exact);
 useEffect(()=>{let live=true;void Promise.all(ids.map(async id=>{try{return {id,mail:unwrap(await window.mailWorkspace.getLocal(id))};}catch{return {id};}})).then(items=>{if(live){setMessages(items);const matches=items.filter(x=>x.mail&&notes.includes(x.mail.subject));if(!exact&&matches.length===1){setSelected(matches[0].id);setResolvedExact(true);}setLoading(false);}});return()=>{live=false;};},[ids,notes,exact]);
 useEffect(()=>{const previous=document.activeElement as HTMLElement|null;const siblings=Array.from(document.body.children).filter(n=>n!==overlay.current) as HTMLElement[],states=siblings.map(n=>n.inert);siblings.forEach(n=>n.inert=true);panel.current?.focus();
  const key=(e:KeyboardEvent)=>{if(e.key==='Escape'){e.preventDefault();e.stopPropagation();onClose();}if(e.key==='Tab'){const nodes=Array.from(panel.current?.querySelectorAll<HTMLElement>('button,[tabindex="0"]')??[]).filter(n=>!(n as HTMLButtonElement).disabled),first=nodes[0],last=nodes[nodes.length-1];if(e.shiftKey&&(document.activeElement===first||!nodes.includes(document.activeElement as HTMLElement))){e.preventDefault();last?.focus();}else if(!e.shiftKey&&(document.activeElement===last||!nodes.includes(document.activeElement as HTMLElement))){e.preventDefault();first?.focus();}}};
  const focus=(e:FocusEvent)=>{if(panel.current&&!panel.current.contains(e.target as Node))panel.current.focus();};document.addEventListener('keydown',key,true);document.addEventListener('focusin',focus);return()=>{document.removeEventListener('keydown',key,true);document.removeEventListener('focusin',focus);siblings.forEach((n,i)=>n.inert=states[i]);if(previous?.isConnected)previous.focus();};},[onClose]);
 const item=messages.find(m=>m.id===selected);
 return createPortal(<div className="canvas-reader-overlay" ref={overlay} onPointerDown={e=>e.stopPropagation()}><section className="canvas-reader" role="dialog" aria-modal="true" aria-label="来源邮件正文" tabIndex={-1} ref={panel}><header><div><span className="canvas-reader-eyebrow">MAIL · 本地原文</span><h2>{item?.mail?.subject||'来源邮件'}</h2></div><button aria-label="关闭来源邮件" onClick={onClose}>×</button></header><div className="canvas-reader-body" tabIndex={0} aria-label="邮件原文内容">{loading?'正在读取本地邮件…':<>{!resolvedExact&&<p>以下为该分析批次的邮件，请选择要查看的原文。</p>}{(!resolvedExact)&&messages.map(m=><button key={m.id} onClick={()=>setSelected(m.id)}>{m.mail?.subject||'正文已删除的邮件'}</button>)}{item&&(item.mail?<><p>{item.mail.sender} · {new Date(item.mail.receivedAt).toLocaleString('zh-CN')}</p><p>{item.mail.body||'正文为空。'}</p></>:<p>这封邮件的本地正文已删除或不可用，导入与分析记忆仍保留；不会自动重新导入。</p>)}</>}</div></section></div>,document.body);
}
export function TaskMailSource({notes,widget=false}:{notes:string;widget?:boolean}){
 const direct=[...notes.matchAll(/本地邮件：\s*([a-f0-9]{64})/gi)].map(m=>m[1]);
 const analysis=notes.match(/分析记录：\s*([a-f0-9-]{36})/i)?.[1];
 const [ids,setIds]=useState<string[]|null>(null),[exact,setExact]=useState(false),[error,setError]=useState(''),[busy,setBusy]=useState(false);const version=useRef(0);
 useEffect(()=>{version.current++;setIds(null);setError('');setBusy(false);return()=>{version.current++;};},[notes]);
 const close=useCallback(()=>setIds(null),[]);
 if(!direct.length&&!analysis)return null;
 async function open(){if(busy)return;setBusy(true);setError('');const request=version.current;try{let values=direct;if(!values.length&&analysis)values=unwrap(await window.mailWorkspace.getAnalysis(analysis)).messageIds;if(request!==version.current)return;if(!values.length)throw new Error('这条分析没有绑定邮件来源。');if(widget)await window.widget?.setExpanded(true);if(request!==version.current)return;setExact(direct.length>0||values.length===1);setIds([...new Set(values)]);}catch(e){if(request===version.current)setError(e instanceof Error?e.message:'无法读取来源');}finally{if(request===version.current)setBusy(false);}}
 return <div className="canvas-source-card" aria-label="邮件来源快捷查看"><button disabled={busy} onClick={()=>void open()}>查看来源邮件正文</button>{error&&<p role="alert">{error}</p>}{ids&&<MailReader ids={ids} exact={exact} notes={notes} onClose={close}/>}</div>;
}
