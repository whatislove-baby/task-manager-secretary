import {TaskMailSource} from './TaskMailSource';
import React, { useEffect, useMemo, useRef, useState } from 'react';
import type { Todo, Due, Deadline } from '../shared/todo';
import type { Result } from '../shared/contracts';
import { localDay, mondayOf, shiftDay, projectPlanner } from '../shared/planner';
import { DueEditor } from './DueEditor';
import { Tools } from './Tools';
import { MailWorkspacePanel } from './MailWorkspacePanel';
import {useMailJobs} from './MailJobs';
import { CanvasSync } from './CanvasSync';
import { CanvasSource } from './CanvasSource';

type Section = 'settings' | 'ai' | 'trash';
type Draft = { title: string; notes: string; due: Due; completed: boolean };
const emptyDraft = (): Draft => ({ title: '', notes: '', due: { mode: 'value', value: { kind: 'date', date: localDay(new Date()) } }, completed: false });
const fromTodo = (todo: Todo): Draft => ({ title: todo.title, notes: todo.notes, due: todo.due, completed: todo.completed });
const unwrap = <T,>(r: Result<T>): T => { if (!r.ok) throw new Error(r.error); return r.data; };
const dayLabel = (day: string) => new Intl.DateTimeFormat('zh-CN', { month: 'long', day: 'numeric' }).format(new Date(day + 'T12:00:00'));
function dueLabel(due: Deadline | null): string { if (!due) return '无截止日期'; return due.kind === 'date' ? dayLabel(due.date) : new Intl.DateTimeFormat('zh-CN', { month: 'numeric', day: 'numeric', hour: '2-digit', minute: '2-digit' }).format(new Date(due.at)); }

export function App() {
  const mailJobs=useMailJobs();
  const [mailHistory,setMailHistory]=useState(false),[dismissedJobs,setDismissedJobs]=useState<Set<string>>(()=>new Set());
  const recentMailJob=mailJobs.find(job=>['completed','failed'].includes(job.state)&&!job.notificationAcknowledged&&!dismissedJobs.has(job.id+':'+job.state));
  function acknowledgeMailJob(){if(!recentMailJob)return;setDismissedJobs(old=>new Set(old).add(recentMailJob.id+':'+recentMailJob.state));void window.mailOperations?.acknowledgeJob(recentMailJob.id);}
  const activeMailJobs=mailJobs.filter(job=>['running','queued','pausing','paused'].includes(job.state));
  const [today, setToday] = useState(() => localDay(new Date()));
  const [day, setDay] = useState(today);
  const [week, setWeek] = useState(() => mondayOf(today));
  const [todos, setTodos] = useState<Todo[]>([]);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState('');
  const [busy, setBusy] = useState(false);
  const [feedback, setFeedback] = useState<{ message: string; error: boolean; scope: 'editor' | 'list' | 'delete' } | null>(null);
  const [selected, setSelected] = useState<string | null>(null);
  const [draft, setDraft] = useState<Draft | null>(null);
  const [baseline, setBaseline] = useState('');
  const [draftRevision, setDraftRevision] = useState(0);
  const [widgetEnabled, setWidgetEnabled] = useState(false);
  const [creating, setCreating] = useState(false);
  const [editing, setEditing] = useState(false);
  const [createParent, setCreateParent] = useState<string | null>(null);
  const [collapsed, setCollapsed] = useState<Set<string>>(new Set());
  const [tool, setTool] = useState<Section | null>(null);
  const [canvasOpen, setCanvasOpen] = useState(false);
  const [mailOpen, setMailOpen] = useState(false);
  const [pending, setPending] = useState<(() => void) | null>(null);
  const [deleteTarget, setDeleteTarget] = useState<string | null>(null);
  const [weekOrders, setWeekOrders] = useState<Record<string, string[]>>({});
  const order = weekOrders[day] ?? [];
  const orderLoading = weekOrders[day] === undefined;
  const [dragging, setDragging] = useState<string | null>(null);
  const orderRequest = useRef(0);
  const drag = useRef<{ id: string; day: string; original: string[]; ids: string[] } | null>(null);
  const editorRef = useRef<HTMLDivElement>(null);
  const detailScrollRef = useRef<HTMLDivElement>(null);
  const treeScrollRef = useRef<HTMLDivElement>(null);
  const taskScrollRef = useRef<HTMLDivElement>(null);
  const busyRef = useRef(false);
  const planner = useMemo(() => projectPlanner(todos), [todos]);
  const todoById = useMemo(() => new Map(todos.map(todo => [todo.id, todo])), [todos]);
  const dirty = (editing || creating) && draft !== null && JSON.stringify(draft) !== baseline;
  const selectedTodo = todos.find(t => t.id === selected && !t.deletedAt);
  const rootId = selected ? planner.rootId(selected) : null;
  const root = todos.find(t => t.id === rootId && !t.deletedAt);
  const baseRoots = planner.dailyRoots(day, order);
  const roots = [...baseRoots].sort((a,b)=>Number(a.completed)-Number(b.completed));
  function reorderGroup(ids:string[],fromId:string,toId:string){const completed=todoById.get(fromId)?.completed;if(completed!==todoById.get(toId)?.completed)return ids;const group=ids.filter(id=>todoById.get(id)?.completed===completed),from=group.indexOf(fromId),to=group.indexOf(toId);if(from<0||to<0)return ids;group.splice(from,1);group.splice(to,0,fromId);let cursor=0;return ids.map(id=>todoById.get(id)?.completed===completed?group[cursor++]:id);}
  const days = Array.from({ length: 7 }, (_, i) => shiftDay(week, i));
  function showFeedback(message: string, scope: 'editor' | 'list' | 'delete', error = true) {
    setFeedback(message ? { message, scope, error } : null);
  }
  function inlineFeedback(scope: 'editor' | 'list' | 'delete', dialog = false) {
    if (!feedback || feedback.scope !== scope || (!dialog && (pending || deleteTarget))) return null;
    return <p className={'inline-feedback ' + (feedback.error ? 'inline-error' : 'inline-warning')} role={feedback.error ? 'alert' : 'status'}>{feedback.message}</p>;
  }
  async function reload() {
    const next = unwrap(await window.desktop.listTodos(true));
    setTodos(next);
    return next;
  }
  async function load() { setLoading(true); setLoadError(''); try { await reload(); } catch (e) { setLoadError(String(e instanceof Error ? e.message : e)); } finally { setLoading(false); } }
  useEffect(() => { void load(); const timer = setInterval(() => setToday(localDay(new Date())), 30000); return () => clearInterval(timer); }, []);
  useEffect(() => {
    const widget = window.widget;
    if (!widget) return;
    let active = true;
    const refresh = async () => {
      try {
        const [state, result] = await Promise.all([widget.getState(), window.desktop.listTodos(true)]);
        if (!active) return;
        cancelDrag();
        setWidgetEnabled(state.enabled);
        setTodos(unwrap(result));
        await loadOrders();
      } catch (error) { if (active) showFeedback(error instanceof Error ? error.message : String(error), 'list'); }
    };
    void widget.getState().then(state => { if (active) setWidgetEnabled(state.enabled); }).catch(() => {});
    const unsubscribe = widget.onChanged(() => { void refresh(); });
    return () => { active = false; unsubscribe(); };
  }, [week]);
  async function toggleWidget() {
    try { await window.widget?.setEnabled(!widgetEnabled); setWidgetEnabled((await window.widget?.getState())?.enabled ?? false); }
    catch (error) { showFeedback(error instanceof Error ? error.message : String(error), 'list'); }
  }
  function updateOrder(date: string, ids: string[]) { setWeekOrders(current => ({ ...current, [date]: ids })); }
  async function loadOrders() {
    const request = ++orderRequest.current;
    setWeekOrders({});
    await Promise.all(Array.from({ length: 7 }, async (_, index) => {
      const date = shiftDay(week, index);
      try {
        const ids = unwrap(await window.desktop.getDayOrder(date));
        if (request === orderRequest.current) updateOrder(date, ids);
      } catch (error) {
        if (request === orderRequest.current) { updateOrder(date, []); showFeedback(error instanceof Error ? error.message : String(error), 'list'); }
      }
    }));
  }
  useEffect(() => { void loadOrders(); return () => { ++orderRequest.current; }; }, [week]);
  useEffect(() => {
    const onBeforeUnload = (event: BeforeUnloadEvent) => { if (dirty || busy) { event.preventDefault(); event.returnValue = ''; } };
    window.addEventListener('beforeunload', onBeforeUnload);
    return () => window.removeEventListener('beforeunload', onBeforeUnload);
  }, [dirty, busy]);
  useEffect(() => {
    if (!pending && !deleteTarget) return;
    const previous = document.activeElement as HTMLElement | null;
    const dialog = document.querySelector<HTMLElement>('.confirmation-dialog');
    const buttons = () => Array.from(dialog?.querySelectorAll<HTMLElement>('button:not(:disabled),input:not(:disabled),[tabindex="0"]') ?? []);
    buttons()[0]?.focus();
    function onKey(event: KeyboardEvent) {
      if (event.key === 'Escape' && !busyRef.current) { event.preventDefault(); setPending(null); setDeleteTarget(null); }
      if (event.key === 'Tab') { const items = buttons(); const first = items[0], last = items[items.length - 1]; if (!first) { event.preventDefault(); return; } if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last?.focus(); } else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); } }
    }
    document.addEventListener('keydown', onKey);
    return () => { document.removeEventListener('keydown', onKey); previous?.focus(); };
  }, [pending, deleteTarget]);
  useEffect(() => {
    if (!selected) return;
    requestAnimationFrame(() => {
      const node = document.getElementById('tree-' + selected);
      const treePane = treeScrollRef.current, detailPane = detailScrollRef.current;
      if (!node || !treePane || !detailPane) return;
      const row = node.getBoundingClientRect(), pane = treePane.getBoundingClientRect();
      if (row.top < pane.top) treePane.scrollTop += row.top - pane.top;
      else if (row.bottom > pane.bottom) treePane.scrollTop += row.bottom - pane.bottom;
      if (row.left < pane.left) treePane.scrollLeft += row.left - pane.left;
      else if (row.right > pane.right) treePane.scrollLeft += row.right - pane.right;
      const outer = detailPane.getBoundingClientRect();
      if (pane.top < outer.top) detailPane.scrollTop += pane.top - outer.top;
    });
  }, [selected, collapsed]);
  useEffect(() => { if (creating && createParent === null && taskScrollRef.current) taskScrollRef.current.scrollTop = 0; }, [creating, createParent]);
  function selectTodo(todo: Todo) {
    setFeedback(null);
    setDraftRevision(todo.revision); setSelected(todo.id); setDraft(fromTodo(todo)); setBaseline(JSON.stringify(fromTodo(todo))); setCreating(false); setEditing(false);
    const next = new Set(collapsed); const seen = new Set<string>(); let ancestor = todo.parentId;
    while (ancestor && !seen.has(ancestor)) { seen.add(ancestor); next.delete(ancestor); ancestor = todoById.get(ancestor)?.parentId ?? null; }
    setCollapsed(next);
  }
  function clearEditor() { setFeedback(null); setSelected(null); setDraft(null); setBaseline(''); setCreating(false); setEditing(false); }
  function navigate(action: () => void) { if (busyRef.current || busy) return; cancelDrag(); setFeedback(null); if (dirty) setPending(() => action); else action(); }
  function pickDay(next: string) { navigate(() => { setDay(next); clearEditor(); }); }
  function beginCreate(parentId: string | null) { navigate(() => { const next = emptyDraft(); setCreateParent(parentId); setCreating(true); setEditing(false); setDraft(next); setBaseline(JSON.stringify(next)); setSelected(parentId); }); }
  async function save(): Promise<boolean> {
    if (!draft || busyRef.current) return false;
    if (!draft.title.trim()) { showFeedback('请填写任务标题。', 'editor'); return false; }
    setFeedback(null); busyRef.current = true; setBusy(true);
    try {
      let saved: Todo; let warningText = '';
      if (creating) {
        const result = unwrap(await window.desktop.createTodo({ title: draft.title, notes: draft.notes, due: draft.due, parentId: createParent }));
        saved = result.value; warningText = result.warnings.map(w => w.message).join('；');
      } else {
        if (!selectedTodo) throw new Error('任务已不存在，请重新选择。');
        const result = unwrap(await window.desktop.editTodo(selectedTodo.id, { title: draft.title, notes: draft.notes, due: draft.due }, draft.completed, draftRevision));
        saved = result.value; warningText = result.warnings.map(w => w.message).join('；');
      }
      const next = await reload(); const current = next.find(t => t.id === saved.id && !t.deletedAt);
      if (current) selectTodo(current); else clearEditor();
      if (warningText) showFeedback(warningText, 'editor', false); return true;
    } catch (e) { showFeedback(e instanceof Error ? e.message : String(e), 'editor'); return false; }
    finally { busyRef.current = false; setBusy(false); }
  }
  async function toggle(todo: Todo) {
    navigate(() => { void (async () => {
      setFeedback(null); busyRef.current = true; setBusy(true);
      try {
        const latest = unwrap(await window.desktop.listTodos()).find(t => t.id === todo.id);
        if (!latest) throw new Error('任务已不存在。');
        const result = unwrap(await window.desktop.setCompleted(todo.id, !todo.completed, latest.revision));
        const next = await reload(); const current = next.find(t => t.id === selected && !t.deletedAt);
        if (current) selectTodo(current); else if (selected) clearEditor();
        if (result.warnings.length) showFeedback(result.warnings.map(w => w.message).join('；'), 'list', false); else setFeedback(null);
      } catch (e) { showFeedback(e instanceof Error ? e.message : String(e), 'list'); } finally { busyRef.current = false; setBusy(false); }
    })(); });
  }
  async function remove() {
    if (!deleteTarget || busyRef.current) return;
    setFeedback(null); busyRef.current = true; setBusy(true);
    try {
      const latest = unwrap(await window.desktop.listTodos()).find(t => t.id === deleteTarget);
      if (!latest) throw new Error('任务已不存在。');
      unwrap(await window.desktop.trashTodo(latest.id, latest.revision));
      const next = await reload(); if (selected && !next.some(t => t.id === selected && !t.deletedAt)) clearEditor();
      setDeleteTarget(null); setFeedback(null);
    } catch (e) { showFeedback(e instanceof Error ? e.message : String(e), 'delete'); } finally { busyRef.current = false; setBusy(false); }
  }
  async function persistOrder(date: string, ids: string[], previous: string[]) {
    if (busyRef.current) return;
    setFeedback(null); busyRef.current = true; setBusy(true);
    updateOrder(date, ids);
    try { updateOrder(date, unwrap(await window.desktop.setDayOrder(date, ids))); }
    catch (error) { updateOrder(date, previous); showFeedback(error instanceof Error ? error.message : String(error), 'list'); }
    finally { busyRef.current = false; setBusy(false); }
  }
  function moveItem(id: string, targetIndex: number) {
    if (busyRef.current || orderLoading) return;
    navigate(() => {
      const previous = baseRoots.map(t => t.id), target=roots[targetIndex];
      if(!target||target.completed!==todoById.get(id)?.completed)return;
      const ids = reorderGroup(previous,id,target.id), from=previous.indexOf(id);
      if (from < 0 || targetIndex < 0 || targetIndex >= ids.length || target.id === id) return;
      void persistOrder(day, ids, previous);
    });
  }
  function cancelDrag() {
    const session = drag.current;
    if (!session) return;
    drag.current = null; setDragging(null); updateOrder(session.day, session.original);
  }
  function startDrag(event: React.DragEvent<HTMLElement>, id: string) {
    if (busyRef.current || orderLoading || dirty) { event.preventDefault(); return; }
    const ids = baseRoots.map(t => t.id);
    drag.current = { id, day, original: ids, ids: [...ids] }; setDragging(id);
    event.dataTransfer.effectAllowed = 'move'; event.dataTransfer.setData('text/plain', id);
  }
  function previewDrag(event: React.DragEvent<HTMLElement>, target: string) {
    const session = drag.current;
    if (!session || session.day !== day) return;
    event.preventDefault(); event.dataTransfer.dropEffect = 'move';
    const from = session.ids.indexOf(session.id), to = session.ids.indexOf(target);
    if (from < 0 || to < 0 || from === to) return;
    const rect = event.currentTarget.getBoundingClientRect();
    if (rect.height && (from < to ? event.clientY < rect.top + rect.height / 2 : event.clientY > rect.top + rect.height / 2)) return;
    const ids = reorderGroup(session.ids,session.id,target);
    session.ids = ids; updateOrder(day, ids);
    const pane = taskScrollRef.current;
    if (pane) { const bounds = pane.getBoundingClientRect(); if (event.clientY < bounds.top + 35) pane.scrollTop -= 18; else if (event.clientY > bounds.bottom - 35) pane.scrollTop += 18; }
  }
  function finishDrag(event: React.DragEvent<HTMLElement>) {
    if (!drag.current || drag.current.day !== day) return;
    event.preventDefault(); const session = drag.current; drag.current = null; setDragging(null);
    if (session.ids.some((id, i) => id !== session.original[i])) void persistOrder(session.day, session.ids, session.original);
  }
  useEffect(() => {
    const escape = (event: KeyboardEvent) => { if (event.key === 'Escape') cancelDrag(); };
    window.addEventListener('keydown', escape); return () => window.removeEventListener('keydown', escape);
  }, []);
  const tree = useMemo(() => {
    if (!root) return [];
    const children = new Map<string, Todo[]>();
    for (const todo of todos) if (!todo.deletedAt && todo.parentId) { const list = children.get(todo.parentId) ?? []; list.push(todo); children.set(todo.parentId, list); }
    for (const list of children.values()) list.sort((a, b) => a.createdAt.localeCompare(b.createdAt) || a.id.localeCompare(b.id));
    const stack = [{ todo: root, depth: 0 }], rows: { todo: Todo; depth: number; hasChildren: boolean }[] = [], seen = new Set<string>();
    while (stack.length) { const item = stack.pop()!; if (seen.has(item.todo.id)) continue; seen.add(item.todo.id); const list = children.get(item.todo.id) ?? []; rows.push({ ...item, hasChildren: list.length > 0 }); if (!collapsed.has(item.todo.id)) for (let i = list.length - 1; i >= 0; i--) stack.push({ todo: list[i]!, depth: item.depth + 1 }); }
    return rows;
  }, [todos, root, collapsed]);
  function editForm(isNew: boolean) {
    if (!draft) return null;
    return <form className={'edit-form ' + (isNew && createParent === null ? 'root-create-form' : '')} onSubmit={event => { event.preventDefault(); void save(); }}>
      {inlineFeedback('editor')}
      <label>任务标题<input aria-label={isNew ? '新任务标题' : '任务标题'} autoFocus={isNew} maxLength={200} value={draft.title} disabled={busy} onChange={e => setDraft({ ...draft, title: e.target.value })} placeholder="写下需要完成的事" /></label>
      <label>备注<textarea value={draft.notes} maxLength={10000} disabled={busy} onChange={e => setDraft({ ...draft, notes: e.target.value })} placeholder="补充细节、材料或下一步" rows={3} /></label>
      <div className="due-field"><span>截止时间</span><DueEditor value={draft.due} onChange={due => setDraft({ ...draft, due })} child={isNew ? createParent !== null : selectedTodo?.parentId != null} disabled={busy} /></div>
      {!isNew && <label className="check-label"><input type="checkbox" checked={draft.completed} disabled={busy} onChange={e => setDraft({ ...draft, completed: e.target.checked })} />已完成</label>}
      <div className="form-actions"><button className="primary" type="submit" disabled={busy}>{busy ? '保存中…' : isNew ? '创建任务' : '保存更改'}</button><button type="button" disabled={busy} onClick={() => { if (isNew) { const parent = createParent ? todoById.get(createParent) : null; if (parent && !parent.deletedAt) selectTodo(parent); else clearEditor(); } else if (selectedTodo) selectTodo(selectedTodo); }}>取消</button>{!isNew && <button className="danger subtle" type="button" disabled={busy} onClick={() => navigate(() => setDeleteTarget(selected))}>移入回收站</button>}</div>
    </form>;
  }
  return <div className="planner-app" aria-busy={busy}>
    <header className="topbar"><h1>task-manager<span>给每一天，留一点余地。</span></h1><div className="week-navigation"><button aria-label="前一周" disabled={busy} onClick={() => navigate(() => { const next = shiftDay(week, -7); setWeek(next); setDay(next === mondayOf(today) ? today : next); clearEditor(); })}>‹</button><strong>{week.slice(0, 4)} 年 {dayLabel(week)} — {shiftDay(week, 6).slice(0, 4) !== week.slice(0, 4) ? shiftDay(week, 6).slice(0, 4) + ' 年 ' : ''}{dayLabel(shiftDay(week, 6))}</strong><button aria-label="后一周" disabled={busy} onClick={() => navigate(() => { const next = shiftDay(week, 7); setWeek(next); setDay(next === mondayOf(today) ? today : next); clearEditor(); })}>›</button><button className="today-button" disabled={busy} onClick={() => navigate(() => { setWeek(mondayOf(today)); setDay(today); clearEditor(); })}>回到今天</button></div><nav aria-label="工具"><button disabled={busy} onClick={()=>navigate(()=>{setMailHistory(false);setMailOpen(true);})}>邮箱{activeMailJobs.length?` · ${activeMailJobs.length} 项处理中`:''}</button><button disabled={busy} onClick={() => navigate(() => setCanvasOpen(true))}>同步 Canvas</button>{window.widget && <><button disabled={busy} aria-pressed={widgetEnabled} onClick={() => void toggleWidget()}>{widgetEnabled ? "关闭悬浮窗" : "开启悬浮窗"}</button>{window.widget.minimizeToTray && <button disabled={busy} onClick={() => { void window.widget?.minimizeToTray?.().catch(error => showFeedback(error instanceof Error ? error.message : String(error), 'list')); }}>最小化到托盘</button>}<button disabled={busy} onClick={() => void window.widget?.quit()}>退出应用</button></>}<button disabled={busy} onClick={() => navigate(() => {if(window.mailOperations){setMailHistory(true);setMailOpen(true);}else{setTool('ai');}})}>✦ AI 整理</button><button disabled={busy} onClick={() => navigate(() => setTool('trash'))}>回收站</button><button disabled={busy} onClick={() => navigate(() => setTool('settings'))}>设置</button></nav></header>
    <section className="week-grid" aria-label="每周计划">{days.map((date, i) => { const entries = planner.weekEntries(date, weekOrders[date] ?? []); return <div className={'day-cell ' + (date === day ? 'selected-day ' : '') + (date === today ? 'is-today' : '')} key={date} onClick={event => { if (!(event.target as Element).closest('.week-entry')) pickDay(date); }}><button className="day-heading" aria-pressed={date === day} onClick={event => { event.stopPropagation(); pickDay(date); }} disabled={busy}><span>周{'一二三四五六日'[i]}</span><strong>{Number(date.slice(-2))}</strong>{date === today && <span className="today-mark">今天</span>}<small>{entries.length ? `${entries.length} 项` : '留白'}</small></button><div className="day-entries" tabIndex={entries.length > 3 ? 0 : undefined} aria-label={dayLabel(date) + '截止任务'}>{entries.length === 0 ? <span className="day-empty">暂无截止任务</span> : entries.map(({ todo, parentTitle }) => <div className={'week-entry ' + (todo.completed ? 'completed' : '')} key={todo.id}><button className={'completion-toggle ' + (todo.completed ? 'checked' : '')} aria-label={(todo.completed ? '重新打开 ' : '完成 ') + todo.title} aria-pressed={todo.completed} disabled={busy} onClick={event => { event.stopPropagation(); void toggle(todo); }}>{todo.completed ? '✓' : ''}</button><button className="week-entry-title" title={parentTitle ? `${todo.title} · ${parentTitle}` : todo.title} disabled={busy} onClick={event => { event.stopPropagation(); navigate(() => { setDay(date); selectTodo(todo); }); }}><span>{todo.title}</span>{parentTitle && <small>↳ {parentTitle}</small>}</button></div>)}</div></div>; })}</section>
    {loading ? <main className="loading-state" role="status">正在打开你的计划…</main> : loadError ? <main className="loading-state"><p role="alert">读取任务失败：{loadError}</p><button onClick={() => void load()}>重新加载</button></main> : <main className="workspace"><aside className="task-panel"><div className="panel-heading"><div><span className="section-kicker">当天任务</span><h2>{dayLabel(day)}<small>{roots.length} 项</small></h2></div><button className="add-root" aria-label="创建根任务" disabled={busy} onClick={() => beginCreate(null)}>＋</button></div><p className="panel-hint">包含当天截止与无截止的主任务</p><div className="task-panel-scroll" ref={taskScrollRef}>{inlineFeedback('list')}{creating && createParent === null && editForm(true)}<div className="root-list" aria-label="当天根任务">{orderLoading && <p className="muted" role="status">正在读取排列…</p>}{roots.length === 0 && <div className="empty-list">今天还没有主任务。<br/>点右上角 ＋，从一件小事开始。</div>}{roots.map((todo, index) => <article className={'root-row ' + (rootId === todo.id ? 'active-root ' : '') + (todo.completed ? 'completed' : '')} key={todo.id} draggable={!busy && !orderLoading && !dirty} data-dragging={dragging === todo.id || undefined} onDragStart={event => startDrag(event, todo.id)} onDragOver={event => previewDrag(event, todo.id)} onDrop={finishDrag} onDragEnd={cancelDrag}><button className={'completion-toggle ' + (todo.completed ? 'checked' : '')} aria-label={(todo.completed ? '重新打开 ' : '完成 ') + todo.title} disabled={busy} onClick={() => toggle(todo)}>{todo.completed ? '✓' : ''}</button><button className="root-title" disabled={busy} onClick={() => navigate(() => selectTodo(todo))}><strong>{todo.title}</strong><small>{dueLabel(planner.effectiveDeadline(todo.id))}</small></button><div className="reorder-buttons"><button aria-label={'上移 ' + todo.title} disabled={busy || orderLoading || (index === 0 || roots[index-1]?.completed !== todo.completed)} onClick={() => moveItem(todo.id, index - 1)}>↑</button><button aria-label={'下移 ' + todo.title} disabled={busy || orderLoading || (index === roots.length - 1 || roots[index+1]?.completed !== todo.completed)} onClick={() => moveItem(todo.id, index + 1)}>↓</button></div></article>)}</div></div><footer className="list-footer">默认按创建时间 · 可调整当天顺序</footer></aside>
    <section className="detail-panel" ref={editorRef} aria-label="任务详情">{!selectedTodo && !(creating && createParent) ? <div className="detail-empty"><span className="empty-glyph">☷</span><h2>把注意力放在一件事上</h2><p>选择主任务查看完整子任务，<br/>也可以从上方周计划直接打开。</p></div> : <><div className="detail-heading"><div><h2>{root?.title ?? '新任务'}</h2></div><span className="detail-status">{dirty ? '有未保存的更改' : '已保存'}</span></div><div className="detail-scroll" ref={detailScrollRef}><div className="tree-scroll" ref={treeScrollRef} aria-label="完整任务树">{tree.map(({ todo, depth, hasChildren }) => <div id={'tree-' + todo.id} className={'tree-row ' + (selected === todo.id ? 'selected-node ' : '') + (todo.completed ? 'completed' : '')} key={todo.id} style={{ marginLeft: depth * 24 }}><button className="collapse-toggle" disabled={!hasChildren} aria-label={(collapsed.has(todo.id) ? '展开 ' : '折叠 ') + todo.title} aria-expanded={hasChildren ? !collapsed.has(todo.id) : undefined} onClick={() => setCollapsed(current => { const next = new Set(current); if (next.has(todo.id)) next.delete(todo.id); else next.add(todo.id); return next; })}>{hasChildren ? collapsed.has(todo.id) ? '▸' : '▾' : '·'}</button><button className={'completion-toggle ' + (todo.completed ? 'checked' : '')} aria-label={(todo.completed ? '重新打开 ' : '完成 ') + todo.title} disabled={busy} onClick={() => toggle(todo)}>{todo.completed ? '✓' : ''}</button><button className="tree-title" disabled={busy} onClick={() => navigate(() => selectTodo(todo))}><span>{todo.title}</span><small>{todo.due.mode === 'inherit' ? '共享父项截止' : dueLabel(planner.effectiveDeadline(todo.id))}</small></button><button className="add-child" disabled={busy} aria-label={'为 ' + todo.title + ' 添加子任务'} onClick={() => beginCreate(todo.id)}>＋ 子任务</button></div>)}</div><div className="editor-content">{!creating && !editing && inlineFeedback('editor')}<div className="editor-heading"><h3>{creating ? '添加子任务' : editing ? '编辑任务' : '任务详情'}</h3>{!creating && selectedTodo && <span>{selectedTodo.parentId ? '子任务' : '主任务'}</span>}</div>{creating && createParent !== null ? editForm(true) : editing ? editForm(false) : selectedTodo && !creating ? <div className="task-details" aria-label="只读任务详情"><h3 className={"detail-title " + (selectedTodo.completed ? "completed-title" : "")}>{selectedTodo.title}</h3><dl><dt>备注</dt><dd className="detail-notes">{selectedTodo.notes || '暂无备注'}<TaskMailSource key={selectedTodo.id} notes={selectedTodo.notes}/></dd><dt>截止时间</dt><dd>{selectedTodo.due.mode === 'inherit' ? '共享父项截止' : dueLabel(planner.effectiveDeadline(selectedTodo.id))}</dd></dl><CanvasSource key={selectedTodo.id} todoId={selectedTodo.id} title={selectedTodo.title} revision={selectedTodo.revision}/><div className="form-actions"><button className="primary" disabled={busy} onClick={() => { setDraftRevision(selectedTodo.revision); setDraft(fromTodo(selectedTodo)); setBaseline(JSON.stringify(fromTodo(selectedTodo))); setEditing(true); }}>编辑任务</button><button className="danger subtle" disabled={busy} onClick={() => navigate(() => setDeleteTarget(selectedTodo.id))}>移入回收站</button></div></div> : null}</div></div></>}</section></main>}
    {canvasOpen && <CanvasSync onClose={() => setCanvasOpen(false)} onChanged={async () => { await reload(); await loadOrders(); }} />}
    {!mailOpen&&!tool&&recentMailJob&&<aside className="mail-background-entry" aria-label="后台任务提示"><button onClick={()=>navigate(()=>{acknowledgeMailJob();setMailHistory(recentMailJob.kind!=='import');setMailOpen(true);})}>{recentMailJob.state==='completed'?'邮件处理已完成，点击返回查看':recentMailJob.state==='failed'?'邮件处理有异常，点击查看':`后台邮件任务：${recentMailJob.done}/${recentMailJob.total}，点击查看`}</button><button aria-label="收起后台任务提示" onClick={acknowledgeMailJob}>×</button></aside>}
    {mailOpen && <MailWorkspacePanel initialHistory={mailHistory} onClose={()=>setMailOpen(false)} onTodosChanged={async()=>{const next=await reload();await loadOrders();const current=next.find(t=>t.id===selected&&!t.deletedAt);if(current)selectTodo(current);else clearEditor();}}/>}
    {tool && <Tools section={tool} todos={todos} onClose={() => setTool(null)} onChanged={async () => { const next = await reload(); await loadOrders(); const current = next.find(t => t.id === selected && !t.deletedAt); if (current) selectTodo(current); else clearEditor(); }} />}
    {pending && <div className="modal-overlay"><section className="confirmation-dialog" role="alertdialog" aria-modal="true" aria-labelledby="unsaved-title"><h2 id="unsaved-title">保留这次修改吗？</h2><p>当前任务有未保存的更改。保存成功后再切换。</p>{feedback && inlineFeedback(feedback.scope, true)}<div className="confirm-actions"><button className="primary" disabled={busy} autoFocus onClick={() => { void (async () => { const action = pending; if (await save()) { setPending(null); action(); } })(); }}>保存并切换</button><button disabled={busy} onClick={() => { const action = pending; setPending(null); if (selectedTodo) selectTodo(selectedTodo); else clearEditor(); action(); }}>放弃并切换</button><button disabled={busy} onClick={() => setPending(null)}>继续编辑</button></div></section></div>}
    {deleteTarget && <div className="modal-overlay"><section className="confirmation-dialog" role="alertdialog" aria-modal="true" aria-labelledby="delete-title"><h2 id="delete-title">移入回收站？</h2><p>这个任务及其全部子任务会一起移入回收站，可以恢复。</p>{feedback && inlineFeedback(feedback.scope, true)}<div className="confirm-actions"><button className="danger" disabled={busy} onClick={() => void remove()}>{busy ? '正在移入…' : '移入回收站'}</button><button autoFocus disabled={busy} onClick={() => setDeleteTarget(null)}>取消</button></div></section></div>}
  </div>;
}
