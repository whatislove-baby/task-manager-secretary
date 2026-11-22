import React,{useEffect,useRef} from 'react';
import {createPortal} from 'react-dom';
import type {LocalMail} from '../shared/mail-workspace';

/** 长正文只在独立阅读层显示；纯文本渲染，不执行 HTML，也不加载外部图片。 */
export function MailBodyReader({message,onClose}:{message:LocalMail;onClose():void}){
 const panel=useRef<HTMLElement>(null),overlay=useRef<HTMLDivElement>(null);
 useEffect(()=>{
  const previous=document.activeElement as HTMLElement|null;
  panel.current?.focus();
  const key=(event:KeyboardEvent)=>{if(event.key==='Escape'){event.preventDefault();event.stopPropagation();onClose();}};
  document.addEventListener('keydown',key,true);
  return ()=>{document.removeEventListener('keydown',key,true);if(previous?.isConnected)previous.focus();};
 },[onClose]);
 return createPortal(<div className="mw-body-reader" ref={overlay} onPointerDown={event=>{if(event.target===event.currentTarget)onClose();}}>
  <section role="dialog" aria-modal="true" aria-labelledby="mw-reader-title" tabIndex={-1} ref={panel}>
   <header><div><small>{message.accountEmail}</small><h2 id="mw-reader-title">{message.subject||'无主题'}</h2><p>{message.sender} · {new Date(message.receivedAt).toLocaleString('zh-CN')}</p></div><button onClick={onClose}>关闭阅读窗格</button></header>
   {message.bodyStatus&&message.bodyStatus!=='complete'&&<p className="mw-job-error">{message.bodyStatus==='pending'?'正文尚未读取，当前只有摘要。':message.bodyStatus==='partial'?'正文尚未完整，这里只显示已读取的部分。':'服务器没有提供可读正文，请打开原邮箱查看。'}{message.bodyNote?' '+message.bodyNote:''}</p>}
   <div className="mw-body-content" tabIndex={0}>{message.body||'邮件正文为空。'}</div>
  </section>
 </div>,document.body);
}
