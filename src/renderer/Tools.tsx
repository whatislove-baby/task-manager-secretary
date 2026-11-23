import React, { useEffect, useRef, useState } from 'react';
import { X, Settings2, Sparkles, ArchiveRestore, Download, Upload, ShieldCheck } from 'lucide-react';
import type { CompletionConfig, Todo, TodoDraft } from '../shared/todo';
import { draftSchema } from '../shared/todo';
import { validateDrafts } from '../shared/drafts';
import type { Result, Settings } from '../shared/contracts';
import type { MailExtraction } from '../shared/mail';
import type { MailAnalysis } from '../shared/mail-workspace';
import { DueEditor } from './DueEditor';
import { PreferencesSection } from './PreferencesSection';
import { clearDraftReview, draftReviewKey, loadDraftReview, saveDraftReview } from './draft-review-store';
import { DuplicateTodoMatches } from './DuplicateTodoMatches';
import { findDuplicateTodos } from './duplicate-todos';

type Props = { initialAnalysis?:MailAnalysis; initialExtraction?: MailExtraction; section: 'settings' | 'ai' | 'trash'; todos: Todo[]; onClose: () => void; onChanged: () => Promise<void>; onSaved?: () => void | Promise<void>; returnFocus?: HTMLElement | null };
const unwrap = <T,>(result: Result<T>): T => { if (!result.ok) throw new Error(result.error); return result.data; };
const describe = (error: unknown) => error instanceof Error ? error.message : String(error);
const tabbable = 'button:not(:disabled),input:not(:disabled),select:not(:disabled),textarea:not(:disabled),a[href],summary,[tabindex="0"]';
type SaveMode = 'selected' | 'all';
type DuplicateConfirmation = { mode: SaveMode; signature: string; blockedCount: number; pendingCount: number };

export function Tools({ section, todos, onClose, onChanged, onSaved, initialExtraction, initialAnalysis, returnFocus }: Props) {
  const panel = useRef<HTMLDivElement>(null);
  const confirmPanel = useRef<HTMLDivElement>(null);
  const duplicatePrompt = useRef<HTMLParagraphElement>(null);
  const locked = useRef(false);
  const [busy, setBusy] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [warning, setWarning] = useState('');
  const [confirmClose, setConfirmClose] = useState(false);
  const [settings, setSettings] = useState<Settings | null>(null);
  const [baseUrl, setBaseUrl] = useState('');
  const [model, setModel] = useState('');
  const [apiKey, setApiKey] = useState('');
  const [config, setConfig] = useState<CompletionConfig | null>(null);
  const [configBaseline, setConfigBaseline] = useState('');
  const [text, setText] = useState('');
  const [textBaseline, setTextBaseline] = useState('');
  const reviewKey = draftReviewKey(initialAnalysis?.id, initialExtraction?.confirmationToken);
  const reviewMode = !!reviewKey;
  const sourceDrafts = initialAnalysis?.drafts ?? initialExtraction?.drafts ?? [];
  const [restored] = useState(() => reviewKey ? loadDraftReview(reviewKey, JSON.stringify(sourceDrafts)) : undefined);
  const initialDraftList = restored?.drafts ?? sourceDrafts;
  const [sourceName, setSourceName] = useState(initialExtraction?.sourceName ?? '');
  const [drafts, setDrafts] = useState<TodoDraft[]>(initialDraftList);
  const [selected, setSelected] = useState<Set<string>>(new Set(restored?.selected ?? initialDraftList.map(d=>d.tempId)));
  const [token, setToken] = useState(restored?.token ?? initialAnalysis?.id ?? initialExtraction?.confirmationToken ?? '');
  const [generated, setGenerated] = useState(!!initialAnalysis || !!initialExtraction);
  const [saved, setSaved] = useState(restored?.saved ?? initialAnalysis?.saved ?? false);
  const [aiNotes, setAiNotes] = useState(restored?.aiNotes ?? initialAnalysis?.notes ?? initialExtraction?.notes ?? '');
  const [added, setAdded] = useState<Set<string>>(new Set(restored?.addedTempIds ?? initialAnalysis?.addedTempIds ?? []));
  const [addedTodos, setAddedTodos] = useState(initialAnalysis?.addedTodos);
  const [invalidDraft, setInvalidDraft] = useState('');
  const [checkedTodos, setCheckedTodos] = useState<Todo[] | null>(null);
  const [duplicateConfirmation, setDuplicateConfirmation] = useState<DuplicateConfirmation | null>(null);
  const dirty = section === 'settings'
    ? !!settings && (baseUrl !== settings.baseUrl || model !== settings.model || !!apiKey || (!!config && JSON.stringify(config) !== configBaseline))
    : section === 'ai' && (initialAnalysis ? !saved && (JSON.stringify(drafts)!==JSON.stringify(initialAnalysis.drafts)||JSON.stringify([...selected])!==JSON.stringify(initialAnalysis.drafts.map(d=>d.tempId))) : text !== textBaseline || (!!token && !saved));
  const latest = useRef({ dirty, busy, onClose, confirmClose, reviewMode });
  latest.current = { dirty, busy, onClose, confirmClose, reviewMode };
  function requestClose() {
    if (locked.current) return;
    // 审核窗口关闭只收起，不丢弃本轮草稿；重新打开由草稿状态恢复。
    if (latest.current.reviewMode) { latest.current.onClose(); return; }
    if (latest.current.dirty) setConfirmClose(true); else onClose();
  }
  async function run(action: () => Promise<void>) {
    if (locked.current) return;
    locked.current = true; setBusy(true); setError(''); setWarning('');
    try { await action(); } catch (e) { const message = describe(e); setError(message); }
    finally { locked.current = false; setBusy(false); }
  }
  async function load() {
    await run(async () => {
      setLoading(true);
      try {
        if (section !== 'trash') {
          const nextSettings = unwrap(await window.desktop.getSettings());
          setSettings(nextSettings); setBaseUrl(nextSettings.baseUrl); setModel(nextSettings.model);
        }
        if (section === 'settings') {
          const nextConfig = unwrap(await window.desktop.getCompletionConfig());
          setConfig(nextConfig); setConfigBaseline(JSON.stringify(nextConfig));
        }
      } finally { setLoading(false); }
    });
  }
  useEffect(() => { void load(); }, []);
  useEffect(() => {
    const previous = document.activeElement as HTMLElement | null;
    panel.current?.focus();
    function keydown(event: KeyboardEvent) {
      if (event.key === 'Escape') {
        event.preventDefault(); event.stopPropagation();
        if (latest.current.busy) return;
        if (latest.current.reviewMode) { latest.current.onClose(); return; }
        if (latest.current.confirmClose) setConfirmClose(false);
        else if (latest.current.dirty) setConfirmClose(true);
        else latest.current.onClose();
      }
      if (event.key === 'Tab') {
        const scope = confirmPanel.current ?? panel.current;
        const nodes = Array.from(scope?.querySelectorAll<HTMLElement>(tabbable) ?? []);
        const first = nodes[0], last = nodes[nodes.length - 1];
        if (!first) { event.preventDefault(); scope?.focus(); }
        else if (event.shiftKey && (document.activeElement === first || !nodes.includes(document.activeElement as HTMLElement))) { event.preventDefault(); last.focus(); }
        else if (!event.shiftKey && (document.activeElement === last || !nodes.includes(document.activeElement as HTMLElement))) { event.preventDefault(); first.focus(); }
      }
    }
    function keepFocus(event: FocusEvent) {
      const scope = confirmPanel.current ?? panel.current;
      if (scope && !scope.contains(event.target as Node)) scope.focus();
    }
    function beforeUnload(event: BeforeUnloadEvent) { if (latest.current.dirty || latest.current.busy) { event.preventDefault(); event.returnValue = ''; } }
    document.addEventListener('keydown', keydown, true);
    document.addEventListener('focusin', keepFocus);
    window.addEventListener('beforeunload', beforeUnload);
    return () => {
      document.removeEventListener('keydown', keydown, true);
      document.removeEventListener('focusin', keepFocus);
      window.removeEventListener('beforeunload', beforeUnload);
      (returnFocus?.isConnected ? returnFocus : previous)?.focus();
    };
  }, []);
  useEffect(() => {
    if (confirmClose) confirmPanel.current?.querySelector<HTMLButtonElement>('button')?.focus();
    else panel.current?.focus();
  }, [confirmClose]);
  useEffect(() => {
    if (duplicateConfirmation) duplicatePrompt.current?.focus();
  }, [duplicateConfirmation]);
  useEffect(() => {
    if (!reviewMode || !reviewKey) return;
    saveDraftReview(reviewKey, { drafts, selected: [...selected], token, saved, aiNotes, addedTempIds: [...added], baseDrafts: JSON.stringify(sourceDrafts) });
  }, [reviewMode, reviewKey, drafts, selected, token, saved, aiNotes, added, sourceDrafts]);
  function patchDraft(id: string, patch: Partial<TodoDraft>) {
    setDuplicateConfirmation(null);
    setDrafts(old => old.map(d => d.tempId === id ? { ...d, ...patch } : d));
  }
  function ancestorIds(id: string, items = drafts): string[] {
    const result: string[] = []; let cursor: string | null = id;
    while (cursor && !result.includes(cursor)) { result.push(cursor); cursor = items.find(d => d.tempId === cursor)?.parentTempId ?? null; }
    return result;
  }
  function toggleSelected(id: string, checked: boolean) {
    setDuplicateConfirmation(null);
    setSelected(old => {
      const next = new Set(old);
      if (checked) ancestorIds(id).forEach(v => next.add(v));
      else drafts.filter(d => ancestorIds(d.tempId).includes(id)).forEach(d => next.delete(d.tempId));
      return next;
    });
  }
  function changeParent(id: string, parentTempId: string | null) {
    setDuplicateConfirmation(null);
    const changed = drafts.map(d => d.tempId === id ? { ...d, parentTempId, due: !parentTempId && d.due?.mode === 'inherit' ? { mode: 'none' as const } : d.due } : d);
    setDrafts(changed);
    if (selected.has(id)) setSelected(old => new Set([...old, ...ancestorIds(id, changed)]));
  }
  function withAncestors(items: TodoDraft[]): TodoDraft[] {
    const ids = new Set(items.map(item => item.tempId));
    for (const item of items) ancestorIds(item.tempId).forEach(id => ids.add(id));
    return drafts.filter(draft => ids.has(draft.tempId));
  }
  /** 统一校验并定位到具体草稿；失败时保留所有编辑内容。 */
  function validateForSave(items: TodoDraft[]): boolean {
    setInvalidDraft('');
    for (let index = 0; index < items.length; index++) {
      const item = items[index];
      const parsed = draftSchema.safeParse(item);
      if (!parsed.success) {
        setInvalidDraft(item.tempId);
        setError(`草稿 ${index + 1}（${item.title.trim() || '未命名'}）：${parsed.error.issues[0]?.message ?? '内容无效'}`);
        const locate=()=>panel.current?.querySelector<HTMLElement>(`[data-draft-id="${item.tempId.replace(/"/g, '\\"')}"]`)?.scrollIntoView?.({ block: 'nearest' });
        if(typeof globalThis.requestAnimationFrame==='function')globalThis.requestAnimationFrame(locate);else setTimeout(locate,0);
        return false;
      }
    }
    try { validateDrafts(items); } catch (e) { setError(describe(e)); return false; }
    return true;
  }
  async function saveDraftSet(mode: SaveMode, confirmedSignature?: string) {
    if (!token || saved) return;
    setDuplicateConfirmation(null);
    const base = mode === 'all' ? drafts : drafts.filter(draft => selected.has(draft.tempId));
    const chosen = withAncestors(base);
    if (!chosen.length) throw new Error('请至少选择一项草稿。');
    const pending = chosen.filter(draft => !added.has(draft.tempId));
    if (!pending.length) { setWarning('本轮草稿都已在待办中，没有重复添加。'); return; }
    if (!validateForSave(mode === 'all' ? drafts : chosen)) return;
    let savedTodoIds = addedTodos;
    const unmappedParents = pending.flatMap(({ due, parentTempId }) =>
      parentTempId && added.has(parentTempId) && !addedTodos?.[parentTempId] && (!due || due.mode === 'inherit')
        ? [parentTempId] : [],
    );
    if (initialAnalysis && unmappedParents.length) {
      try {
        const progress = unwrap(await window.mailWorkspace.getAnalysis(initialAnalysis.id));
        savedTodoIds = { ...addedTodos, ...progress.addedTodos };
        if (unmappedParents.some(id => !savedTodoIds?.[id])) throw new Error('部分已添加的父待办缺少对应标识。');
        setAddedTodos(savedTodoIds);
      } catch (e) {
        throw new Error(`无法读取已添加父待办的保存记录，草稿已保留。${describe(e)}`);
      }
    }
    let latestTodos: Todo[];
    try {
      latestTodos = unwrap(await window.desktop.listTodos());
    } catch (e) {
      throw new Error(`无法读取待办以检查重复，草稿已保留。${describe(e)}`);
    }
    setCheckedTodos(latestTodos);
    const matches = findDuplicateTodos(chosen, latestTodos, added, savedTodoIds);
    const signature = JSON.stringify(Array.from(matches, ([id, candidates]) => [
      id, [...candidates].sort((a, b) => a.todo.id.localeCompare(b.todo.id))
        .map(({ todo, deadline, parentTitle, reasons }) => [todo.id, todo.revision, deadline, parentTitle, reasons]),
    ]));
    if (matches.size && signature !== confirmedSignature) {
      setDuplicateConfirmation({ mode, signature, blockedCount: matches.size, pendingCount: pending.length });
      return;
    }
    if (initialAnalysis) {
      // 后端按已添加标识去重，并保留批次之间的父子层级。
      const result = unwrap(await window.mailWorkspace.saveAnalysis(initialAnalysis.id, mode === 'all' ? drafts : chosen));
      const next = new Set([...added, ...chosen.map(draft => draft.tempId)]);
      setAdded(next);
      const done = drafts.every(draft => next.has(draft.tempId));
      setSaved(done);
      setTextBaseline(text);
      await onChanged();
      if (done) { setWarning(''); await onSaved?.(); }
      else setWarning(`已添加 ${result.count} 项，剩余 ${drafts.length - next.size} 项待审核。`);
      return;
    }
    const result = unwrap(await window.desktop.saveDrafts(chosen, token));
    setWarning(result.warnings.map(w => w.message).join(' '));
    setAdded(new Set([...added, ...chosen.map(draft => draft.tempId)]));
    setSaved(true); setToken('');
    clearDraftReview(reviewKey);
    setTextBaseline(text);
    await onChanged();
    await onSaved?.();
  }
  const duplicateMatches = section === 'ai' && generated && !saved
    ? findDuplicateTodos(drafts, checkedTodos ?? todos, added, addedTodos)
    : undefined;
  const batches = Array.from(new Set(todos.filter(t => t.deletedAt && t.trashBatchId).map(t => t.trashBatchId!))).map(id => {
    const items = todos.filter(t => t.trashBatchId === id && t.deletedAt);
    const root = items.find(t => !items.some(parent => parent.id === t.parentId)) ?? items[0];
    return { id, items, root };
  }).sort((a, b) => (b.root.deletedAt ?? '').localeCompare(a.root.deletedAt ?? ''));
  const title = section === 'settings' ? '偏好与数据' : section === 'ai' ? '从原文整理待办' : '回收站';
  const Icon = section === 'settings' ? Settings2 : section === 'ai' ? Sparkles : ArchiveRestore;

  return <div className="tools-backdrop" onClick={event => { if (event.target === event.currentTarget) requestClose(); }}>
    <div ref={panel} className="tools-panel" role="dialog" aria-modal="true" aria-labelledby="tools-title" tabIndex={-1} aria-busy={busy}>
      <div className="tools-content" inert={confirmClose}>
        <header className="tools-header"><div><span className="tools-eyebrow">本地工作台</span><h2 id="tools-title"><Icon size={23} aria-hidden="true" />{title}</h2></div><button type="button" className="tools-icon-button" aria-label="关闭工具面板" disabled={busy} onClick={requestClose}><X size={21} /></button></header>
        {error && <div role="alert" className="tools-error">{error}</div>}
        {warning && <p role="status" className="tools-ai-notes">{warning}</p>}
        {loading ? <p role="status">正在读取本地设置…</p> : <>
          {section !== 'trash' && (!settings || (section === 'settings' && !config)) && <button disabled={busy} onClick={() => void load()}>重新读取设置</button>}
          {section === 'settings' && settings && <>
            <PreferencesSection />
            <form className="tools-card" onSubmit={e => { e.preventDefault(); void run(async () => {
              const next = unwrap(await window.desktop.saveSettings({ baseUrl, model, apiKey }));
              setSettings(next); setBaseUrl(next.baseUrl); setModel(next.model); setApiKey('');
            }); }}>
              <h3>AI 服务</h3><p className="tools-muted">使用你自己的兼容服务。密钥由系统加密保存在本机。</p>
              <fieldset disabled={busy} className="tools-fields">
                <label>服务地址（HTTPS）<input type="url" required value={baseUrl} onChange={e => setBaseUrl(e.target.value)} placeholder="https://你的服务域名/v1" /></label>
                <label>模型名称<input required maxLength={150} value={model} onChange={e => setModel(e.target.value)} /></label>
                <label>API 密钥<input type="password" autoComplete="new-password" maxLength={1000} value={apiKey} onChange={e => setApiKey(e.target.value)} placeholder={settings.hasApiKey ? '已保存；留空保留当前密钥' : '尚未配置密钥'} /></label>
                <p className="tools-muted">更换服务地址时，需重新填写对应密钥。</p>
                <div className="tools-actions"><button type="submit" className="tools-primary">保存服务配置</button><button type="button" disabled={!settings.hasApiKey} onClick={() => void run(async () => { unwrap(await window.desktop.clearKey()); setSettings({ ...settings, hasApiKey: false }); setApiKey(''); })}>清除已保存密钥</button></div>
              </fieldset>
            </form>
            {config && <form className="tools-card" onSubmit={e => { e.preventDefault(); void run(async () => { const next = unwrap(await window.desktop.setCompletionConfig(config)); setConfig(next); setConfigBaseline(JSON.stringify(next)); }); }}>
              <h3>完成联动</h3><fieldset disabled={busy} className="tools-fields">
                <label className="tools-check"><input type="checkbox" checked={config.cascadeComplete} onChange={e => setConfig({ ...config, cascadeComplete: e.target.checked })} />完成父待办时，同时完成所有子待办</label>
                <label className="tools-check"><input type="checkbox" checked={config.autoCompleteParent} onChange={e => setConfig({ ...config, autoCompleteParent: e.target.checked })} />所有子待办完成时，自动完成父待办</label>
                <button type="submit">保存完成联动</button>
              </fieldset>
            </form>}
            <section className="tools-card"><h3>数据留在本机</h3><p className="tools-path">{settings.dataDirectory}</p><p className="tools-muted">备份包含待办与完成联动配置，不包含 AI 密钥。仅空数据库可导入；回收站中的待办也算已有数据。</p>
              <div className="tools-actions"><button disabled={busy} onClick={() => void run(async () => { unwrap(await window.desktop.exportBackup()); })}><Download size={16} />导出备份</button><button disabled={busy || todos.length > 0} onClick={() => void run(async () => { if (unwrap(await window.desktop.importBackup())) { await onChanged(); } })}><Upload size={16} />导入空库备份</button></div>
              {todos.length > 0 && <p className="tools-muted">当前数据库有待办，导入已停用。</p>}
            </section>
          </>}
          {section === 'ai' && <>
            {!initialExtraction && !initialAnalysis && <form className="tools-card" onSubmit={e => { e.preventDefault(); void run(async () => {
              const next = unwrap(await window.desktop.extract(text));
              setDrafts(next.drafts); setSelected(new Set(next.drafts.map(d => d.tempId))); setToken(next.confirmationToken); setGenerated(true); setSaved(false); setAiNotes(next.notes);
              setAdded(new Set()); setDuplicateConfirmation(null); setCheckedTodos(null);
            }); }}>
              <h3>01 / 放入原文</h3><fieldset disabled={busy} className="tools-fields">
                <label>邮件或其他原文<textarea rows={7} maxLength={20000} value={text} onChange={e => setText(e.target.value)} placeholder="粘贴需要整理的文字…" /></label>
                <div className="tools-actions"><button type="button" onClick={() => void run(async () => { const imported = unwrap(await window.desktop.importText()); if (imported) { setText(imported.text); setSourceName(imported.name); } })}><Upload size={16} />导入邮件 / 文本</button><span className="tools-muted">{sourceName || `${text.length} / 20000 字`}</span></div>
                <div className="tools-privacy"><ShieldCheck size={18} aria-hidden="true" /><div><strong>生成时将发送文本框中的全部原文</strong><p>服务：{settings?.baseUrl ? `${settings.baseUrl.replace(/\/$/, '')}/chat/completions` : '尚未配置，请先在偏好中配置服务'}</p><p>模型：{settings?.model || '尚未配置'}。导入文件只读取到文本框；点击生成才发送。</p></div></div>
                <button type="submit" className="tools-primary" disabled={!settings?.hasApiKey || !text.trim() || text.length > 20000}>{busy ? '正在处理…' : token ? '重新生成并替换当前草稿' : '生成待办草稿'}</button>
              </fieldset>
            </form>}
            {initialExtraction && !initialAnalysis && <section className="tools-card"><h3>{initialExtraction.sourceName}</h3><p className="tools-muted">草稿来自选中的邮件。确认保存后，该邮件会记为已处理；未勾选的草稿不会保存。</p></section>}
            {initialAnalysis && <section className="tools-card"><h3>邮件分析记录 · {initialAnalysis.messageIds.length} 封</h3><p className="tools-muted">分析结果已保存在本机，可以关闭后重新查看。{initialAnalysis.saved?'本批待办已经确认保存。':'审核草稿后再确认添加到待办。'}</p><p className="tools-muted">模型：{initialAnalysis.model} · {new Date(initialAnalysis.createdAt).toLocaleString('zh-CN')}</p>{initialAnalysis.instructions&&<p className="tools-ai-notes">你的要求：{initialAnalysis.instructions}</p>}</section>}
            {generated && <form className="tools-card" onSubmit={e => { e.preventDefault(); void run(() => saveDraftSet('selected')); }}>
              <h3>02 / 审核与确认</h3>{aiNotes && <p className="tools-ai-notes">{aiNotes}</p>}
              {drafts.length === 0 ? <p className="tools-muted">{initialAnalysis?'分析已保存在本机，本次没有生成待办。':'这段原文中没有识别到待办。可以编辑原文后再次生成。'}</p> : <>
                <p className="tools-muted">逐项核对标题、截止日期与层级。勾选子项会同时勾选祖先；取消父项会同时取消后代。关闭窗口只收起审核，不会丢弃本轮编辑。</p><p className="tools-muted">本轮共 {drafts.length} 项，已添加 {added.size} 项，待审核 {drafts.length - added.size} 项。</p>
                <fieldset disabled={busy || saved} className="tools-fields">
                  {drafts.map((draft, index) => <article key={draft.tempId} data-draft-id={draft.tempId} className={`tools-draft ${selected.has(draft.tempId) ? 'tools-draft-selected' : ''} ${invalidDraft === draft.tempId ? 'tools-draft-invalid' : ''} ${added.has(draft.tempId) ? 'tools-draft-added' : ''}`}>
                    <label className="tools-check tools-draft-select"><input type="checkbox" aria-label={`保存草稿 ${index + 1}`} checked={selected.has(draft.tempId)} disabled={added.has(draft.tempId)} onChange={e => toggleSelected(draft.tempId, e.target.checked)} />草稿 {String(index + 1).padStart(2, '0')}{added.has(draft.tempId) && <small className="tools-added-badge">已添加</small>}</label>
                    <label>标题<input maxLength={200} required={selected.has(draft.tempId)} disabled={added.has(draft.tempId)} value={draft.title} onChange={e => patchDraft(draft.tempId, { title: e.target.value })} /></label>
                    <label>备注<textarea maxLength={10000} rows={2} disabled={added.has(draft.tempId)} value={draft.notes ?? ''} onChange={e => patchDraft(draft.tempId, { notes: e.target.value })} /></label>
                    <label>所属父待办<select disabled={added.has(draft.tempId)} value={draft.parentTempId ?? ''} onChange={e => changeParent(draft.tempId, e.target.value || null)}><option value="">独立待办</option>{drafts.filter(candidate => !ancestorIds(candidate.tempId).includes(draft.tempId)).map(candidate => <option key={candidate.tempId} value={candidate.tempId}>{candidate.title || '未命名草稿'}</option>)}</select></label>
                    <DueEditor value={draft.due ?? { mode: draft.parentTempId ? 'inherit' : 'none' }} child={!!draft.parentTempId} disabled={busy || saved || added.has(draft.tempId) || !selected.has(draft.tempId)} onChange={due => patchDraft(draft.tempId, { due })} />
                    {draft.source && <details className="tools-source"><summary>查看原文依据</summary><p>{draft.source}</p></details>}
                    <DuplicateTodoMatches matches={duplicateMatches?.get(draft.tempId)} draftNumber={index + 1} />
                  </article>)}
                  {duplicateConfirmation && <p className="tools-ai-notes" role="status" tabIndex={0} ref={duplicatePrompt}>
                    {duplicateConfirmation.blockedCount} 项草稿可能重复。取消勾选相应草稿后可使用“确认保存”，也可以确认仍然添加。
                  </p>}
                  <div className="tools-actions">
                    <button type="submit" className="tools-primary" disabled={!selected.size || !token || saved}>确认保存 {selected.size} 项待办</button>
                    <button type="button" className="tools-primary" disabled={!drafts.length || !token || saved || drafts.every(draft => added.has(draft.tempId))} onClick={() => void run(() => saveDraftSet('all'))}>全部添加（{drafts.length} 项）</button>
                    {duplicateConfirmation && <button type="button" onClick={() => void run(() => saveDraftSet(duplicateConfirmation.mode, duplicateConfirmation.signature))}>
                      仍然添加 {duplicateConfirmation.pendingCount} 项待办
                    </button>}
                  </div>
                  <p className="tools-muted">“全部添加”覆盖本轮全部 {drafts.length} 项待审核草稿（包含未勾选项）；已添加的 {added.size} 项不会重复保存。</p>
                </fieldset>
                {saved && <p role="status" className="tools-success">{initialAnalysis?'本批待办已全部保存，可关闭面板；此记录仅供查看。':'本批草稿已保存，可关闭面板。再次生成会创建新一批草稿。'}</p>}
              </>}
            </form>}
          </>}
          {section === 'trash' && <section className="tools-card"><h3>暂存，留一点余地</h3><p className="tools-muted">按删除批次完整恢复，保留原有层级。这里不会自动清空。</p>
            {!batches.length && <div className="tools-empty"><ArchiveRestore size={32} aria-hidden="true" /><p>回收站是空的</p></div>}
            <div className="tools-batches">{batches.map(batch => <article key={batch.id} className="tools-batch"><div><h4>{batch.root.title}</h4><p>{batch.items.length} 项 · {batch.root.deletedAt && new Date(batch.root.deletedAt).toLocaleString('zh-CN')}</p><details><summary>查看批次内容</summary><ul>{batch.items.map(t => <li key={t.id}>{t.title}</li>)}</ul></details></div><button disabled={busy} onClick={() => void run(async () => { const result = unwrap(await window.desktop.restoreBatch(batch.id)); await onChanged(); setWarning(result.warnings.map(w => w.message).join(' ')); })}><ArchiveRestore size={16} />恢复整批</button></article>)}</div>
          </section>}
        </>}
      </div>
      {confirmClose && <div className="tools-confirm-backdrop"><div ref={confirmPanel} className="tools-confirm" role="alertdialog" aria-modal="true" aria-labelledby="tools-confirm-title" aria-describedby="tools-confirm-description" tabIndex={-1}><h3 id="tools-confirm-title">还有未保存的内容</h3><p id="tools-confirm-description">关闭会放弃未保存的配置或 AI 原文与草稿。</p><div className="tools-actions"><button onClick={() => setConfirmClose(false)} className="tools-primary">继续编辑</button><button onClick={onClose}>放弃并关闭</button></div></div></div>}
    </div>
  </div>;
}
