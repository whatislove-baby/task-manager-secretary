import type { Deadline, Todo } from './todo';
import { createTodoSchema, dueSchema } from './todo';
import { z } from 'zod';

export function localDay(date: Date): string {
  if (!Number.isFinite(date.getTime())) throw new Error('无效日期');
  return `${String(date.getFullYear()).padStart(4, '0')}-${String(date.getMonth() + 1).padStart(2, '0')}-${String(date.getDate()).padStart(2, '0')}`;
}
function calendar(day: string): Date {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(day)) throw new Error('无效日期');
  const date = new Date(`${day}T12:00:00`);
  if (!Number.isFinite(date.getTime()) || localDay(date) !== day) throw new Error('无效日期');
  return date;
}
export function shiftDay(day: string, delta: number): string {
  if (!Number.isSafeInteger(delta)) throw new Error('日期偏移必须是整数');
  const date = calendar(day); date.setDate(date.getDate() + delta);
  const result = localDay(date); calendar(result); return result;
}
export function mondayOf(day: string): string {
  return shiftDay(day, -((calendar(day).getDay() + 6) % 7));
}
const nodeSchema = z.strictObject({
  id:z.string().uuid(), parentId:z.string().uuid().nullable(), title:createTodoSchema.shape.title,
  notes:z.string().max(10000), completed:z.boolean(), due:dueSchema,
  completedAt:z.string().datetime({offset:true}).nullable().optional(),
  createdAt:z.string().datetime({offset:true}), updatedAt:z.string().datetime({offset:true}),
  revision:z.number().int().positive(), deletedAt:z.string().datetime({offset:true}).nullable(), trashBatchId:z.string().uuid().nullable(),
});
const deadlineDay = (deadline: Deadline) => deadline.kind === 'date' ? deadline.date : localDay(new Date(deadline.at));
export interface WeekEntry { todo: Todo; parentTitle: string | null; rootId: string }
/** Browser-safe isolated snapshot; paths and inherited deadlines are resolved once, iteratively. */
export function projectPlanner(todos: Todo[]) {
  const nodes = new Map<string, Todo>();
  for (const input of todos) {
    const node = nodeSchema.parse(input);
    if (nodes.has(node.id)) throw new Error('重复的待办 ID');
    if ((node.deletedAt === null) !== (node.trashBatchId === null)) throw new Error('回收状态与批次不一致');
    if (node.parentId === null && node.due.mode === 'inherit') throw new Error('根待办不能继承截止时间');
    nodes.set(node.id, node);
  }
  const roots = new Map<string, string>();
  const deadlines = new Map<string, Deadline | null>();
  for (const node of nodes.values()) {
    const path: Todo[] = []; const visiting = new Set<string>(); let current: Todo | undefined = node;
    while (current && !roots.has(current.id)) {
      if (visiting.has(current.id)) throw new Error('待办树存在循环');
      visiting.add(current.id); path.push(current);
      const parent: Todo | undefined = current.parentId === null ? undefined : nodes.get(current.parentId);
      if (!current.deletedAt && current.parentId !== null && (!parent || parent.deletedAt)) throw new Error('有效待办的父项不存在或已回收');
      current = parent;
    }
    for (let i = path.length - 1; i >= 0; i--) {
      const item = path[i];
      roots.set(item.id, item.parentId === null ? item.id : roots.get(item.parentId) ?? item.id);
      deadlines.set(item.id, item.due.mode === 'value' ? item.due.value : item.due.mode === 'none' ? null : deadlines.get(item.parentId!) ?? null);
    }
  }
  const batches = new Map<string, number>();
  for (const node of nodes.values()) if (node.trashBatchId && nodes.get(node.parentId ?? '')?.trashBatchId !== node.trashBatchId) batches.set(node.trashBatchId, (batches.get(node.trashBatchId) ?? 0) + 1);
  if ([...batches.values()].some(count => count !== 1)) throw new Error('回收批次必须是一棵连续子树');
  const ordered = [...nodes.values()].filter(t => t.deletedAt === null).sort((a,b) => Date.parse(a.createdAt) - Date.parse(b.createdAt));
  const stableRank = new Map(ordered.map((todo,index) => [todo.id,index]));
  const daily = new Map<string, Todo[]>(); const undated: Todo[] = []; const weekly = new Map<string, WeekEntry[]>();
  for (const todo of ordered) {
    const due = deadlines.get(todo.id)!;
    if (todo.parentId === null) {
      if (due) { const day = deadlineDay(due); if (!daily.has(day)) daily.set(day, []); daily.get(day)!.push(todo); }
      else undated.push(todo);
    }
    if (!due || todo.due.mode !== 'value') continue;
    const day = deadlineDay(due);
    const parent = todo.parentId === null ? null : nodes.get(todo.parentId)!;
    const parentDue = parent ? deadlines.get(parent.id) : null;
    if (parentDue && deadlineDay(parentDue) === day) continue;
    if (!weekly.has(day)) weekly.set(day, []);
    weekly.get(day)!.push({todo, parentTitle:parent?.title ?? null, rootId:roots.get(todo.id)!});
  }
  return {
    dailyRoots(day: string, order: string[] = []): Todo[] {
      calendar(day);
      const rank = new Map(order.map((id,index) => [id,index]));
      // 无截止任务未完成时每天都出现；完成后只在其完成当天出现（旧数据用 updatedAt 兜底）。
      const completedDay = (todo: Todo) => localDay(new Date(todo.completedAt ?? todo.updatedAt));
      const undatedVisible = undated.filter(todo => !todo.completed || completedDay(todo) === day);
      return structuredClone([...undatedVisible, ...daily.get(day) ?? []].sort((a,b) => {
        const aRank = rank.get(a.id), bRank = rank.get(b.id);
        if (aRank !== undefined || bRank !== undefined) return (aRank ?? Infinity) - (bRank ?? Infinity);
        return Date.parse(a.createdAt) - Date.parse(b.createdAt) || stableRank.get(a.id)! - stableRank.get(b.id)!;
      }));
    },
    weekEntries(day: string, order: string[] = []): WeekEntry[] {
      calendar(day);
      const rank = new Map(order.map((id, index) => [id, index]));
      return structuredClone([...(weekly.get(day) ?? [])].sort((a, b) => {
        const aRank = rank.get(a.todo.id) ?? rank.get(a.rootId);
        const bRank = rank.get(b.todo.id) ?? rank.get(b.rootId);
        if (aRank !== undefined || bRank !== undefined) {
          const difference = (aRank ?? Infinity) - (bRank ?? Infinity);
          if (difference) return difference;
        }
        return stableRank.get(a.todo.id)! - stableRank.get(b.todo.id)!;
      }));
    },
    rootId(id: string): string | null { return roots.get(id) ?? null; },
    effectiveDeadline(id: string): Deadline | null { return structuredClone(deadlines.get(id) ?? null); },
  };
}
