import React,{useEffect,useState} from 'react';
import type {MailJob,MailJobState} from '../shared/mail-operations';
import type {MailImportReport,MailImportFailure} from '../shared/mail-workspace';
export const jobStateText:Record<MailJobState,string>={queued:'排队中',running:'处理中',pausing:'正在暂停',paused:'已暂停',canceling:'正在取消',canceled:'已取消',completed:'已完成',failed:'处理失败'};
export function useMailJobs(){const [jobs,setJobs]=useState<MailJob[]>([]);useEffect(()=>{if(!window.mailOperations)return;let live=true;const refresh=()=>void window.mailOperations.listJobs().then(r=>{if(live&&r.ok)setJobs(r.data);}).catch(()=>{});refresh();const timer=setInterval(refresh,1000);const stop=window.widget?.onChanged(refresh);return()=>{live=false;clearInterval(timer);stop?.();};},[]);return jobs;}
const activeStates:MailJob['state'][]=['queued','running','pausing','paused','canceling'];
const finishedStates:MailJob['state'][]=['completed','failed','canceled'];
const stageText:Record<string,string>={list:'读取清单',identity:'核对邮件身份',transfer:'下载正文',structure:'正文结构',parse:'解析正文',save:'本地保存'};
function detail(failure:MailImportFailure){return [failure.mailboxEmail,failure.sender,new Date(failure.receivedAt).toLocaleString('zh-CN')].filter(Boolean).join(' · ');}
function Report({report,jobId,onRetry}:{report:MailImportReport;jobId:string;onRetry:(work:()=>Promise<unknown>)=>void}){
 const failures=report.failures??[],mailboxErrors=report.mailboxErrors??[],pending=report.pendingLocalIds??[];
 return <div className="mw-job-report">
  <p className="mw-job-counts">新增 {report.added} · 跳过 {report.skipped} · 失败 {report.failed} · 正文已读 {report.bodyRead??0}{report.bodyPending?<span> · 正文待读取 {report.bodyPending}</span>:null}{report.limited?' · 已达读取上限':''}</p>
  {mailboxErrors.map(error=><p className="mw-job-error" key={error.bindingId+error.code}><strong>{error.mailboxEmail||error.bindingId}</strong> · {stageText[error.stage]??error.stage} · {error.code}：{error.reason}</p>)}
  {failures.length>0&&<details className="mw-job-failures"><summary>失败明细（{failures.length} 封）</summary>
   <ul>{failures.map(failure=><li key={failure.key}><div><strong>{failure.subject||'（无主题）'}</strong><small>{detail(failure)}</small><small>阶段：{stageText[failure.stage]??failure.stage} · {failure.code}</small><p>{failure.reason}</p></div><button disabled={!failure.retryable} onClick={()=>onRetry(()=>window.mailOperations.retryImportFailures({jobId,keys:[failure.key]}))}>重试这一封</button></li>)}</ul>
   <button onClick={()=>onRetry(()=>window.mailOperations.retryImportFailures({jobId}))}>重试全部失败邮件（{failures.filter(f=>f.retryable).length}）</button>
  </details>}
  {pending.length>0&&<button onClick={()=>onRetry(()=>window.mailOperations.retryBodies({localIds:pending}))}>补读正文（{pending.length} 封）</button>}
  {!failures.length&&!mailboxErrors.length&&report.errors.length>0&&<p className="mw-job-error">{report.errors.slice(0,3).join(' ')}</p>}
 </div>;
}
function JobCard({job,onConversation,onDismiss,onRetry}:{job:MailJob;onConversation?(id:string):void;onDismiss?(job:MailJob):void;onRetry:(work:()=>Promise<unknown>)=>void}){
 const [cancel,setCancel]=useState(false),[error,setError]=useState(''),[busy,setBusy]=useState(false);
 const active=activeStates.includes(job.state),finished=finishedStates.includes(job.state);
 async function act(work:()=>Promise<{ok:boolean;error?:string}>){setBusy(true);setError('');try{const result=await work();if(!result.ok)throw Error(result.error??'无法更改任务状态');}catch(e){setError(e instanceof Error?e.message:'无法更改任务状态');}finally{setBusy(false);setCancel(false);}}
 return <div className={'mw-job '+('mw-job-'+job.state)}>
  <p><strong>{job.label}</strong> · {jobStateText[job.state]} · {job.kind==='analysis'?`${job.messageCount??'—'} 封邮件 · 分析批次 ${job.done} / ${job.total}`:job.kind==='import'?`邮件进度 ${job.done} / ${job.total} 封`:`对话轮次 ${job.done} / ${job.total}`}{job.error&&<span> · {job.error}</span>}</p>
  <progress max={Math.max(job.total,1)} value={job.done}/>
  {error&&<p role="alert" className="mw-job-error">{error}</p>}
  {job.report&&job.kind==='import'&&<Report report={job.report} jobId={job.id} onRetry={onRetry}/>}
  <div className="mw-job-actions">
   {(job.state==='running'||job.state==='queued'||job.state==='pausing')&&<button disabled={busy} onClick={()=>void act(()=>window.mailOperations.pauseJob(job.id))}>暂停</button>}
   {(job.state==='paused'||job.state==='failed')&&<button disabled={busy} onClick={()=>void act(()=>window.mailOperations.resumeJob(job.id))}>继续处理</button>}
   {active&&!cancel&&<button disabled={busy} className="mw-job-cancel" onClick={()=>setCancel(true)}>取消任务</button>}
   {cancel&&<span className="mw-job-confirm-cancel"><button disabled={busy} onClick={()=>void act(()=>window.mailOperations.cancelJob(job.id))}>确认取消本次任务</button><button disabled={busy} onClick={()=>setCancel(false)}>继续运行</button></span>}
   {job.conversationId&&onConversation&&<button onClick={()=>onConversation(job.conversationId!)}>查看对话与结果</button>}
   {finished&&onDismiss&&<button aria-label={`关闭任务提示 ${job.label}`} title="关闭后不再显示此任务的完成提示" onClick={()=>onDismiss(job)}>×</button>}
  </div>
 </div>;
}
export function MailJobs({onConversation}:{onConversation(id:string):void}){
 const jobs=useMailJobs(),[error,setError]=useState(''),[dismissed,setDismissed]=useState<Set<string>>(()=>new Set());
 async function dismiss(job:MailJob){setDismissed(current=>{const next=new Set(current);next.add(job.id);return next;});setError('');try{const r=await window.mailOperations.acknowledgeJob(job.id);if(!r.ok)throw Error(r.error);}catch(e){setError(e instanceof Error?e.message:'无法关闭任务提示');}}
 async function retry(work:()=>Promise<unknown>){setError('');try{const result=await work() as {ok:boolean;error?:string};if(!result.ok)throw Error(result.error??'无法开始重试');}catch(e){setError(e instanceof Error?e.message:'无法开始重试');}}
 // 进行中的任务始终显示；关闭失败提示后仍保留折叠的恢复入口。
 const visible=jobs.filter(job=>activeStates.includes(job.state)||finishedStates.includes(job.state)&&!job.notificationAcknowledged&&!dismissed.has(job.id));
 const hiddenFailures=jobs.filter(job=>job.state==='failed'&&(job.notificationAcknowledged||dismissed.has(job.id)));
 if(!visible.length&&!hiddenFailures.length)return null;
 return <section aria-label="后台邮件任务" className="mw-jobs">{error&&<p role="alert">{error}</p>}{visible.map(job=><JobCard key={job.id} job={job} onConversation={onConversation} onDismiss={dismiss} onRetry={work=>void retry(work)}/>)}{hiddenFailures.length>0&&<details className="mw-job-history"><summary>已收起的失败任务（{hiddenFailures.length}）</summary>{hiddenFailures.map(job=><JobCard key={job.id} job={job} onConversation={onConversation} onRetry={work=>void retry(work)}/>)}</details>}</section>;
}
/** 任务记录始终可查看，包括已经关闭提示的历史导入；失败明细和重试入口也保留。 */
export function MailJobHistory(){
 const jobs=useMailJobs(),[error,setError]=useState('');
 const imports=jobs.filter(job=>job.kind==='import').slice(0,20);
 if(!imports.length)return null;
 async function retry(work:()=>Promise<{ok:boolean;error?:string}>){setError('');try{const result=await work();if(!result.ok)throw Error(result.error??'无法开始重试');}catch(e){setError(e instanceof Error?e.message:'无法开始重试');}}
 return <details className="mw-job-history"><summary>导入任务记录（{imports.length}）</summary>{error&&<p role="alert" className="mw-job-error">{error}</p>}{imports.map(job=><article key={job.id}><p><strong>{job.label}</strong> · {jobStateText[job.state]} · {new Date(job.createdAt).toLocaleString('zh-CN')}</p>{job.report&&<Report report={job.report} jobId={job.id} onRetry={work=>void retry(work as ()=>Promise<{ok:boolean;error?:string}>)}/>}</article>)}</details>;
}
