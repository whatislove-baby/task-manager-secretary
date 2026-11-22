import { randomUUID } from 'node:crypto';
import { z } from 'zod';
import {
  completionConfigSchema, createTodoSchema, defaultCompletionConfig, dueSchema, todoPatchSchema,
  type CompletionConfig, type CreateTodoInput, type Deadline, type DueWarning,
  type ResolvedDue, type Todo, type TodoPatch,
} from '../shared/todo';

const todoSchema = z.strictObject({
  id: z.string().uuid(), parentId: z.string().uuid().nullable(),
  title: createTodoSchema.shape.title, notes: z.string().max(10000), completed: z.boolean(), due: dueSchema,
  completedAt: z.string().datetime({ offset: true }).nullable().optional(),
  createdAt: z.string().datetime({ offset: true }), updatedAt: z.string().datetime({ offset: true }),
  revision: z.number().int().positive(), deletedAt: z.string().datetime({ offset: true }).nullable(),
  trashBatchId: z.string().uuid().nullable(),
});
const clone = <T>(value: T): T => structuredClone(value);
const localDay = (date: Date): string => `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, '0')}-${String(date.getDate()).padStart(2, '0')}`;
/** Date deadlines expire only after the local calendar day has ended. */
export function isOverdue(deadline: Deadline | null, now = new Date()): boolean {
  if (!deadline) return false;
  return deadline.kind === 'date' ? localDay(now) > deadline.date : now.getTime() > new Date(deadline.at).getTime();
}
function after(child: Deadline, parent: Deadline): boolean {
  if (child.kind === 'minute' && parent.kind === 'minute') return Date.parse(child.at) > Date.parse(parent.at);
  const day = (value: Deadline) => value.kind === 'date' ? value.date : localDay(new Date(value.at));
  return day(child) > day(parent);
}

/** A validated, isolated snapshot. Persistence wraps each command in its own transaction. */
export class TodoTree {
  private nodes = new Map<string, Todo>();
  private children = new Map<string, Set<string>>();
  private dueCache = new Map<string, ResolvedDue>();
  private config: CompletionConfig;

  constructor(todos: Todo[] = [], config: CompletionConfig = defaultCompletionConfig) {
    this.config = completionConfigSchema.parse(config);
    for (const input of todos) {
      const node = todoSchema.parse(input);
      if (this.nodes.has(node.id)) throw new Error('重复的待办 ID');
      if ((node.deletedAt === null) !== (node.trashBatchId === null)) throw new Error('回收状态与批次不一致');
      if (node.parentId === null && node.due.mode === 'inherit') throw new Error('根待办不能继承截止时间');
      this.nodes.set(node.id, node);
      if (node.parentId !== null) this.link(node.parentId, node.id);
    }
    for (const node of this.nodes.values()) {
      if (node.parentId === null) continue;
      const parent = this.nodes.get(node.parentId);
      if (!node.deletedAt && (!parent || parent.deletedAt)) throw new Error('有效待办的父项不存在或已回收');
    }
    // Color whole paths iteratively; each node is examined once, even in a very deep tree.
    const done = new Set<string>();
    for (const node of this.nodes.values()) {
      const path = new Set<string>();
      let current: Todo | undefined = node;
      while (current && !done.has(current.id)) {
        if (path.has(current.id)) throw new Error('待办树存在循环');
        path.add(current.id);
        current = current.parentId === null ? undefined : this.nodes.get(current.parentId);
      }
      for (const id of path) done.add(id);
    }
    const batchRoots = new Map<string, number>();
    for (const node of this.nodes.values()) {
      if (!node.trashBatchId) continue;
      const parent = node.parentId === null ? undefined : this.nodes.get(node.parentId);
      if (parent?.trashBatchId !== node.trashBatchId) {
        batchRoots.set(node.trashBatchId, (batchRoots.get(node.trashBatchId) ?? 0) + 1);
      }
    }
    if ([...batchRoots.values()].some(count => count !== 1)) throw new Error('回收批次必须是一棵连续子树');
  }

  list(includeDeleted = false): Todo[] {
    return clone([...this.nodes.values()].filter(n => includeDeleted || !n.deletedAt)
      .sort((a, b) => Date.parse(a.createdAt) - Date.parse(b.createdAt)));
  }
  get(id: string): Todo { return clone(this.require(id)); }
  getConfig(): CompletionConfig { return clone(this.config); }
  setConfig(config: CompletionConfig): void { this.config = completionConfigSchema.parse(config); }

  create(input: CreateTodoInput, id:string = randomUUID()): Todo {
    z.string().uuid().parse(id);
    if (this.nodes.has(id)) throw new Error('待办标识已存在，请重新确认草稿。');
    const parsed = createTodoSchema.parse(input);
    const parentId = parsed.parentId ?? null;
    if (parentId !== null) this.active(parentId);
    const due = parsed.due ?? { mode: parentId === null ? 'none' as const : 'inherit' as const };
    if (parentId === null && due.mode === 'inherit') throw new Error('根待办不能继承截止时间');
    const now = new Date().toISOString();
    const node: Todo = { id, parentId, title: parsed.title, notes: parsed.notes ?? '', completed: false,
      completedAt: null, due, createdAt: now, updatedAt: now, revision: 1, deletedAt: null, trashBatchId: null };
    this.nodes.set(node.id, node);
    if (parentId !== null) this.link(parentId, node.id);
    const touched = new Set<string>();
    this.reconcile(parentId, touched);
    this.finish(touched);
    return clone(node);
  }

  update(id: string, patch: TodoPatch, expectedRevision: number): Todo {
    const node = this.editable(id, expectedRevision);
    const parsed = todoPatchSchema.parse(patch);
    if (node.parentId === null && parsed.due?.mode === 'inherit') throw new Error('根待办不能继承截止时间');
    const changes = Object.fromEntries(Object.entries(parsed).filter(([, value]) => value !== undefined));
    if (!Object.keys(changes).length) throw new Error('没有修改内容');
    Object.assign(node, changes);
    this.finish(new Set([id]));
    return clone(node);
  }

  move(id: string, parentId: string | null, expectedRevision: number): Todo {
    const node = this.editable(id, expectedRevision);
    if (parentId !== null) {
      let current: Todo | undefined = this.active(parentId);
      while (current) {
        if (current.id === id) throw new Error('不能移动到自身或自己的后代');
        current = current.parentId === null ? undefined : this.nodes.get(current.parentId);
      }
    }
    const oldParent = node.parentId;
    if (oldParent !== null) this.children.get(oldParent)?.delete(id);
    node.parentId = parentId;
    if (parentId !== null) this.link(parentId, id);
    else if (node.due.mode === 'inherit') node.due = { mode: 'none' };
    const touched = new Set([id]);
    this.reconcile(oldParent, touched);
    this.reconcile(parentId, touched);
    this.finish(touched);
    return clone(node);
  }

  setCompleted(id: string, completed: boolean, expectedRevision: number): Todo {
    const node = this.editable(id, expectedRevision);
    if (typeof completed !== 'boolean') throw new Error('完成状态必须是布尔值');
    const touched = new Set([id]);
    const at = new Date().toISOString();
    node.completed = completed;
    node.completedAt = completed ? at : null;
    if (completed && this.config.cascadeComplete) {
      for (const descendant of this.subtree(id)) {
        if (!descendant.completed) { descendant.completed = true; descendant.completedAt = at; touched.add(descendant.id); }
      }
    }
    // Start above the target so an explicitly reopened parent stays reopened.
    this.reconcile(node.parentId, touched);
    this.finish(touched);
    return clone(node);
  }

  trash(id: string, expectedRevision: number): string {
    const node = this.editable(id, expectedRevision);
    const batchId = randomUUID();
    const deletedAt = new Date().toISOString();
    const touched = new Set<string>();
    const subtree = this.subtree(id);
    for (const item of subtree) {
      item.deletedAt = deletedAt; item.trashBatchId = batchId; touched.add(item.id);
    }
    this.reconcile(node.parentId, touched);
    this.finish(touched);
    return batchId;
  }

  restore(batchId: string): Todo[] {
    const batch = [...this.nodes.values()].filter(n => n.deletedAt !== null && n.trashBatchId === batchId);
    if (!batch.length) return [];
    const members = new Set(batch.map(n => n.id));
    const externalParents = new Set<string>();
    const touched = new Set<string>();
    for (const node of batch) {
      if (node.parentId !== null && !members.has(node.parentId)) {
        const parent = this.nodes.get(node.parentId);
        if (parent && !parent.deletedAt) externalParents.add(parent.id);
        else {
          this.children.get(node.parentId)?.delete(node.id);
          node.parentId = null;
          if (node.due.mode === 'inherit') node.due = { mode: 'none' };
        }
      }
      node.deletedAt = null; node.trashBatchId = null; touched.add(node.id);
    }
    for (const parentId of externalParents) this.reconcile(parentId, touched);
    this.finish(touched);
    return clone(batch);
  }

  effectiveDue(id: string): ResolvedDue { this.require(id); return clone(this.resolveDue(id)); }
  warnings(): DueWarning[] {
    const result: DueWarning[] = [];
    for (const node of this.nodes.values()) {
      if (node.deletedAt || node.parentId === null) continue;
      const own = this.resolveDue(node.id).deadline;
      const parent = this.resolveDue(node.parentId).deadline;
      if (own && parent && after(own, parent)) result.push({ todoId: node.id, parentId: node.parentId,
        code: 'AFTER_PARENT', message: '子待办截止时间晚于父待办，仍可保存。' });
    }
    return result;
  }

  private resolveDue(id: string): ResolvedDue {
    const pending: Todo[] = [];
    let current = this.require(id);
    while (!this.dueCache.has(current.id)) {
      pending.push(current);
      if (current.due.mode !== 'inherit' || current.parentId === null || !this.nodes.has(current.parentId)) break;
      current = this.require(current.parentId);
    }
    for (let i = pending.length - 1; i >= 0; i--) {
      const node = pending[i];
      if (node.due.mode === 'value') this.dueCache.set(node.id, { deadline: node.due.value, displayedDue: node.due.value, inherited: false, sourceId: node.id });
      else if (node.due.mode === 'none') this.dueCache.set(node.id, { deadline: null, displayedDue: null, inherited: false, sourceId: null });
      else {
        const parent = node.parentId === null ? undefined : this.dueCache.get(node.parentId);
        this.dueCache.set(node.id, { deadline: parent?.deadline ?? null, displayedDue: null, inherited: true, sourceId: parent?.sourceId ?? null });
      }
    }
    return this.dueCache.get(id)!;
  }
  private require(id: string): Todo {
    const node = this.nodes.get(id);
    if (!node) throw new Error('待办不存在');
    return node;
  }
  private active(id: string): Todo {
    const node = this.require(id);
    if (node.deletedAt) throw new Error('待办已在回收站');
    return node;
  }
  private editable(id: string, revision: number): Todo {
    const node = this.active(id);
    if (!Number.isInteger(revision) || revision !== node.revision) throw new Error('版本冲突：待办已被修改，请刷新后重试');
    return node;
  }
  private link(parentId: string, id: string): void {
    if (!this.children.has(parentId)) this.children.set(parentId, new Set());
    this.children.get(parentId)!.add(id);
  }
  private subtree(id: string): Todo[] {
    const result: Todo[] = [];
    const stack = [id];
    while (stack.length) {
      const node = this.require(stack.pop()!);
      if (node.deletedAt) continue;
      result.push(node);
      for (const child of this.children.get(node.id) ?? []) stack.push(child);
    }
    return result;
  }
  private reconcile(parentId: string | null, touched: Set<string>): void {
    if (!this.config.autoCompleteParent) return;
    const at = new Date().toISOString();
    let current = parentId;
    while (current !== null) {
      const parent = this.nodes.get(current);
      if (!parent || parent.deletedAt) break;
      let count = 0;
      let all = true;
      for (const id of this.children.get(current) ?? []) {
        const child = this.require(id);
        if (!child.deletedAt) { count++; all = all && child.completed; }
      }
      if (count && parent.completed !== all) { parent.completed = all; parent.completedAt = all ? at : null; touched.add(parent.id); }
      current = parent.parentId;
    }
  }
  private finish(touched: Set<string>): void {
    const now = Date.now();
    for (const id of touched) {
      const node = this.require(id);
      node.revision++;
      node.updatedAt = new Date(Math.max(now, Date.parse(node.updatedAt) + 1)).toISOString();
    }
    this.dueCache.clear();
  }
}
