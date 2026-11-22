import React, { useEffect, useRef, useState } from 'react';
import type { CanvasConfig, CanvasSyncReport } from '../shared/canvas';
import type { Due } from '../shared/todo';
import type { Result } from '../shared/contracts';
const unwrap = <T,>(value: Result<T>): T => { if (!value.ok) throw new Error(value.error); return value.data; };
const describe = (error: unknown) => (error instanceof Error ? error.message : '操作失败，请重试。').replace(/(?:https?|webcal):\/\/[^\s]+/gi, '［订阅地址］');
function deadline(due?: Due) { if (!due) return '无记录'; if (due.mode !== 'value') return '无截止时间'; return due.value.kind === 'date' ? due.value.date : new Date(due.value.at).toLocaleString('zh-CN', {year:'numeric',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit'}); }
type Props = { onClose(): void; onChanged(): Promise<void> };
export function CanvasSync({ onClose, onChanged }: Props) {
  const [config, setConfig] = useState<CanvasConfig | null>(null), [url, setUrl] = useState('');
  const [loading, setLoading] = useState(true), [busy, setBusy] = useState(false), [editing, setEditing] = useState(false);
  const [error, setError] = useState(''), [report, setReport] = useState<CanvasSyncReport | null>(null), [confirmClose, setConfirmClose] = useState(false);
  const panel = useRef<HTMLElement>(null), confirmation = useRef<HTMLDivElement>(null);
  const locked = useRef(false), mounted = useRef(false), started = useRef(false);
  const initial = useRef<Promise<Result<CanvasConfig>> | null>(null);
  const latest = useRef({onClose, dirty:false, busy:false, confirmClose:false});
  latest.current = {onClose, dirty:!!url, busy, confirmClose};
  function requestClose() { if (locked.current) return; if (url) setConfirmClose(true); else onClose(); }
  async function run(action: () => Promise<void>) {
    if (locked.current) return;
    locked.current = true; setBusy(true); setError('');
    try { await action(); } catch (e) { if (mounted.current) setError(describe(e)); }
    finally { locked.current = false; if (mounted.current) setBusy(false); }
  }
  async function syncWork() {
    setReport(null);
    const next = unwrap(await window.desktop.syncCanvas());
    if (!mounted.current) return;
    setReport(next);
    try { await onChanged(); } catch { setError('同步已完成，但列表刷新失败，请关闭面板后重新打开应用查看。'); }
  }
  useEffect(() => {
    mounted.current = true;
    if (!initial.current) initial.current = window.desktop.getCanvasConfig();
    initial.current.then(result => {
      if (!mounted.current) return;
      const next = unwrap(result); setConfig(next); setEditing(!next.configured); setLoading(false);
      if (next.configured && !started.current) { started.current = true; void run(syncWork); }
    }).catch(e => { if (mounted.current) { setError(describe(e)); setLoading(false); } });
    return () => { mounted.current = false; };
  }, []);
  useEffect(() => {
    const previous = document.activeElement as HTMLElement | null;
    panel.current?.focus();
    function key(event: KeyboardEvent) {
      const state = latest.current;
      if (event.key === 'Escape') { event.preventDefault(); event.stopPropagation(); if (state.busy) return; if (state.confirmClose) setConfirmClose(false); else if (state.dirty) setConfirmClose(true); else state.onClose(); }
      if (event.key === 'Tab') {
        const scope = confirmation.current ?? panel.current;
        const nodes = Array.from(scope?.querySelectorAll<HTMLElement>('button:not(:disabled),input:not(:disabled),summary,[tabindex="0"]') ?? []);
        const first = nodes[0], last = nodes[nodes.length - 1];
        if (!first) { event.preventDefault(); scope?.focus(); }
        else if (event.shiftKey && (document.activeElement === first || !nodes.includes(document.activeElement as HTMLElement))) { event.preventDefault(); last?.focus(); }
        else if (!event.shiftKey && (document.activeElement === last || !nodes.includes(document.activeElement as HTMLElement))) { event.preventDefault(); first.focus(); }
      }
    }
    function focus(event: FocusEvent) { const scope = confirmation.current ?? panel.current; if (scope && !scope.contains(event.target as Node)) scope.focus(); }
    function beforeUnload(event: BeforeUnloadEvent) { if (latest.current.busy || latest.current.dirty) { event.preventDefault(); event.returnValue = ''; } }
    document.addEventListener('keydown', key, true); document.addEventListener('focusin', focus); window.addEventListener('beforeunload', beforeUnload);
    return () => { document.removeEventListener('keydown', key, true); document.removeEventListener('focusin', focus); window.removeEventListener('beforeunload', beforeUnload); previous?.focus(); };
  }, []);
  useEffect(() => { if (confirmClose) confirmation.current?.querySelector<HTMLButtonElement>('button')?.focus(); else panel.current?.focus(); }, [confirmClose]);
  return <div className="canvas-backdrop"><section className="canvas-panel" role="dialog" aria-modal="true" aria-labelledby="canvas-title" tabIndex={-1} ref={panel} aria-busy={busy}>
    <div className="canvas-content" inert={confirmClose}>
      <header><div><h2 id="canvas-title">同步 Canvas</h2><p>点击一次，只将今天及之后的日历事项同步为本地待办。</p></div><button aria-label="关闭Canvas同步" disabled={busy} onClick={requestClose}>×</button></header>
      {error && <p className="canvas-error" role="alert">{error}</p>}
      {loading ? <p role="status">正在读取订阅配置…</p> : <>
        {config?.configured && <div className="canvas-source"><span>当前订阅：{config.host}</span><p>完成状态和本地备注会保留；标题、截止时间和来源说明跟随 Canvas 更新。日历缺少旧事项时保留本地记录。</p></div>}
        {(editing || !config?.configured) && <form onSubmit={event => { event.preventDefault(); void run(async () => { const next = unwrap(await window.desktop.saveCanvasFeed(url.trim())); setConfig(next); setUrl(''); setEditing(false); await syncWork(); }); }}>
          <label>Calendar Feed 订阅链接<input type="password" autoComplete="off" spellCheck={false} value={url} maxLength={8000} disabled={busy} onChange={event => setUrl(event.target.value)} placeholder="粘贴 Canvas 日历中的订阅链接" required/></label>
          <p className="canvas-muted">链接只保存在本机，无需 Canvas 密码或 AI。请在这里粘贴，不必发送到聊天中。</p>
          <div className="canvas-actions"><button className="primary" type="submit" disabled={busy || !url.trim()}>保存并同步</button>{config?.configured && <button type="button" disabled={busy} onClick={() => { setUrl(''); setEditing(false); }}>取消更换</button>}</div>
        </form>}
        {config?.configured && !editing && <div className="canvas-actions"><button className="primary" disabled={busy} onClick={() => void run(syncWork)}>{busy ? '正在同步…' : '再次同步'}</button><button disabled={busy} onClick={() => setEditing(true)}>更换订阅链接</button><button disabled={busy} onClick={() => void run(async () => { unwrap(await window.desktop.clearCanvasFeed()); setConfig({configured:false,host:''}); setReport(null); setEditing(true); })}>移除订阅</button></div>}
        {busy && <p role="status">正在从 Canvas 拉取并保存待办，请稍候…</p>}
        {report && <section className="canvas-report" aria-label="Canvas同步结果"><h3>同步完成</h3><p role="status">新增 {report.added} 项 · 更新 {report.updated} 项 · 未变化 {report.unchanged} 项 · 跳过 {report.skipped} 项</p>
          {!!report.expired && <p className="canvas-muted">已跳过 {report.expired} 项截止日期早于今天的事项，已有历史记录仍保留。</p>}
          {!report.changes.length && <p className="canvas-muted">没有新增或更新的事项。</p>}
          <ul>{report.changes.map(change => <li key={change.id}><span className="canvas-change-kind">{change.kind === 'updated' ? '已更新' : '已新增'}</span><strong>{change.title}</strong>{change.kind === 'updated' ? <details><summary>查看变更</summary><dl><dt>标题</dt><dd>{change.previousTitle ?? change.title} → {change.title}</dd><dt>截止时间</dt><dd>{deadline(change.previousDue)} → {deadline(change.due)}</dd>{change.descriptionChanged && <><dt>来源说明</dt><dd>Canvas 说明已更新，可在任务详情中查看全文。</dd></>}{change.urlChanged && <><dt>原文链接</dt><dd>Canvas 原文地址已更新。</dd></>}</dl></details> : <small>截止时间：{deadline(change.due)}</small>}</li>)}</ul>
          {!!report.issues?.length && <div className="canvas-import-issues"><h3>以下事项未同步</h3><p>其他有效事项已保存。修正后可以再次同步。</p><ul>{report.issues.map((issue, index) => <li key={index}><strong>{issue.title || '未命名事项'}</strong><p>{issue.reason}</p></li>)}</ul></div>}
        </section>}
        <p className="canvas-muted">移除订阅只清除连接配置，已导入的待办仍保留。本应用中的完成状态不会回写 Canvas。</p>
      </>}
    </div>
    {confirmClose && <div className="canvas-confirm" role="alertdialog" aria-modal="true" aria-labelledby="canvas-close-title" ref={confirmation} tabIndex={-1}><h3 id="canvas-close-title">放弃未保存的订阅链接？</h3><div className="canvas-actions"><button className="primary" onClick={() => setConfirmClose(false)}>继续填写</button><button onClick={onClose}>放弃并关闭</button></div></div>}
  </section></div>;
}
