import React, {useEffect, useRef, useState} from 'react';
import type {Result} from '../shared/contracts';
import type {LocalMail, MailBinding} from '../shared/mail-workspace';
import type {ComposeReceipt, ComposeInput, MailComposeAPI,ReplyMailSource} from '../shared/mail-compose';
import {FilePicker,FileCard,fileSize} from './MailFiles';
import {MailAttachmentPreview} from './MailAttachmentPreview';

type Config = Awaited<ReturnType<MailComposeAPI['getConfig']>> extends Result<infer T> ? T : never;
const unwrap = <T,>(result:Result<T>):T => { if (!result.ok) throw Error(result.error); return result.data; };
const labels:Record<ComposeReceipt['status'],string> = {prepared:'已准备，尚未发送',sending:'发送处理中',sent:'邮件服务器已接受',failed:'发送失败',uncertain:'发送结果不确定'};
const accountLabel = (value:MailBinding) => value.state.imap?.email || value.state.account?.email || '尚未配置的邮箱';
const replyTargets=(source:ReplyMailSource)=>source.replyToAddresses?.length?source.replyToAddresses:source.fromAddress?[source.fromAddress]:[];
const defaultReplyTo=(source:ReplyMailSource)=>replyTargets(source).length===1?replyTargets(source)[0]:'';

/** Plain-text writing stays independent of transport. Only a prepared snapshot may be sent. */
export function MailComposePanel({accounts,replySource,attachmentRequest,initialDraft,onDirty,onBusy,onSent}:{accounts:MailBinding[];replySource?:ReplyMailSource|null;attachmentRequest?:{token:string;id:string};initialDraft?:ComposeInput & {id:string;revision?:number;lifecycleStatus?:string};onDirty?:(value:boolean)=>void;onBusy?:(value:boolean)=>void;onSent?:(receipt:ComposeReceipt)=>void|Promise<void>}) {
 const [origin,setOrigin]=useState<{originDraftId:string;expectedRevision:number}|null>(null);
 const [attachmentIds,setAttachmentIds]=useState<string[]>([]),[referenceFiles,setReferenceFiles]=useState(false);const attachmentToken=useRef('');const [filesBusy,setFilesBusy]=useState(false);
 const available = accounts.filter(value=>value.method==='imap');
 const [bindingId,setBindingId] = useState(replySource?.bindingId || available[0]?.id || '');
 const [to,setTo] = useState(''), [subject,setSubject] = useState(replySource ? 'Re: '+replySource.subject.replace(/^re:\s*/i,'') : ''), [body,setBody] = useState('');
 const [instructions,setInstructions] = useState(replySource ? '根据这封来信起草一封礼貌的回复。不要替我承诺未确认的事项，缺少的信息请用【待补充】标出。' : '');
 const [source,setSource] = useState(replySource ?? null);
 const [config,setConfig] = useState<Config|null>(null), [host,setHost] = useState(''), [port,setPort] = useState(465), [security,setSecurity] = useState<'ssl'|'starttls'>('ssl');
 const [busy,setBusy] = useState(false), [loadingConfig,setLoadingConfig] = useState(false), [error,setError] = useState(''), [notice,setNotice] = useState('');
 const [preview,setPreview] = useState<ComposeReceipt|null>(null), [recent,setRecent] = useState<ComposeReceipt[]>([]), [attempted,setAttempted] = useState(false), [clearConfirm,setClearConfirm] = useState(false);
 const mounted=useRef(true), lock=useRef(false), configGeneration=useRef(0), attemptedIds=useRef(new Set<string>());
 const accountConfig = accounts.find(value=>value.id===bindingId)?.state.imap;
 const accountSignature = JSON.stringify(accountConfig ? [accountConfig.email,accountConfig.username,accountConfig.host,accountConfig.port,accountConfig.security,accountConfig.hasSecret] : null);
 const smtpDirty = !!config && (host!==config.host || port!==config.port || security!==config.security);
 const terminal = attempted || (!!preview && preview.status!=='prepared');
 const dirty = (!terminal && !!(to || subject || body || instructions || attachmentIds.length)) || smtpDirty;
 useEffect(()=>{onDirty?.(dirty);},[dirty,onDirty]);
 useEffect(()=>{onBusy?.(busy || loadingConfig || filesBusy);},[busy,loadingConfig,filesBusy,onBusy]);
 useEffect(()=>{mounted.current=true;void refreshRecent();return()=>{mounted.current=false;configGeneration.current++;onBusy?.(false);onDirty?.(false);};},[]);
 useEffect(()=>{if(!accounts.some(value=>value.id===bindingId))setBindingId(available[0]?.id || '');},[accounts,bindingId]);
 useEffect(()=>{if(replySource){setSource(replySource);setBindingId(replySource.bindingId);setTo(defaultReplyTo(replySource));setSubject('Re: '+replySource.subject.replace(/^re:\s*/i,''));setBody('');setInstructions('根据这封来信起草一封礼貌的回复。不要替我承诺未确认的事项，缺少的信息请用【待补充】标出。');setAttachmentIds([]);setReferenceFiles(false);invalidate();}},[replySource]);
 useEffect(()=>{if(initialDraft){setOrigin(initialDraft.id.startsWith('manual-')?null:{originDraftId:initialDraft.id,expectedRevision:initialDraft.revision??1});setBindingId(initialDraft.bindingId||available[0]?.id||'');setTo(initialDraft.to||(replySource?defaultReplyTo(replySource):''));setSubject(initialDraft.subject);setBody(initialDraft.body);setAttachmentIds(initialDraft.attachmentIds??[]);setInstructions('');setReferenceFiles(false);invalidate();}},[initialDraft?.id]);
 useEffect(()=>{if(!attachmentRequest||attachmentToken.current===attachmentRequest.token)return;attachmentToken.current=attachmentRequest.token;if(terminal&&(!replySource||replySource===source)){setError('请先点击“新写一封”，再从文件列表添加附件；原发送记录不会改变。');return;}if(attachmentIds.length>=6&&!attachmentIds.includes(attachmentRequest.id)){setError('一封邮件最多 6 个附件，请先移除一个再添加。');return;}setAttachmentIds(current=>[...new Set([...current,attachmentRequest.id])]);invalidate();setNotice('文件已加入这封邮件的附件，请在发送预览中核对。');},[attachmentRequest]);
 useEffect(()=>{
  const generation=++configGeneration.current;setConfig(null);setHost('');setPort(465);setSecurity('ssl');
  if(!bindingId){setLoadingConfig(false);return;}
  setLoadingConfig(true);
  void window.mailCompose.getConfig(bindingId).then(result=>{if(!mounted.current || generation!==configGeneration.current)return;const value=unwrap(result);applyConfig(value);}).catch(e=>{if(mounted.current && generation===configGeneration.current)setError(message(e));}).finally(()=>{if(mounted.current && generation===configGeneration.current)setLoadingConfig(false);});
 },[bindingId,accountSignature]);
 function message(e:unknown){return e instanceof Error ? e.message : '操作未完成，请检查连接。';}
 function applyConfig(value:Config){setConfig(value);setHost(value.host);setPort(value.port);setSecurity(value.security);}
 function invalidate(){setPreview(null);setAttempted(false);setError('');setNotice('');}
 async function refreshRecent(){try{const values=unwrap(await window.mailCompose.listRecent());if(mounted.current){setRecent(values);setPreview(current=>{if(!current || !attemptedIds.current.has(current.id))return current;return values.find(value=>value.id===current.id&&value.status!=='prepared') ?? current;});}}catch{/* History is secondary; preserve the editable draft if unavailable. */}}
 async function run(work:()=>Promise<void>){if(lock.current)return;lock.current=true;setBusy(true);setError('');setNotice('');try{await work();}catch(e){if(mounted.current)setError(message(e));}finally{lock.current=false;if(mounted.current)setBusy(false);}}
 function clear(){setOrigin(null);setTo('');setSubject('');setBody('');setInstructions('');setSource(null);setAttachmentIds([]);setReferenceFiles(false);invalidate();setClearConfirm(false);}
 async function generate(){
  const settings=unwrap(await window.desktop.getSettings());
  if(!settings.hasApiKey)throw Error('请先在主窗口“设置 → AI 服务”保存 OpenRouter API Key，再回来起草。手动写信和复制正文仍可使用。');
  const value=unwrap(await window.mailCompose.generate({instructions,subject,body,...(source?{replyToLocalId:source.localId}:{}),...(referenceFiles&&attachmentIds.length?{fileIds:attachmentIds}:{}),target:{baseUrl:settings.baseUrl,model:settings.model}}));
  if(mounted.current){setSubject(value.subject);setBody(value.body);setPreview(null);setNotice('草稿已生成，请核对事实并填写收件人。AI 不会自动发送。');}
 }
 async function prepare(){
  const value=unwrap(await window.mailCompose.prepare({bindingId,to,subject,body,...(origin??{}),...(source?{replyToLocalId:source.localId}:{}),...(attachmentIds.length?{attachmentIds}:{})}));
  if(mounted.current){setPreview(value);setAttempted(false);}await refreshRecent();
 }
 async function send(){
  if(!preview || preview.status!=='prepared' || attemptedIds.current.has(preview.id))return;
  const id=preview.id;attemptedIds.current.add(id);setAttempted(true);
  let accepted:ComposeReceipt|undefined;
  try{const value=unwrap(await window.mailCompose.send(id));if(mounted.current)setPreview(value);if(value.status==='sent')accepted=value;}
  catch(e){if(mounted.current){setPreview(current=>current?{...current,status:'uncertain',detail:'客户端未确认发送结果，请检查发送记录与邮箱。'}:current);setError('发送状态暂时无法确认。请刷新发送记录并检查邮箱，勿立即重发。'+message(e));}}
  finally{await refreshRecent();}
  if(accepted&&onSent){try{await onSent(accepted);}catch{if(mounted.current)setError('邮件服务器已接受邮件，但待确认列表更新失败。请刷新列表，不要重复发送。');}}
 }
 const canPrepare = !!bindingId && !!config?.configured && !!config?.hasSecret && !smtpDirty && !!to.trim() && !!subject.trim() && !!body.trim() && !terminal;
 return <section className="mc-panel" aria-label="邮件写作" aria-busy={busy || loadingConfig}>
  <header className="mc-intro"><div><small>WRITE · 先写好，再发送</small><h3>AI 邮件写作</h3><p>起草新邮件，或根据来信写回复。收件人、主题和正文都由你核对。</p></div><button disabled={busy} onClick={()=>dirty?setClearConfirm(true):clear()}>{terminal?'新写一封':'清空草稿'}</button></header>
  {error&&<p className="mw-error" role="alert">{error}</p>}{notice&&<p className="mw-notice" role="status">{notice}</p>}
  {busy&&<p role="status" className="mc-status">正在处理，请稍候……</p>}
  {clearConfirm&&<div className="mc-clear" role="alertdialog" aria-label="清空邮件草稿确认"><p>清空这封邮件的输入？已保存的发送记录会保留。</p><button onClick={clear}>确认清空草稿</button><button onClick={()=>setClearConfirm(false)}>继续编辑</button></div>}
  <div className="mc-columns">
   <div className="mc-editor">
    {source&&<aside className="mc-source"><strong>回复参考：{source.subject}</strong><p>原发件人：{source.sender}。</p><p>{source.replyToAddresses?.length?'原邮件指定 Reply-To：'+source.replyToAddresses.join('、'):'回复地址：'+(source.fromAddress||'未提供结构化地址，请核对后填写')}。发送预览会再次校验。</p>{replyTargets(source).length>1&&<label>选择原邮件的回复地址<select aria-label="选择回复地址" disabled={busy||terminal} value={to} onChange={e=>{setTo(e.target.value);invalidate();}}><option value="">请选择一个地址</option>{replyTargets(source).map(address=><option key={address} value={address}>{address}</option>)}</select></label>}<p>AI 起草会读取这封来信的本地正文；请先确认内容完整。</p></aside>}
    <label>写信要求<textarea rows={3} maxLength={4000} disabled={busy||terminal} value={instructions} placeholder="例如：给老师写一封英文邮件，询问作业第 3 题是否允许使用 Python，语气礼貌、简洁。" onChange={e=>{setInstructions(e.target.value);invalidate();}}/></label>
    <div className="mw-actions"><button className="mw-primary" disabled={busy||filesBusy||terminal||(!instructions.trim()&&!body.trim())} onClick={()=>void run(generate)}>AI 起草／润色</button><small>生成后可继续编辑，不会改变收件人。</small></div>
    <label>收件人（一个邮箱地址）<input type="email" autoComplete="off" maxLength={320} disabled={busy||terminal} value={to} placeholder="teacher@example.com" onChange={e=>{setTo(e.target.value);invalidate();}}/></label>
    <label>邮件主题<input maxLength={500} disabled={busy||terminal} value={subject} onChange={e=>{setSubject(e.target.value);invalidate();}}/></label>
    <label>邮件正文<textarea rows={12} maxLength={20000} disabled={busy||terminal} value={body} placeholder="可以直接写信，也可以让 AI 根据要求生成初稿。" onChange={e=>{setBody(e.target.value);invalidate();}}/></label>
    {window.mailFiles&&<section className="mc-attachments" aria-label="邮件附件"><FilePicker label="待发送附件" ids={attachmentIds} disabled={busy||terminal} onBusy={setFilesBusy} onChange={ids=>{setAttachmentIds(ids);invalidate();}}/>{attachmentIds.map(id=><FileCard key={id} id={id} disabled={busy||terminal}/>)}<label className="mf-mode"><input type="checkbox" checked={referenceFiles} disabled={busy||terminal||!attachmentIds.length} onChange={e=>{setReferenceFiles(e.target.checked);invalidate();}}/>AI 起草时参考附件内容</label><p>{referenceFiles?'点击 AI 起草会将这些附件提取的文字发送给已配置的模型。':'附件仅用于邮件发送；AI 起草暂不读取其内容。'} 最多 6 个附件，合计不超过 10 MB。</p></section>}
    <div className="mw-actions"><button disabled={busy||!body} onClick={()=>void run(async()=>{if(!navigator.clipboard)throw Error('当前环境不能自动复制，请选中邮件正文后手动复制。');await navigator.clipboard.writeText(body);setNotice('正文已复制。');})}>复制正文</button><button className="mw-primary" disabled={busy||filesBusy||loadingConfig||!canPrepare} onClick={()=>void run(prepare)}>预览并准备发送</button></div>
    <p className="mc-hint">当前支持单个收件人、纯文本正文和文件附件。未发送的输入保留在此窗口；准备发送后会保存正文和附件的固定快照。</p>
   </div>
   <aside className="mc-sidebar">
    <section className="mc-card"><h3>发件邮箱</h3><label>选择发件账号<select value={bindingId} disabled={busy||terminal||loadingConfig} onChange={e=>{setBindingId(e.target.value);invalidate();}}><option value="">选择已添加的 IMAP 邮箱</option>{available.map(value=><option key={value.id} value={value.id}>{accountLabel(value)}</option>)}</select></label>
    {!available.length?<p>先返回邮件列表，添加 QQ 或网易等 IMAP 邮箱并保存授权码。也可以现在起草，复制后到网页邮箱发送。</p>:<p>SMTP 发送使用该邮箱已保存的授权码。请在邮箱网页中开启 IMAP / SMTP 服务。</p>}
    {config&&!config.hasSecret&&<p className="mc-warning">尚未保存邮箱授权码，请返回邮箱连接设置填写。</p>}
    {bindingId&&<details open={!config?.configured}><summary>SMTP 发送设置{config?.configured?' · 已保存':''}</summary><div className="mc-smtp"><div className="mw-actions"><button disabled={busy||terminal||loadingConfig} onClick={()=>{setHost('smtp.qq.com');setPort(465);setSecurity('ssl');invalidate();}}>QQ 邮箱</button><button disabled={busy||terminal||loadingConfig} onClick={()=>{setHost('smtp.163.com');setPort(465);setSecurity('ssl');invalidate();}}>163 邮箱</button></div><label>SMTP 服务器<input disabled={busy||terminal||loadingConfig} value={host} placeholder="smtp.qq.com" onChange={e=>{setHost(e.target.value);invalidate();}}/></label><div className="mc-smtp-row"><label>SMTP 端口<input type="number" min="1" max="65535" disabled={busy||terminal||loadingConfig} value={port} onChange={e=>{setPort(Number(e.target.value));invalidate();}}/></label><label>SMTP 加密方式<select disabled={busy||terminal||loadingConfig} value={security} onChange={e=>{setSecurity(e.target.value as 'ssl'|'starttls');setPort(e.target.value==='ssl'?465:587);invalidate();}}><option value="ssl">SSL / TLS</option><option value="starttls">STARTTLS</option></select></label></div><button disabled={busy||terminal||loadingConfig||!host.trim()||!config?.hasSecret} onClick={()=>void run(async()=>{applyConfig(unwrap(await window.mailCompose.saveConfig(bindingId,{host,port,security})));setPreview(null);setNotice('SMTP 设置已保存。实际连接结果在发送时检查。');})}>保存 SMTP 设置</button></div></details>}
    </section>
    {preview&&<section className={'mc-card mc-preview '+preview.status} aria-label="发送前预览"><h3>{labels[preview.status]}</h3><dl><dt>发件人</dt><dd>{preview.from}</dd><dt>收件人</dt><dd>{preview.to}</dd><dt>主题</dt><dd>{preview.subject}</dd></dl><pre>{preview.body}</pre>{preview.replyContext&&<section className="mc-thread" aria-label="固定回复线程"><h4>本次回复关联</h4><p>所属邮箱：{preview.replyContext.accountEmail}</p><p>回复地址：{preview.replyContext.to}</p>{preview.replyContext.threadAvailable?<details><summary>查看固定线程标识</summary><p>In-Reply-To：<code>{preview.replyContext.inReplyTo}</code></p><p>References：<code>{preview.replyContext.references.join(' ')}</code></p></details>:<p>原邮件缺少 Message-ID，本次不能建立协议线程关联。</p>}<small>确认发送使用以上固定快照。</small></section>}
     <MailAttachmentPreview attachments={preview.attachments}/>{preview.detail&&<p>{preview.detail}</p>}
     {preview.status==='prepared'&&!attempted?<><p>请核对上面的完整内容。点击下面按钮才会发送。</p><button className="mw-primary" disabled={busy} onClick={()=>void run(send)}>确认发送这封邮件</button><button disabled={busy} onClick={()=>setPreview(null)}>取消预览，继续编辑</button></>:<p className="mc-warning">{preview.status==='sent'?'服务器已接受邮件，不代表对方已收到或阅读。':'请检查下方发送记录与邮箱中的收件情况，确认后再决定是否另写一封。此快照不会自动重发。'}</p>}
    </section>}
    <section className="mc-card mc-history"><div className="mc-history-heading"><h3>最近发送记录</h3><button disabled={busy} onClick={()=>void run(refreshRecent)}>刷新发送记录</button></div>{!recent.length?<p>尚无记录。预览和发送结果会保存在本机。</p>:recent.map(item=><details key={item.id}><summary><span>{item.subject}</span><em>{labels[item.status]}</em></summary><p>{item.from} → {item.to}</p><p>{new Date(item.createdAt).toLocaleString('zh-CN')}</p><p>{item.detail}</p><pre>{item.body}</pre>{item.attachments?.map(file=><p key={file.id}>{file.name} · {fileSize(file.byteSize)} · {file.sha256.slice(0,10)}</p>)}</details>)}</section>
   </aside>
  </div>
 </section>;
}
