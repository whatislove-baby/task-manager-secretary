import React from 'react';
import type { Deadline } from '../shared/todo';
import type { DuplicateTodoMatch } from './duplicate-todos';

function deadlineLabel(deadline: Deadline | null): string {
  if (!deadline) return '无截止时间';
  if (deadline.kind === 'date') return `截止时间 ${deadline.date}`;
  const { at, timeZone } = deadline;
  const time = new Intl.DateTimeFormat('zh-CN', { timeZone, dateStyle: 'medium', timeStyle: 'short' }).format(new Date(at));
  return `截止时间 ${time}（${timeZone}）`;
}

export function DuplicateTodoMatches({ matches, draftNumber }: { matches?: DuplicateTodoMatch[]; draftNumber: number }) {
  if (!matches?.length) return null;
  return <section className="tools-ai-notes" aria-label={`可能重复的已有待办（草稿 ${draftNumber}）`}>
    <strong>可能重复的已有待办</strong>
    <ul>
      {matches.map(({ todo, deadline, parentTitle, reasons }) => <li key={todo.id}>
        <strong>{todo.title}</strong> · <span>{todo.completed ? '已完成' : '未完成'}</span> · <span>{deadlineLabel(deadline)}</span>
        {parentTitle && <div>父待办 {parentTitle}</div>}
        <div>{reasons.join(' · ')}</div>
      </li>)}
    </ul>
  </section>;
}
