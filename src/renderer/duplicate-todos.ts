import type { Deadline, Todo, TodoDraft } from '../shared/todo';

export interface DuplicateTodoMatch {
  todo: Todo;
  deadline: Deadline | null;
  parentTitle: string | null;
  reasons: string[];
}

function normalizedTitle(title: string): string {
  return title.normalize('NFKC').toLowerCase().replace(/\s+/gu, ' ').trim().replace(/[.。!?]+$/u, '').trim();
}

function mailIdsFromNotes(notes: string): string[] {
  return Array.from(notes.matchAll(/^[ \t]*本地邮件：[ \t]*([a-f0-9]{64})[ \t\r]*$/gim), match => match[1].toLowerCase());
}

function deadlineDay(deadline: Deadline): string | null {
  if (deadline.kind === 'date') return deadline.date;
  const at = Date.parse(deadline.at);
  if (!Number.isFinite(at)) return null;
  return new Intl.DateTimeFormat('sv-SE', { timeZone: deadline.timeZone, dateStyle: 'short' }).format(at);
}

function sameDeadline(left: Deadline | null, right: Deadline | null): boolean {
  if (!left || !right) return false;
  if (left.kind === 'date') return left.date === deadlineDay(right);
  if (right.kind === 'date') return deadlineDay(left) === right.date;
  return Date.parse(left.at) === Date.parse(right.at);
}

export function findDuplicateTodos(
  drafts: TodoDraft[],
  todos: Todo[],
  added: ReadonlySet<string>,
  addedTodos?: Readonly<Record<string, string>>,
): Map<string, DuplicateTodoMatch[]> {
  const activeTodos = new Map(todos.filter(todo => !todo.deletedAt).map(todo => [todo.id, todo]));
  const draftById = new Map(drafts.map(draft => [draft.tempId, draft]));
  const todosByTitle = new Map<string, Todo[]>();
  for (const todo of activeTodos.values()) {
    const title = normalizedTitle(todo.title);
    const group = todosByTitle.get(title) ?? [];
    group.push(todo);
    todosByTitle.set(title, group);
  }

  function parentOf(item: Todo | TodoDraft): Todo | TodoDraft | undefined {
    if (!('tempId' in item)) return activeTodos.get(item.parentId ?? '');
    if (!item.parentTempId) return undefined;
    const parentDraft = draftById.get(item.parentTempId);
    // A parent saved in an earlier batch may have been edited since the draft was created.
    const savedParentId = added.has(item.parentTempId) ? addedTodos?.[item.parentTempId] ?? parentDraft?.todoId : undefined;
    if (savedParentId) return activeTodos.get(savedParentId);
    return parentDraft ?? activeTodos.get(item.parentTodoId ?? '');
  }

  function effectiveDeadline(item: Todo | TodoDraft): Deadline | null {
    const visited = new Set<Todo | TodoDraft>();
    let current: Todo | TodoDraft | undefined = item;
    while (current && !visited.has(current)) {
      if (current.due?.mode === 'value') return current.due.value;
      // The repository defaults an omitted deadline to inheritance for children.
      if (current.due?.mode === 'none') break;
      visited.add(current);
      current = parentOf(current);
    }
    return null;
  }

  const matches = new Map<string, DuplicateTodoMatch[]>();
  for (const draft of drafts) {
    const title = normalizedTitle(draft.title);
    const candidates = todosByTitle.get(title);
    if (!title || !candidates || added.has(draft.tempId)) continue;
    const sourceMailIds = new Set(
      draft.sourceMailIds?.length ? draft.sourceMailIds.map(id => id.toLowerCase()) : mailIdsFromNotes(draft.notes ?? ''),
    );
    const draftDeadline = effectiveDeadline(draft);
    const similar: DuplicateTodoMatch[] = [];
    for (const todo of candidates) {
      if (todo.id === draft.todoId) continue;
      const todoMailIds = mailIdsFromNotes(todo.notes);
      const sharedMail = todoMailIds.some(id => sourceMailIds.has(id));
      const deadline = effectiveDeadline(todo);
      const matchingDeadline = sameDeadline(draftDeadline, deadline);
      if (!sharedMail && (!matchingDeadline || (sourceMailIds.size > 0 && todoMailIds.length > 0))) continue;
      const reasons = ['标题相同'];
      if (sharedMail) reasons.push('来源邮件相同');
      if (matchingDeadline) reasons.push('截止日期相同');
      else if (draftDeadline || deadline) reasons.push('截止时间不同');
      const parentTitle = activeTodos.get(todo.parentId ?? '')?.title ?? null;
      similar.push({ todo, deadline, parentTitle, reasons });
    }
    if (similar.length) matches.set(draft.tempId, similar);
  }
  return matches;
}
