import React, {useEffect, useRef, useState} from 'react';
import type {Result} from '../shared/contracts';
import type {MailAnalysis, MailBinding} from '../shared/mail-workspace';
import {
  defaultMailAutomationConfig,
  type MailAutomationAPI, type MailAutomationBackgroundStatus, type MailAutomationCategory,
  type MailAutomationConfig, type MailAutomationConfigInput, type MailAutomationItem,
  type MailAutomationResultPage, type MailAutomationResultQuery, type MailAutomationStatus,
} from '../shared/mail-automation';

const categories:Record<MailAutomationCategory,string> = {
  action:'待处理', important:'重要知会', normal:'普通', spam:'疑似垃圾', review:'待核实',
};
const categoryKeys = Object.keys(categories) as MailAutomationCategory[];
const stateText = {idle:'等待下次整理', queued:'已排队', running:'正在整理', failed:'整理失败'};
const runStateText = {queued:'排队中', running:'处理中', completed:'已完成', failed:'处理失败'};
const triggerText = {schedule:'定时整理', manual:'手动整理', retry:'重试'};
const PAGE_SIZE = 20;
const unwrap = <T,>(result:Result<T>):T => {if (!result.ok) throw Error(result.error); return result.data;};
const message = (error:unknown) => error instanceof Error ? error.message : '操作未完成，请重试。';
type Form = Omit<MailAutomationConfigInput,'importantSenders'|'mutedSenders'> & {importantText:string; mutedText:string};
const formOf = (config:MailAutomationConfigInput):Form => ({
  enabled:config.enabled, bindingIds:[...config.bindingIds], times:[config.times[0]??'09:00',config.times[1]??'18:00'],
  timeZone:config.timeZone, lookbackDays:config.lookbackDays, maxMessagesPerRun:config.maxMessagesPerRun,
  importantText:config.importantSenders.join('\n'), mutedText:config.mutedSenders.join('\n'),
});
const inputOf = (form:Form):MailAutomationConfigInput => ({
  enabled:form.enabled, bindingIds:[...form.bindingIds], times:[...form.times], timeZone:form.timeZone,
  lookbackDays:form.lookbackDays, maxMessagesPerRun:form.maxMessagesPerRun,
  importantSenders:form.importantText.split(/\r?\n/).map(value=>value.trim()).filter(Boolean),
  mutedSenders:form.mutedText.split(/\r?\n/).map(value=>value.trim()).filter(Boolean),
});
function timeLabel(value:string|null|undefined, timeZone:string) {
  if (!value) return '—';
  try {return new Intl.DateTimeFormat('zh-CN',{timeZone,month:'numeric',day:'numeric',hour:'2-digit',minute:'2-digit',hour12:false}).format(new Date(value));}
  catch {return '时间暂不可用';}
}

export interface MailAutomationPanelProps {
  accounts:MailBinding[];
  onReview(analysis:MailAnalysis):void;
  onOpenMail?(id:string):void;
  onDirty?(dirty:boolean):void;
}

/** Poll status/results independently: background refreshes never replace unsaved settings. */
export function MailAutomationPanel({accounts,onReview,onOpenMail,onDirty}:MailAutomationPanelProps) {
  const api = (window as Window & {mailAutomation?:MailAutomationAPI}).mailAutomation;
  const [form,setForm] = useState<Form>(()=>formOf(defaultMailAutomationConfig));
  const [saved,setSaved] = useState<MailAutomationConfig|null>(null);
  const [baseline,setBaseline] = useState('');
  const [loading,setLoading] = useState(true), [loadError,setLoadError] = useState('');
  const [status,setStatus] = useState<MailAutomationStatus|null>(null), [statusError,setStatusError] = useState('');
  const [background,setBackground] = useState<MailAutomationBackgroundStatus|null>(null), [backgroundError,setBackgroundError] = useState('');
  const [category,setCategory] = useState<MailAutomationCategory|''>(''), [offset,setOffset] = useState(0);
  const [page,setPage] = useState<MailAutomationResultPage>({items:[],total:0,counts:{}});
  const [resultsLoading,setResultsLoading] = useState(true), [resultsError,setResultsError] = useState('');
  const [busy,setBusy] = useState<'save'|'run'|'review'|'correct'|null>(null);
  const [actionError,setActionError] = useState(''), [notice,setNotice] = useState('');
  const live = useRef(false), actionLock = useRef(false), changingCategory = useRef(false);
  const configSequence = useRef(0), statusSequence = useRef(0), resultSequence = useRef(0);
  const query = useRef<MailAutomationResultQuery>({offset:0,limit:PAGE_SIZE});
  const dirtyCallback = useRef(onDirty); dirtyCallback.current = onDirty;
  const dirty = !!saved && JSON.stringify(form)!==baseline;
  const connected = accounts.filter(account=>account.method==='imap' && !!account.state.account);
  const unavailable = form.bindingIds.filter(id=>!connected.some(account=>account.id===id));
  const inProgress = status?.state==='queued' || status?.state==='running';
  const zone = saved?.timeZone ?? 'Asia/Hong_Kong';
  query.current = {...(category ? {category} : {}), offset, limit:PAGE_SIZE};

  useEffect(()=>{dirtyCallback.current?.(dirty);},[dirty]);
  useEffect(()=>()=>{dirtyCallback.current?.(false);},[]);

  async function loadConfig() {
    if (!api) return;
    const sequence=++configSequence.current;
    setLoading(true); setLoadError('');
    try {
      const value=unwrap(await api.getConfig());
      if (!live.current||sequence!==configSequence.current) return;
      const next=formOf(value);setSaved(value);setForm(next);setBaseline(JSON.stringify(next));
    } catch(error) {if(live.current&&sequence===configSequence.current)setLoadError(message(error));}
    finally {if(live.current&&sequence===configSequence.current)setLoading(false);}
  }
  async function refreshStatus() {
    if (!api) return;
    const sequence=++statusSequence.current;
    try {
      const value=unwrap(await api.getStatus());
      if(live.current&&sequence===statusSequence.current){setStatus(value);setStatusError('');}
    } catch(error) {if(live.current&&sequence===statusSequence.current)setStatusError(message(error));}
  }
  async function refreshBackground() {
    if (!api?.getBackgroundStatus) return;
    try {const value=unwrap(await api.getBackgroundStatus());if(live.current){setBackground(value);setBackgroundError('');}}
    catch(error){if(live.current)setBackgroundError(message(error));}
  }
  async function refreshResults(showLoading=false) {
    if (!api||changingCategory.current) return;
    const sequence=++resultSequence.current, requested={...query.current};
    if(showLoading)setResultsLoading(true);
    try {
      const value=unwrap(await api.listResults(requested));
      if(!live.current||sequence!==resultSequence.current)return;
      if((requested.offset??0)>0 && (requested.offset??0)>=value.total) {
        setOffset(Math.max(0,(Math.ceil(value.total/PAGE_SIZE)-1)*PAGE_SIZE));
      }
      setPage(value);setResultsError('');
    } catch(error) {if(live.current&&sequence===resultSequence.current)setResultsError(message(error));}
    finally {if(live.current&&sequence===resultSequence.current)setResultsLoading(false);}
  }

  useEffect(()=>{
    live.current=true;
    if(!api){setLoading(false);setResultsLoading(false);return()=>{live.current=false;};}
    void loadConfig();void refreshStatus();void refreshBackground();
    let polls=0;
    const timer=setInterval(()=>{void refreshStatus();void refreshResults();if(++polls%5===0)void refreshBackground();},3000);
    return()=>{live.current=false;clearInterval(timer);configSequence.current++;statusSequence.current++;resultSequence.current++;};
  },[api]);
  useEffect(()=>{void refreshResults(true);},[api,category,offset]);

  function change<K extends keyof Form>(key:K,value:Form[K]) {setForm(current=>({...current,[key]:value}));setNotice('');}
  async function save(event:React.FormEvent) {
    event.preventDefault();
    if(!api||actionLock.current)return;
    actionLock.current=true;setBusy('save');setActionError('');setNotice('');
    try {
      const value=unwrap(await api.saveConfig(inputOf(form)));
      if(!live.current)return;
      const next=formOf(value);setSaved(value);setForm(next);setBaseline(JSON.stringify(next));
      setNotice(value.enabled?'已保存并启用定时整理。':'已保存并停用定时整理。');
      void refreshStatus();void refreshBackground();
    } catch(error){
      if(!live.current)return;
      setActionError(message(error));
      // Applying the system schedule may fail after the backend persisted a
      // disabled rollback. Reconcile its state while preserving the user's input.
      try {
        const actual=unwrap(await api.getConfig());
        if(live.current){setSaved(actual);setBaseline(JSON.stringify(formOf(actual)));setLoadError('');}
      } catch {if(live.current)setLoadError('保存失败后无法确认当前设置，请重新加载设置。');}
      void refreshStatus();void refreshBackground();
    }
    finally{actionLock.current=false;if(live.current)setBusy(null);}
  }
  async function runNow() {
    if(!api||actionLock.current||inProgress||dirty)return;
    actionLock.current=true;statusSequence.current++;setBusy('run');setActionError('');setNotice('');
    try {
      unwrap(await api.runNow());
      if(!live.current)return;
      setStatus(current=>({state:'queued',lastRun:current?.lastRun??null,nextRunAt:current?.nextRunAt??saved?.nextRunAt??null}));
      setNotice('已加入整理队列，结果会自动更新。');
      void refreshStatus();void refreshResults();
    } catch(error){if(live.current)setActionError(message(error));}
    finally{actionLock.current=false;if(live.current)setBusy(null);}
  }
  async function review(item:MailAutomationItem) {
    if(!item.analysisId||actionLock.current)return;
    actionLock.current=true;setBusy('review');setActionError('');
    try {const value=unwrap(await window.mailWorkspace.getAnalysis(item.analysisId));if(live.current)onReview(value);}
    catch(error){if(live.current)setActionError(message(error));}
    finally{actionLock.current=false;if(live.current)setBusy(null);}
  }
  async function correct(item:MailAutomationItem,next:MailAutomationCategory) {
    if(!api||actionLock.current||item.category===next)return;
    actionLock.current=true;changingCategory.current=true;resultSequence.current++;
    setBusy('correct');setActionError('');setNotice('');
    try {
      const updated=unwrap(await api.correctCategory(item.localId,next));
      if(!live.current)return;
      setPage(current=>({
        ...current,
        items:current.items.map(row=>row.localId===updated.localId?updated:row).filter(row=>!category||row.category===category),
        total:category&&updated.category!==category?Math.max(0,current.total-1):current.total,
        counts:item.category===updated.category?current.counts:{...current.counts,[item.category]:Math.max(0,(current.counts[item.category]??0)-1),[updated.category]:(current.counts[updated.category]??0)+1},
      }));
      setNotice('分类已更正，后续整理会保留你的选择。');
    } catch(error){if(live.current)setActionError(message(error));}
    finally {
      actionLock.current=false;changingCategory.current=false;
      if(live.current){setBusy(null);void refreshResults();}
    }
  }

  if(!api)return <section className="ma-panel" aria-label="自动整理邮件"><h3>自动整理邮件</h3><p>此版本暂未提供自动整理接口，请更新应用后重试。</p></section>;
  const allCount=categoryKeys.reduce((sum,key)=>sum+(page.counts[key]??0),0);
  const nextRunAt=status?status.nextRunAt:saved?.nextRunAt;
  return <section className="ma-panel" aria-label="自动整理邮件">
    <header className="ma-heading"><div><small>MAIL · 自动整理</small><h3>让待处理的来信自己浮出来</h3><p>每天按计划检查收件箱，把邮件分好类，再由你确认待办。</p></div><span className={'ma-enabled '+(saved?.enabled?'is-enabled':'')}>{saved?.enabled?'定时整理已启用':'定时整理未启用'}</span></header>
    <div className="ma-layout">
      <aside className="ma-settings">
        {loadError&&<div role="alert" className="ma-error"><p>{loadError}</p><button onClick={()=>void loadConfig()} disabled={loading}>重新加载设置</button></div>}
        {loading&&<p role="status">正在读取整理设置…</p>}
        <form onSubmit={event=>void save(event)} aria-label="自动整理设置">
          <fieldset disabled={loading||!saved||busy==='save'}>
            <legend>收取与分类计划</legend>
            <label className="ma-check"><input type="checkbox" checked={form.enabled} onChange={event=>change('enabled',event.target.checked)}/>启用定时整理</label>
            <fieldset className="ma-accounts"><legend>选择已连接的 IMAP 邮箱</legend>
              {connected.map(account=><label className="ma-check" key={account.id}><input type="checkbox" checked={form.bindingIds.includes(account.id)} onChange={event=>change('bindingIds',event.target.checked?[...form.bindingIds,account.id]:form.bindingIds.filter(id=>id!==account.id))}/>{account.state.account!.email}</label>)}
              {!connected.length&&<p>请先在邮箱设置连接一个 IMAP 邮箱。</p>}
              {!!unavailable.length&&<div className="ma-warning"><p>已保存的选择中有 {unavailable.length} 个邮箱目前未连接或不支持此功能。</p><button type="button" onClick={()=>change('bindingIds',form.bindingIds.filter(id=>!unavailable.includes(id)))}>移除不可用账号</button></div>}
            </fieldset>
            <div className="ma-times"><label>第一次整理时间<input type="time" required value={form.times[0]} onChange={event=>change('times',[event.target.value,form.times[1]])}/></label><label>第二次整理时间<input type="time" required value={form.times[1]} onChange={event=>change('times',[form.times[0],event.target.value])}/></label></div>
            <p className="ma-help">时区：{form.timeZone==='Asia/Hong_Kong'?'香港时间（Asia/Hong_Kong）':form.timeZone}。错过的整理会在本机恢复运行后检查并补跑。</p>
            <label>首次读取范围<select value={form.lookbackDays} onChange={event=>change('lookbackDays',Number(event.target.value))}><option value={1}>最近 1 天</option><option value={7}>最近 7 天</option><option value={30}>最近 30 天</option></select></label>
            <label>每次最多分析<select value={form.maxMessagesPerRun} onChange={event=>change('maxMessagesPerRun',Number(event.target.value))}><option value={10}>10 封</option><option value={20}>20 封</option><option value={50}>50 封</option></select></label>
            <p className="ma-help">只读收件箱：首次按所选天数读取，后续增量整理新邮件。每次分析数量用于控制模型用量。</p>
            <label>重要发件人（每行一个完整邮箱地址）<textarea rows={3} placeholder="teacher@example.com" value={form.importantText} onChange={event=>change('importantText',event.target.value)} spellCheck={false}/></label>
            <label>忽略发件人（每行一个完整邮箱地址）<textarea rows={3} placeholder="newsletter@example.com" value={form.mutedText} onChange={event=>change('mutedText',event.target.value)} spellCheck={false}/></label>
            <p className="ma-help">规则按完整邮箱地址匹配。重要发件人优先关注；忽略发件人的邮件归为“疑似垃圾”并跳过 AI，原邮件仍保留。</p>
          </fieldset>
          <div className="ma-save"><button className="primary" type="submit" disabled={loading||!saved||!!busy}>{busy==='save'?'正在保存…':form.enabled?'保存并启用':'保存并停用'}</button>{dirty&&<><span>设置尚未保存</span><button type="button" disabled={!!busy} onClick={()=>{if(saved){setForm(formOf(saved));setActionError('');setNotice('已恢复已保存的设置。');}}}>恢复已保存设置</button></>}</div>
        </form>
        <p className="ma-disclosure">启用或立即整理后，邮件内容将发送到当前设置的 AI 服务。分类不删除邮件；待办建议需人工审核确认，不会自动发送邮件。</p>
      </aside>
      <div className="ma-workspace">
        <section className="ma-run" aria-label="自动整理运行状态">
          <div className="ma-run-heading"><strong>{status?stateText[status.state]:'正在读取运行状态…'}</strong><div><button disabled={loading||!saved||!!busy||inProgress||dirty} onClick={()=>void runNow()}>{busy==='run'?'正在加入队列…':inProgress?'整理进行中':'立即整理一次'}</button><button disabled={!!busy} onClick={()=>{void refreshStatus();void refreshResults(true);void refreshBackground();}}>刷新结果</button></div></div>
          {dirty&&<p className="ma-help">请先保存设置，再立即整理。</p>}
          {!saved?.enabled&&<p className="ma-help">未启用定时整理时，立即整理只运行一次；出错后请查看原因并手动重试。</p>}
          <p>下一次：{timeLabel(nextRunAt,zone)}{nextRunAt?`（${zone==='Asia/Hong_Kong'?'香港时间':zone}）`:''}</p>
          {status?.lastRun&&<div><p>{triggerText[status.lastRun.trigger]} · {runStateText[status.lastRun.state]} · {timeLabel(status.lastRun.finishedAt??status.lastRun.startedAt,zone)}</p><p>导入 {status.lastRun.imported} · 已分类 {status.lastRun.classified} · 跳过 {status.lastRun.skipped} · 失败 {status.lastRun.failed}</p>{status.lastRun.error&&<p role="alert" className="ma-error">{status.lastRun.error}</p>}</div>}
          {statusError&&<p role="alert" className="ma-error">运行状态读取失败：{statusError}</p>}
          <div className="ma-background"><strong>系统后台：</strong>{background?(!background.supported?'当前系统不支持系统后台':!background.installed?'尚未安装系统后台':background.active===false?'已安装，尚未加载':background.active===true?'已启用，等待计划或执行中':'已安装'):api.getBackgroundStatus?'正在读取…':'系统后台状态暂不可用。'}{background?.message&&<p>{background.message}</p>}{backgroundError&&<p role="alert">后台状态读取失败：{backgroundError}</p>}</div>
        </section>
        {actionError&&<p className="ma-error" role="alert">{actionError}</p>}
        {notice&&<p className="ma-notice" role="status">{notice}</p>}
        <nav className="ma-categories" aria-label="按分类筛选邮件">
          <button disabled={busy==='correct'} aria-pressed={!category} onClick={()=>{setCategory('');setOffset(0);}}>全部（{allCount}）</button>
          {categoryKeys.map(key=><button key={key} disabled={busy==='correct'} aria-pressed={category===key} onClick={()=>{setCategory(key);setOffset(0);}}>{categories[key]}（{page.counts[key]??0}）</button>)}
        </nav>
        <p className="ma-result-help">正文不完整或过长的邮件会留在“待核实”，请查看原文后手动处理。{dirty?'查看原邮件前，请先保存或恢复已保存设置。':''}</p>
        <div className="ma-results" aria-label="自动分类结果" aria-busy={resultsLoading}>
          {resultsError&&<p role="alert" className="ma-error">分类结果读取失败：{resultsError}</p>}
          {resultsLoading&&<p role="status">正在读取分类结果…</p>}
          {!resultsLoading&&!resultsError&&!page.items.length&&<p className="ma-empty">{category?'这个分类暂时没有邮件。':'尚无整理结果。保存设置后，可立即整理一次。'}</p>}
          {!resultsLoading&&page.items.map(item=><article className="ma-result" key={item.localId}>
            <div className="ma-result-title"><h4>{item.subject||'（无主题）'}</h4><span className={'ma-category ma-category-'+item.category}>{categories[item.category]}</span>{item.corrected&&<span className="ma-corrected">已手动更正</span>}</div>
            <p className="ma-source">{item.sender} · {timeLabel(item.receivedAt,zone)}</p>
            <p className="ma-summary">{item.summary||'暂无摘要'}</p>
            {item.reason&&<p className="ma-reason">分类依据：{item.reason}</p>}
            {item.error&&<p className="ma-error" role="alert">{item.error}</p>}
            <div className="ma-result-actions">
              {onOpenMail&&<button disabled={!!busy||dirty} title={dirty?'请先保存或恢复已保存设置':''} onClick={()=>onOpenMail(item.localId)} aria-label={`查看原邮件 ${item.subject}`}>查看原邮件</button>}
              {item.analysisId&&<button className="primary" disabled={!!busy} onClick={()=>void review(item)} aria-label={`审核待办建议 ${item.subject}`}>审核待办建议</button>}
              <label>更正分类<span className="ma-sr-only">：{item.subject}</span><select aria-label={`更正分类 ${item.subject}`} disabled={!!busy} value={item.category} onChange={event=>void correct(item,event.target.value as MailAutomationCategory)}>{categoryKeys.map(key=><option key={key} value={key}>{categories[key]}</option>)}</select></label>
            </div>
          </article>)}
        </div>
        <footer className="ma-pagination"><button disabled={resultsLoading||offset===0||!!busy} onClick={()=>setOffset(Math.max(0,offset-PAGE_SIZE))}>上一页</button><span>共 {page.total} 封 · 第 {Math.floor(offset/PAGE_SIZE)+1} / {Math.max(1,Math.ceil(page.total/PAGE_SIZE))} 页</span><button disabled={resultsLoading||offset+PAGE_SIZE>=page.total||!!busy} onClick={()=>setOffset(offset+PAGE_SIZE)}>下一页</button></footer>
      </div>
    </div>
  </section>;
}
