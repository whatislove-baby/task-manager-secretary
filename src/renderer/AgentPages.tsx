import React,{useEffect,useId,useRef,useState} from 'react';
import {createPortal} from 'react-dom';
import {ArrowUpRight,FileText,Mail,Plus,RefreshCw,Search,X} from 'lucide-react';
import type {LocalMail,LocalMailPage} from '../shared/mail-workspace';
import type {FileRecord} from '../shared/mail-files';
import type {MailJob} from '../shared/mail-operations';
import type {Result} from '../shared/contracts';
import {FileCard,fileSize} from './MailFiles';

const unwrap=<T,>(r:Result<T>):T=>{if(!r.ok)throw Error(r.error);return r.data;};
const describe=(e:unknown)=>e instanceof Error?e.message:'操作未完成，请重试。';
export const shortDate=(date:string)=>{try{return new Intl.DateTimeFormat('zh-CN',{month:'numeric',day:'numeric',hour:'2-digit',minute:'2-digit'}).format(new Date(date));}catch{return '';}};

export function AgentModal({title,children,onClose,wide=false,closeDisabled=false}:{title:string;children:React.ReactNode;onClose():void;wide?:boolean;closeDisabled?:boolean}) {
 const label=useId(),root=useRef<HTMLDivElement>(null),panel=useRef<HTMLElement>(null),latest=useRef({onClose,closeDisabled});latest.current={onClose,closeDisabled};
 useEffect(()=>{
  const previous=document.activeElement as HTMLElement|null;
  const siblings=Array.from(document.body.children).filter(n=>n!==root.current) as HTMLElement[];
  const inert=siblings.map(n=>n.inert);siblings.forEach(n=>n.inert=true);panel.current?.focus();
  function key(e:KeyboardEvent){if(e.key==='Escape'&&!latest.current.closeDisabled){e.preventDefault();latest.current.onClose();}
   if(e.key==='Tab'){const nodes=Array.from(panel.current?.querySelectorAll<HTMLElement>('button:not(:disabled),input:not(:disabled),textarea:not(:disabled),select:not(:disabled),summary,[tabindex="0"]')??[]).filter(n=>!n.closest('[hidden]'));const first=nodes[0],last=nodes[nodes.length-1];if(!first){e.preventDefault();panel.current?.focus();}else if(e.shiftKey&&(document.activeElement===first||document.activeElement===panel.current)){e.preventDefault();last.focus();}else if(!e.shiftKey&&document.activeElement===last){e.preventDefault();first.focus();}}
  }
  document.addEventListener('keydown',key);return()=>{document.removeEventListener('keydown',key);siblings.forEach((n,i)=>n.inert=inert[i]);previous?.focus();};
 },[]);
 return createPortal(<div className="ag-modal-backdrop" ref={root} onMouseDown={e=>{if(e.target===e.currentTarget&&!closeDisabled)onClose();}}><section className={'ag-modal'+(wide?' ag-modal-wide':'')} role="dialog" aria-modal="true" aria-labelledby={label} tabIndex={-1} ref={panel}><header><h2 id={label}>{title}</h2><button aria-label={'关闭'+title} disabled={closeDisabled} onClick={onClose}><X size={20}/></button></header><div className="ag-modal-body">{children}</div></section></div>,document.body);
}

const syncIsActive=(job:MailJob|null)=>!!job&&['queued','running','pausing','canceling'].includes(job.state);
const syncLabel='收取最近邮件';

export function AgentInbox({onRead,onUse,onManage,selectMode=false,onSelect}:{onRead(mail:LocalMail):void;onUse(mail:LocalMail):void;onManage():void;selectMode?:boolean;onSelect?(mail:LocalMail):void}) {
 const [page,setPage]=useState<LocalMailPage>({messages:[],total:0}),[search,setSearch]=useState(''),[offset,setOffset]=useState(0),[loading,setLoading]=useState(true),[error,setError]=useState(''),[revision,setRevision]=useState(0);
 const [job,setJob]=useState<MailJob|null>(null),[busy,setBusy]=useState(false),[syncError,setSyncError]=useState(''),[connected,setConnected]=useState<number|null>(null);
 const alive=useRef(true),locked=useRef(false),operation=useRef(0),refreshed=useRef(new Set<string>());
 const active=syncIsActive(job),api=window.mailOperations;
 useEffect(()=>{alive.current=true;return()=>{alive.current=false;operation.current++;};},[]);
 useEffect(()=>{let live=true;setLoading(true);setError('');const timer=setTimeout(()=>{void window.mailWorkspace.listLocal({search,offset,limit:30}).then(r=>{if(live)setPage(unwrap(r));}).catch(e=>{if(live)setError(describe(e));}).finally(()=>{if(live)setLoading(false);});},search?180:0);return()=>{live=false;clearTimeout(timer);};},[search,offset,revision]);
 useEffect(()=>{
  if(selectMode)return;
  let live=true;const version=operation.current;
  const accounts=()=>{void window.mailWorkspace.getAccounts().then(r=>{if(live)setConnected(unwrap(r).filter(a=>!!a.state.account).length);}).catch(()=>{/* Starting a sync rechecks account state in the backend. */});};
  accounts();
  if(api?.listJobs)void api.listJobs().then(r=>{
   if(!live||version!==operation.current)return;
   const jobs=unwrap(r).filter(j=>j.kind==='import'&&j.label===syncLabel).sort((a,b)=>b.createdAt.localeCompare(a.createdAt));
   setJob(jobs.find(j=>syncIsActive(j))??jobs[0]??null);
  }).catch(e=>{if(live&&version===operation.current)setSyncError(describe(e));});
  const unsubscribe=window.widget?.onChanged(()=>{accounts();if(live)setRevision(n=>n+1);});
  return()=>{live=false;unsubscribe?.();};
 },[selectMode,api]);
 useEffect(()=>{
  if(selectMode||!job||!active||!api)return;
  let live=true,timer:ReturnType<typeof setTimeout>;const id=job.id,version=operation.current;
  async function poll(){
   try{const next=unwrap(await api!.getJob(id));if(!live||version!==operation.current)return;setJob(next);setSyncError('');if(!syncIsActive(next))return;}
   catch(e){if(!live||version!==operation.current)return;setSyncError('暂时无法读取收取进度：'+describe(e));}
   if(live)timer=setTimeout(()=>void poll(),700);
  }
  timer=setTimeout(()=>void poll(),250);
  return()=>{live=false;clearTimeout(timer);};
 },[job?.id,job?.state,active,api,selectMode,busy]);
 useEffect(()=>{
  if(!job||active||refreshed.current.has(job.id+':'+job.state))return;
  refreshed.current.add(job.id+':'+job.state);setRevision(n=>n+1);
 },[job,active]);
 async function synchronize(resume=false){
  if(locked.current||active)return;
  if(!api){setSyncError('当前版本未提供收取接口，请打开邮箱设置。');return;}
  locked.current=true;setBusy(true);setSyncError('');const version=++operation.current;
  try{
   const next=unwrap(await (resume&&job?api.resumeJob(job.id):api.startInboxSync()));
   if(alive.current&&version===operation.current){setJob(next);setConnected(old=>old===0?null:old);}
  }catch(e){if(alive.current&&version===operation.current)setSyncError(describe(e));}
  finally{locked.current=false;if(alive.current&&version===operation.current)setBusy(false);}
 }
 const report=job?.report;
 const counts=report?`新增 ${report.added} 封 · 跳过已收 ${report.skipped} 封${report.failed?` · 失败 ${report.failed} 项`:''}${report.bodyPending?` · ${report.bodyPending} 封正文待补读`:''}`:'';
 const incomplete=!!report&&(report.failed>0||!!report.bodyPending||report.limited);
 const status=active?`正在收取新邮件${job&&job.total>0?` · 已处理 ${job.done}/${job.total} 封`:'…'}`:job?.state==='completed'?(incomplete?'本次收取结束，仍有内容待处理':'收取完成'):job?.state==='failed'?'本次收取未完成':job?.state==='paused'?'收取已暂停':job?.state==='canceled'?'收取已取消':'';
 return <section className="ag-inbox"><div className="ag-page-actions"><label className="ag-search"><Search size={17}/><input aria-label="搜索邮件主题或发件人" placeholder="搜索主题或发件人" value={search} onChange={e=>{setSearch(e.target.value);setOffset(0);}}/></label>
 {selectMode?<button className="ag-secondary" aria-label="刷新列表" title="刷新已收取的邮件列表" onClick={()=>setRevision(n=>n+1)}><RefreshCw size={16}/></button>:<>
  <button className="ag-primary" disabled={busy||active} onClick={()=>void synchronize()}><RefreshCw size={16} className={busy||active?'ag-spin':undefined}/>{busy||active?'正在收取…':'收取新邮件'}</button>
  <button className="ag-secondary" onClick={onManage}>邮箱设置</button>
 </>}</div>
 {!selectMode&&<>
  <p className="ag-page-note ag-sync-scope">最近 7 天 · 已连接邮箱的收件箱 · 自动跳过已收邮件</p>
  {connected===0&&<p className="ag-sync-empty">尚未连接邮箱。<button onClick={onManage}>连接邮箱</button>后即可一键收取。</p>}
  {job&&<div className={'ag-sync-result'+(incomplete||job.state==='failed'?' ag-sync-warning':'')} role="status" aria-live="polite">
   <div><strong>{status}</strong>{counts&&<p>{counts}</p>}{job.error&&<p>{job.error}</p>}{report?.limited&&<p>仍有邮件未收取，请查看详情后继续。</p>}</div>
   {(job.state==='paused'||job.state==='failed')&&<button className="ag-secondary" disabled={busy} onClick={()=>void synchronize(true)}>继续收取</button>}
   {(incomplete||job.state==='failed'||job.state==='paused')&&<button className="ag-secondary" onClick={onManage}>查看收取详情</button>}
  </div>}
  {syncError&&<p role="alert" className="ag-error">{syncError} <button className="ag-sync-settings" onClick={onManage}>打开邮箱设置</button></p>}
 </>}
 {error&&<p role="alert" className="ag-error">{error}</p>}
 {loading?<p className="ag-muted" role="status">正在读取收件箱…</p>:!page.messages.length?<div className="ag-empty-page"><Mail size={30}/><h3>{search?'没有匹配的邮件':'收件箱还没有邮件'}</h3><p>{search?'试试课程名称或发件人。':selectMode?'请先在收件箱收取邮件。':'点击上方“收取新邮件”，新收到的邮件会出现在这里。'}</p></div>:<div className="ag-mail-list">{page.messages.map(mail=><article key={mail.localId}><button className="ag-mail-row" onClick={()=>selectMode?onSelect?.(mail):onRead(mail)}><span className="ag-avatar">{(mail.sender||'?').slice(0,1).toUpperCase()}</span><span className="ag-mail-text"><span className="ag-mail-meta"><strong>{mail.sender}</strong><time>{shortDate(mail.receivedAt)}</time></span><strong className="ag-mail-subject">{mail.subject||'无主题'}</strong><span className="ag-mail-preview">{mail.preview||'打开查看正文'}</span></span></button>{!selectMode&&<button className="ag-mail-use" aria-label={'与助手讨论：'+mail.subject} onClick={()=>onUse(mail)}><ArrowUpRight size={18}/></button>}</article>)}</div>}
 {page.total>30&&<div className="ag-pagination"><span>{offset+1}–{Math.min(offset+30,page.total)} / {page.total}</span><button disabled={offset===0||loading} onClick={()=>setOffset(n=>Math.max(0,n-30))}>上一页</button><button disabled={offset+30>=page.total||loading} onClick={()=>setOffset(n=>n+30)}>下一页</button></div>}
 </section>;
}

export function AgentLibrary({onUse,onAttach}:{onUse(file:FileRecord):void;onAttach(id:string):void}) {
 const [files,setFiles]=useState<FileRecord[]>([]),[search,setSearch]=useState(''),[expanded,setExpanded]=useState<string|null>(null),[loading,setLoading]=useState(true),[busy,setBusy]=useState(false),[error,setError]=useState('');
 async function load(){if(!window.mailFiles){setError('当前版本未提供文件接口。');setLoading(false);return;}try{setFiles(unwrap(await window.mailFiles.list()));}catch(e){setError(describe(e));}finally{setLoading(false);}}
 useEffect(()=>{void load();},[]);
 async function add(){setBusy(true);setError('');try{const imported=unwrap(await window.mailFiles!.importLocal());if(imported)await load();}catch(e){setError(describe(e));}finally{setBusy(false);}}
 const shown=files.filter(f=>f.name.toLowerCase().includes(search.toLowerCase()));
 return <section><div className="ag-page-actions"><label className="ag-search"><Search size={17}/><input aria-label="搜索资料" placeholder="搜索文件名称" value={search} onChange={e=>setSearch(e.target.value)}/></label><button className="ag-primary" disabled={busy||!window.mailFiles} onClick={()=>void add()}><Plus size={16}/>{busy?'正在导入…':'添加资料'}</button></div>
 <p className="ag-page-note">上传的资料和生成的文件保存在这里。助手可以按你的问题查找；无需先选择空间。</p>
 {error&&<p role="alert" className="ag-error">{error}</p>}{loading?<p role="status" className="ag-muted">正在读取资料…</p>:!shown.length?<div className="ag-empty-page"><FileText size={30}/><h3>{search?'没有匹配的资料':'把参考资料放在这里'}</h3><p>支持 PDF、Word、CSV 和文本文件，也可以在对话中上传。</p></div>:<div className="ag-library-list">{shown.map(file=><article key={file.id}><div className="ag-file-row"><FileText size={23}/><button onClick={()=>setExpanded(old=>old===file.id?null:file.id)}><strong>{file.name}</strong><small>{fileSize(file.byteSize)} · {file.source==='generated'?'助手生成':file.source==='mail'?'邮件附件':'本地导入'} · {shortDate(file.createdAt)}</small></button><button className="ag-secondary" onClick={()=>onUse(file)}>用于对话</button></div>{expanded===file.id&&<FileCard id={file.id} onAttach={onAttach}/>}</article>)}</div>}
 </section>;
}
