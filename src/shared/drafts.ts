import { z } from 'zod';
import { draftSchema, TodoDraft } from './todo';
export function validateDrafts(value: unknown, allowEmpty = false): TodoDraft[] {
  const drafts = z.array(draftSchema).max(100).parse(value);
  if (!allowEmpty && drafts.length === 0) throw new Error('请至少选择一项待办草稿。');
  const items = new Map<string, TodoDraft>();
  for (const draft of drafts) {
    if (items.has(draft.tempId)) throw new Error('草稿存在重复标识。');
    items.set(draft.tempId, draft);
  }
  const children = new Map<string, TodoDraft[]>();
  const ordered: TodoDraft[] = [];
  for (const draft of drafts) {
    // 分批添加时，父草稿可能已在更早批次保存；此时用 parentTodoId 指向真实待办。
    if (draft.parentTempId !== null && !items.has(draft.parentTempId) && draft.parentTodoId) ordered.push(draft);
    else if (draft.parentTempId === null) ordered.push(draft);
    else {
      if (!items.has(draft.parentTempId)) throw new Error('草稿引用了未选择或不存在的父项。');
      const branch = children.get(draft.parentTempId) || [];
      branch.push(draft); children.set(draft.parentTempId, branch);
    }
  }
  for (let i=0; i<ordered.length; i++) ordered.push(...(children.get(ordered[i].tempId) || []));
  if (ordered.length !== drafts.length) throw new Error('草稿层级存在循环。');
  return ordered;
}
