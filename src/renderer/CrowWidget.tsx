import {TaskMailSource} from './TaskMailSource';
import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import type { Todo, Due, Deadline } from '../shared/todo';
import type { Result } from '../shared/contracts';
import { localDay, projectPlanner } from '../shared/planner';
import { DueEditor } from './DueEditor';
import { CanvasSource } from './CanvasSource';
import { artworkHit, hoverAvailability, birdPose } from './widget-hit-test';

const unwrap = <T,>(result: Result<T>): T => { if (!result.ok) throw new Error(result.error); return result.data; };
function deadlineText(deadline: Deadline | null): string {
  if (!deadline) return '无截止日期';
  return deadline.kind === 'date' ? deadline.date : new Date(deadline.at).toLocaleString('zh-CN', { year:'numeric',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit' });
}
function subtaskDueText(todo: Todo, deadline: Deadline | null): string {
  return todo.due.mode === 'inherit' ? '共享父项截止' : deadlineText(deadline);
}
type Perched = { todo: Todo; slot: number; phase: 'standing' | 'incoming' | 'outgoing' };
type Edit = { id: string; title: string; notes: string; due: Due; revision: number; completed: boolean };
type SubtaskRow = { todo: Todo; depth: number; hasChildren: boolean };
// 主枝上沿的素材坐标，跳过向上分叉；按实际铺展宽度插值落脚点。
const branchSurface = [[110,375],[220,375],[330,370],[440,377],[550,393],[660,408],[770,412],[880,420],[990,414],[1100,408],[1210,393],[1320,380],[1430,386],[1540,404],[1650,399],[1760,410],[1870,419],[1980,436],[2090,416]];
function birdPerchTop(index: number, sceneWidth: number) {
  // 鸟图66×72 contain布局中，爪子抓握位置约为(36,61)。
  const sourceX = (15 + index * 58 + 36) / sceneWidth * 2172;
  let y = branchSurface[0]![1]!;
  for (let i = 1; i < branchSurface.length; i++) {
    const [x0, y0] = branchSurface[i - 1]!, [x1, y1] = branchSurface[i]!;
    if (sourceX >= x0!) {
      const t = Math.min(1, (sourceX - x0!) / (x1! - x0!));
      y = y0! + t * (y1! - y0!);
    }
    if (sourceX <= x1!) break;
  }
  return 8 + y / 724 * 120 - 61 + 1; // 爪子与枝条交叠1px，避免透明边缘露缝。
}
export function CrowWidget() {
  const surface=useRef<HTMLElement>(null);
  const [artHover,setArtHover]=useState(false);
  const [items, setItems] = useState<Todo[]>([]);
  const [allTodos, setAllTodos] = useState<Todo[]>([]);
  const [collapsed, setCollapsed] = useState<Set<string>>(() => new Set());
  const [perched, setPerched] = useState<Perched[]>([]);
  const slots = useRef<Perched[]>([]);
  const snapshotDay = useRef('');
  const [mode, setMode] = useState<'closed' | 'list' | 'detail'>('closed');
  const expanded = mode !== 'closed';
  const [menu, setMenu] = useState(false);
  const [selected, setSelected] = useState<string | null>(null);
  const [edit, setEdit] = useState<Edit | null>(null);
  const [error, setError] = useState('');
  const [warning, setWarning] = useState('');
  const [dragError, setDragError] = useState('');
  const [busy, setBusy] = useState(false);
  const latest = useRef<Todo[] | null>(null), request = useRef(0), locked = useRef(false);
  const timers = useRef(new Map<string, ReturnType<typeof setTimeout>>());
  function clearBirdTimer(id: string) { const timer = timers.current.get(id); if (timer) clearTimeout(timer); timers.current.delete(id); }
  function settleBird(id: string, phase: Perched['phase']) {
    clearBirdTimer(id);
    const timer = setTimeout(() => {
      if (timers.current.get(id) !== timer) return;
      timers.current.delete(id);
      const bird = slots.current.find(entry => entry.todo.id === id);
      if (!bird || bird.phase !== phase) return;
      if (phase === 'outgoing') slots.current = slots.current.filter(entry => entry !== bird);
      else bird.phase = 'standing';
      reconcileBirds(latest.current ?? [], true);
    }, phase === 'outgoing' ? 760 : 1000);
    timers.current.set(id, timer);
  }
  function reconcileBirds(next: Todo[], animate: boolean) {
    if (!animate) { for (const timer of timers.current.values()) clearTimeout(timer); timers.current.clear(); slots.current = []; }
    const byId = new Map(next.map(todo => [todo.id, todo]));
    slots.current = slots.current.filter(entry => {
      const current = byId.get(entry.todo.id);
      if (!current) { clearBirdTimer(entry.todo.id); return false; }
      entry.todo = current;
      if (current.completed && entry.phase !== 'outgoing') { entry.phase = 'outgoing'; settleBird(current.id, 'outgoing'); }
      else if (!current.completed && entry.phase === 'outgoing') { entry.phase = 'incoming'; settleBird(current.id, 'incoming'); }
      return true;
    });
    const represented = new Set(slots.current.map(entry => entry.todo.id));
    for (const todo of next) {
      if (todo.completed || represented.has(todo.id) || slots.current.length >= 5) continue;
      const occupied = new Set(slots.current.map(entry => entry.slot));
      const slot = [0,1,2,3,4].find(value => !occupied.has(value))!;
      slots.current.push({ todo, slot, phase: animate ? 'incoming' : 'standing' });
      represented.add(todo.id);
      if (animate) settleBird(todo.id, 'incoming');
    }
    setPerched(slots.current.map(entry => ({ ...entry })));
  }
  const refresh = useCallback(async () => {
    const version = ++request.current;
    try {
      const day = localDay(new Date());
      const [all, order] = await Promise.all([window.desktop.listTodos(), window.desktop.getDayOrder(day)]);
      if (version !== request.current) return;
      const todos = unwrap(all);
      const next = projectPlanner(todos).dailyRoots(day, unwrap(order));
      const animate = latest.current !== null && snapshotDay.current === day;
      snapshotDay.current = day; latest.current = next; reconcileBirds(next, animate);
      latest.current = next; setItems(next); setAllTodos(todos);
    } catch (e) { if (version === request.current) setError(e instanceof Error ? e.message : String(e)); }
  }, []);
  useEffect(() => {
    void refresh(); const stop = window.widget?.onChanged(() => { void refresh(); });
    const timer = setInterval(() => { void refresh(); }, 30000);
    return () => { ++request.current; stop?.(); clearInterval(timer); for (const timer of timers.current.values()) clearTimeout(timer); timers.current.clear(); };
  }, [refresh]);
  const gesture = useRef<{ id: number; startX: number; startY: number; x: number; y: number; dragging: boolean; target: HTMLElement } | null>(null);
  const suppressClick = useRef(false), queuedMove = useRef({ x: 0, y: 0 }), sendingMove = useRef(false);
  const mounted = useRef(true);
  useEffect(()=>{
    if(!window.widget?.setPointerPassthrough)return;
    let last:{x:number;y:number}|null=null,ignored:boolean|undefined,active=true,availableUntil=0;
    const update=()=>{if(!active||!surface.current)return;const art=!!gesture.current||!!last&&artworkHit(surface.current,last.x,last.y,false),hit=!!gesture.current||!!last&&artworkHit(surface.current,last.x,last.y);const hover=hoverAvailability(availableUntil,Date.now(),art,hit&&!art,!!last||!!gesture.current);availableUntil=hover.until;setArtHover(hover.visible);if(ignored!==!hover.interactive){ignored=!hover.interactive;void window.widget?.setPointerPassthrough?.(!hover.interactive).catch(()=>{ignored=undefined;});}};
    const move=(event:MouseEvent)=>{last={x:event.clientX,y:event.clientY};update();};
    const leave=()=>{last=null;availableUntil=0;update();};
    window.addEventListener('mousemove',move);document.documentElement.addEventListener('mouseleave',leave);
    const timer=setInterval(update,50);update();
    return()=>{active=false;clearInterval(timer);window.removeEventListener('mousemove',move);document.documentElement.removeEventListener('mouseleave',leave);};
  },[]);

  async function drainMoves() {
    if (sendingMove.current) return;
    sendingMove.current = true;
    try {
      while (queuedMove.current.x || queuedMove.current.y) {
        const delta = queuedMove.current; queuedMove.current = { x: 0, y: 0 };
        await window.widget?.moveBy?.(delta.x, delta.y);
      }
    } catch (e) { queuedMove.current = { x: 0, y: 0 }; if (mounted.current) setDragError(String(e)); }
    finally { sendingMove.current = false; }
  }
  function beginMove(event: React.PointerEvent<HTMLElement>) {
    if (event.button !== 0 || !(event.target as Element).closest('.crow-perch,.crow-pendant,.crow-overflow')) return;
    if(window.widget?.setPointerPassthrough&&surface.current&&!artworkHit(surface.current,event.clientX,event.clientY))return;
    void window.widget?.setPointerPassthrough?.(false);
    suppressClick.current = false; setDragError('');
    gesture.current = { id: event.pointerId, startX: event.screenX, startY: event.screenY, x: event.screenX, y: event.screenY, dragging: false, target: event.currentTarget };
  }
  useEffect(() => {
    mounted.current = true;
    function move(event: PointerEvent) {
      const current = gesture.current;
      if (!current || event.pointerId !== current.id) return;
      if (!current.dragging && Math.hypot(event.screenX - current.startX, event.screenY - current.startY) < 5) return;
      if (!current.dragging) { current.dragging = true; suppressClick.current = true; current.target.setPointerCapture?.(current.id); }
      event.preventDefault();
      queuedMove.current.x += event.screenX - current.x; queuedMove.current.y += event.screenY - current.y;
      current.x = event.screenX; current.y = event.screenY; void drainMoves();
    }
    function end(event: PointerEvent) {
      const current = gesture.current;
      if (!current || event.pointerId !== current.id) return;
      gesture.current = null;
      if (current.target.hasPointerCapture?.(current.id)) current.target.releasePointerCapture(current.id);
    }
    function cancel() {
      const current = gesture.current; gesture.current = null;
      if (current?.dragging) suppressClick.current = true;
      if (current?.target.hasPointerCapture?.(current.id)) current.target.releasePointerCapture(current.id);
    }
    window.addEventListener('blur', cancel);
    window.addEventListener('pointermove', move, { passive: false });
    window.addEventListener('pointerup', end); window.addEventListener('pointercancel', end); window.addEventListener('lostpointercapture', end);
    return () => { mounted.current = false; cancel(); window.removeEventListener('blur', cancel); queuedMove.current = { x: 0, y: 0 }; window.removeEventListener('pointermove', move); window.removeEventListener('pointerup', end); window.removeEventListener('pointercancel', end); window.removeEventListener('lostpointercapture', end); };
  }, []);
  async function run(action: () => Promise<void>) {
    if (locked.current) return;
    locked.current = true; setBusy(true); setError(''); setWarning('');
    try { await action(); } catch (e) { setError(e instanceof Error ? e.message : String(e)); }
    finally { locked.current = false; setBusy(false); }
  }
  async function openPanel(next: 'closed' | 'list' | 'detail') {
    if (edit) { setError('请先保存或取消当前修改。'); return; }
    try { await window.widget?.setExpanded(next !== 'closed'); setMode(next); setMenu(false); setError(''); }
    catch (e) { setError(String(e)); }
  }
  function select(item: Todo) {
    if (edit) { setError('请先保存或取消当前修改。'); return; }
    if (mode === 'detail' && selected === item.id) { void openPanel('closed'); return; }
    setSelected(item.id); void openPanel('detail');
  }
  function openTask(item: Todo) {
    if (edit) { setError('请先保存或取消当前修改。'); return; }
    setSelected(item.id);
    if (mode !== 'detail') void openPanel('detail');
  }
  function beginEdit(item: Todo) {
    if (edit) { setError('请先保存或取消当前修改。'); return; }
    setSelected(item.id);
    setEdit({ id: item.id, title: item.title, notes: item.notes, due: item.due, completed: item.completed, revision: item.revision });
    setError('');
  }
  function toggleCollapsed(id: string) {
    setCollapsed(current => { const next = new Set(current); if (next.has(id)) next.delete(id); else next.add(id); return next; });
  }
  function toggle(item: Todo) {
    if (edit) { setError('请先保存或取消当前修改。'); return; }
    void run(async () => {
      const current = unwrap(await window.desktop.listTodos()).find(t => t.id === item.id);
      if (!current) throw new Error('事项已不存在，请刷新。');
      const result = unwrap(await window.desktop.setCompleted(current.id, !current.completed, current.revision));
      setWarning(result.warnings.map(w => w.message).join('；')); await refresh();
    });
  }
  useEffect(() => { if (error && !expanded) { void window.widget?.setExpanded(true).then(() => setMode('list')).catch(() => {}); } }, [error, expanded]);
  const current = allTodos.find(t => t.id === selected) ?? items.find(t => t.id === selected);
  const incomplete = items.filter(t => !t.completed);
  const todoById = useMemo(() => new Map(allTodos.map(todo => [todo.id, todo])), [allTodos]);
  const planner = useMemo(() => projectPlanner(allTodos), [allTodos]);
  const { subtaskRoot, subtaskRows } = useMemo(() => {
    if (!current) return { subtaskRoot: null as Todo | null, subtaskRows: [] as SubtaskRow[] };
    let root: Todo = current; const seenUp = new Set<string>();
    while (root.parentId && !seenUp.has(root.id)) { seenUp.add(root.id); const parent = todoById.get(root.parentId); if (!parent || parent.deletedAt) break; root = parent; }
    const children = new Map<string, Todo[]>();
    for (const todo of allTodos) if (!todo.deletedAt && todo.parentId) { const list = children.get(todo.parentId) ?? []; list.push(todo); children.set(todo.parentId, list); }
    for (const list of children.values()) list.sort((a, b) => a.createdAt.localeCompare(b.createdAt) || a.id.localeCompare(b.id));
    const stack: { todo: Todo; depth: number }[] = [{ todo: root, depth: 0 }], rows: SubtaskRow[] = [], seen = new Set<string>();
    while (stack.length) { const item = stack.pop()!; if (seen.has(item.todo.id)) continue; seen.add(item.todo.id); const list = children.get(item.todo.id) ?? []; rows.push({ ...item, hasChildren: list.length > 0 }); if (!collapsed.has(item.todo.id)) for (let i = list.length - 1; i >= 0; i--) stack.push({ todo: list[i]!, depth: item.depth + 1 }); }
    return { subtaskRoot: root, subtaskRows: rows };
  }, [allTodos, current, collapsed, todoById]);
  const parent = current?.parentId ? todoById.get(current.parentId) : undefined;
  const hasSubtaskTree = subtaskRoot !== null && subtaskRows[0]?.hasChildren === true;

  const overflowCount = Math.max(0, incomplete.length - 5);
  const sorted = [...incomplete, ...items.filter(t => t.completed)];
  useEffect(() => {
    const beforeUnload = (event: BeforeUnloadEvent) => { if (edit || busy) { event.preventDefault(); event.returnValue = ''; } };
    window.addEventListener('beforeunload', beforeUnload); return () => window.removeEventListener('beforeunload', beforeUnload);
  }, [edit, busy]);
  return <main ref={surface} className={'crow-widget ' + (artHover ? 'artwork-hover ' : '') + (expanded ? 'crow-expanded' : '')} aria-label="乌鸦待办悬浮窗"
    onPointerDown={beginMove}
    onClickCapture={event => { if (suppressClick.current) { event.preventDefault(); event.stopPropagation(); suppressClick.current = false; } }}
    onContextMenu={event => { event.preventDefault(); setMenu(true); }}
    onKeyDown={event => { if (event.key === 'Enter' || event.key === ' ') suppressClick.current = false; if (event.key === 'Escape') { event.stopPropagation(); if (menu) setMenu(false); else void openPanel('closed'); } if (event.key === 'F10' && event.shiftKey) { event.preventDefault(); setMenu(true); } }}>
    {dragError && <p className="crow-move-error" role="alert">{dragError}</p>}
    <button className="crow-close" title="关闭悬浮窗" aria-label="关闭悬浮窗" onClick={() => void window.widget?.close().catch(e => setError(String(e)))}><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M6 5L18 18M18 5L5 18"/></svg></button>
    {menu && <div className="crow-context" aria-label="悬浮窗操作"><button autoFocus onClick={() => void run(async () => { setMenu(false); await window.widget?.showMain(); })}>打开任务界面</button><button onClick={() => void window.widget?.quit().catch(e => setError(String(e)))}>退出整个应用</button><button onClick={() => setMenu(false)}>取消</button></div>}
    <div className="crow-perch" aria-label="当天未完成待办乌鸦"><div className="crow-branch-scene" style={{ width: 344 }}>
      <img className="crow-branch" src="./art/branch-pencil.png" alt="" draggable={false}/>
      {perched.map(({ todo: item, slot, phase }) => <div className="crow-slot" key={item.id} style={{ left: 15 + slot * 58, top: birdPerchTop(slot, 344) }} data-slot={slot}><button className={'crow-bird ' + (phase === 'outgoing' ? 'crow-flying' : phase === 'incoming' ? 'crow-incoming' : '')} aria-label={'查看任务 ' + item.title} disabled={busy} onClick={() => select(item)}>{phase !== 'standing' ? <span className="crow-flight-sprite" aria-hidden="true"/> : <img className="crow-illustration" style={{'--crow-pose-angle':birdPose(slot).rotation+'deg','--crow-pose-scale':birdPose(slot).scale,'--crow-idle-duration':birdPose(slot).duration+'s','--crow-idle-delay':birdPose(slot).delay+'s'} as React.CSSProperties} src="./art/raven-pencil.png" alt="" draggable={false}/>}<span className="crow-tooltip">{item.title}</span></button><button className="crow-name-tag" title={item.title} aria-label={'任务标题：' + item.title} onClick={() => select(item)}>{item.title}</button></div>)}
    </div></div>
    {overflowCount > 0 && <button className="crow-overflow" aria-label={`还有${overflowCount}项未完成待办，查看完整名单`} title={`还有${overflowCount}项未完成待办`} onClick={() => void openPanel('list')}><span className="crow-flight-sprite" aria-hidden="true"/></button>}
    <button className="crow-pendant" title={mode === 'list' ? '收起今日待办' : '展开今日待办'} aria-label={mode === 'list' ? '收起今日待办' : '展开今日待办'} aria-expanded={mode === 'list'} onClick={() => void openPanel(mode === 'list' ? 'closed' : 'list')}><svg viewBox="0 0 42 58" aria-hidden="true"><path d="M20 1Q15 10 22 18" fill="none"/><path d="M23 14Q5 20 10 43Q25 47 32 23L23 14Z" fill="#a5b3a3"/><path d="M23 20L14 49M18 33L12 29M20 28L28 25" fill="none"/><path d="M13 46L9 54" fill="none"/></svg></button>
    {expanded && <section className="crow-paper" aria-label={mode === 'list' ? '今日待办列表' : '悬浮窗任务详情'}>
      <svg className="crow-paper-outline" viewBox="0 0 440 400" preserveAspectRatio="none" aria-hidden="true"><path d="M5 6L171 3L301 7L434 4L437 163L433 289L436 394L304 397L172 392L4 396L7 207Z"/></svg>
      <header className="crow-paper-heading"><span className="crow-paper-knot" aria-hidden="true"/><h2>{mode === 'list' ? '今日待办' : edit ? '编辑任务' : '枝头来信'}</h2><button className="crow-fold" aria-label="收起纸笺" onClick={() => void openPanel('closed')}>⌃</button></header>
      <div className="crow-paper-content">
        {error && <p role="alert" className="crow-error">{error}</p>}{warning && <p className="crow-warning">{warning}</p>}
        {mode === 'list' ? <div className="crow-day-list">{!sorted.length && <p className="crow-empty">今天还没有待办。</p>}{sorted.map(item => <div className={'crow-task ' + (item.completed ? 'crow-completed' : '')} key={item.id}><button className="crow-check" aria-label={(item.completed ? '取消完成 ' : '完成 ') + item.title} aria-pressed={item.completed} disabled={busy} onClick={() => toggle(item)}>{item.completed ? '✓' : ''}</button><button className="crow-task-title" onClick={() => select(item)}>{item.title}</button></div>)}</div> : !current && !edit ? <p className="crow-empty">该事项已不在今天的列表中。</p> : edit ? <form className="crow-edit" onSubmit={event => { event.preventDefault(); void run(async () => {
          if (!edit.title.trim() || !edit.id) throw new Error('请填写标题。');
          const result = unwrap(await window.desktop.editTodo(edit.id, { title: edit.title, notes: edit.notes, due: edit.due }, edit.completed, edit.revision));
          setEdit(null); setWarning(result.warnings.map(w => w.message).join('；')); await refresh();
        }); }}><label>标题<input required maxLength={200} value={edit.title} disabled={busy} onChange={event => setEdit({ ...edit, title: event.target.value })}/></label><label>备注<textarea maxLength={10000} value={edit.notes} disabled={busy} onChange={event => setEdit({ ...edit, notes: event.target.value })}/></label><DueEditor value={edit.due} onChange={due => setEdit({ ...edit, due })} child={!!planner.rootId(edit.id)&&planner.rootId(edit.id)!==edit.id} disabled={busy}/><div className="crow-form-actions"><button type="submit" disabled={busy}>保存修改</button><button type="button" disabled={busy} onClick={() => { const target = edit.id; setEdit(null); setSelected(target); setError(''); }}>取消</button></div></form> : current && <div className="crow-detail"><div className="crow-detail-title"><button className="crow-check" aria-label={(current.completed ? '取消完成 ' : '完成 ') + current.title} aria-pressed={current.completed} disabled={busy} onClick={() => toggle(current)}>{current.completed ? '✓' : ''}</button><h3 className={current.completed ? 'crow-strike' : ''}>{current.title}</h3></div>{parent && <button className="crow-back-parent" disabled={busy} onClick={() => openTask(parent)}>← 返回父任务</button>}<p className="crow-notes">{current.notes || '暂无备注'}</p><TaskMailSource key={"mail-"+current.id} notes={current.notes} widget/><p className="crow-due">{current.due.mode === 'inherit' ? '共享父项截止 · ' + deadlineText(planner.effectiveDeadline(current.id)) : deadlineText(planner.effectiveDeadline(current.id))}</p>{hasSubtaskTree && <section className="crow-subtasks" aria-label="子任务"><h3 className="crow-subtasks-heading">子任务</h3>{subtaskRows.map(({ todo: item, depth, hasChildren }) => <div className={'crow-subtask-row' + (item.id === current.id ? ' crow-subtask-current' : '') + (item.completed ? ' crow-completed' : '')} key={item.id} style={{ paddingLeft: depth * 14 }}><button className="crow-subtask-toggle" aria-label={(collapsed.has(item.id) ? '展开 ' : '折叠 ') + item.title} aria-expanded={hasChildren ? !collapsed.has(item.id) : undefined} disabled={!hasChildren} onClick={() => toggleCollapsed(item.id)}>{hasChildren ? collapsed.has(item.id) ? '▸' : '▾' : '·'}</button><button className="crow-check" aria-label={(item.completed ? '取消完成 ' : '完成 ') + item.title} aria-pressed={item.completed} disabled={busy} onClick={() => toggle(item)}>{item.completed ? '✓' : ''}</button><button className="crow-subtask-title" onClick={() => openTask(item)} title={item.title}><span>{item.title}</span><small className="crow-subtask-due">{subtaskDueText(item, planner.effectiveDeadline(item.id))}</small></button><button className="crow-subtask-edit" aria-label={'编辑 ' + item.title} disabled={busy} onClick={() => beginEdit(item)}>编辑</button></div>)}</section>}<CanvasSource key={current.id} todoId={current.id} title={current.title} revision={current.revision}/><div className="crow-form-actions"><button disabled={busy} onClick={() => beginEdit(current)}>编辑任务</button><button onClick={() => void openPanel('list')}>查看今日待办</button></div></div>}
      </div>
    </section>}
  </main>;
}
