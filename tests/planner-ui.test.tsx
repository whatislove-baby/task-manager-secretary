import React from 'react';
import { test, afterEach } from 'node:test';
import assert from 'node:assert/strict';
import { JSDOM } from 'jsdom';
import { createRequire } from 'node:module';
import { App } from '../src/renderer/App';
import { localDay, shiftDay, mondayOf } from '../src/shared/planner';
import type { Todo } from '../src/shared/todo';
import type { DesktopAPI } from '../src/shared/contracts';

const dom = new JSDOM('<!doctype html><html><body></body></html>', { url: 'http://localhost' });
Object.defineProperty(globalThis, 'window', { value: dom.window, configurable: true });
Object.defineProperty(globalThis, 'document', { value: dom.window.document, configurable: true });
Object.defineProperty(globalThis, 'navigator', { value: dom.window.navigator, configurable: true });
Object.assign(globalThis, { HTMLElement: dom.window.HTMLElement, requestAnimationFrame: (callback: FrameRequestCallback) => { callback(0); return 0; } });
dom.window.HTMLElement.prototype.scrollIntoView = function () {};
const { render, fireEvent, waitFor, cleanup, within } = createRequire(process.cwd() + '/package.json')('@testing-library/react') as typeof import('@testing-library/react');
afterEach(() => cleanup());
const today = localDay(new Date());
let serial = 0;
function todo(title: string, overrides: Partial<Todo> = {}): Todo {
  serial++;
  return { id: `00000000-0000-4000-8000-${String(serial).padStart(12, '0')}`, parentId: null, title, notes: '', due: { mode: 'none' }, completed: false, createdAt: new Date(2026, 0, 1, 0, serial).toISOString(), updatedAt: new Date().toISOString(), revision: 1, deletedAt: null, trashBatchId: null, ...overrides };
}
function api(items: Todo[], overrides: Partial<DesktopAPI> = {}) {
  const calls: { day: string; ids: string[] }[] = [];
  let state = items;
  window.desktop = {
    getCanvasSource: async () => ({ ok: true, data: null }),
    listTodos: async () => ({ ok: true, data: structuredClone(state) }),
    getDayOrder: async () => ({ ok: true, data: [] }),
    setDayOrder: async (day: string, ids: string[]) => { calls.push({ day, ids }); return { ok: true, data: ids }; },
    setCompleted: async (id, completed) => { const item = state.find(t => t.id === id)!; Object.assign(item, { completed, revision: item.revision + 1 }); return { ok: true, data: { value: structuredClone(item), warnings: [] } }; },
    editTodo: async (id, patch) => { const item = state.find(t => t.id === id)!; Object.assign(item, patch, { revision: item.revision + 1 }); return { ok: true, data: { value: structuredClone(item), warnings: [] } }; },
    createTodo: async input => { const item = todo(input.title, { notes: input.notes ?? '', due: input.due ?? { mode: 'none' }, parentId: input.parentId ?? null }); state = [...state, item]; return { ok: true, data: { value: item, warnings: [] } }; },
    ...overrides,
  } as DesktopAPI;
  return calls;
}

test('周格子项打开完整父树，左侧不插入异日父项或子项', async () => {
  const other = shiftDay(today, 10);
  const parent = todo('异日父任务', { due: { mode: 'value', value: { kind: 'date', date: other } } });
  const child = todo('今日独立截止子项', { parentId: parent.id, due: { mode: 'value', value: { kind: 'date', date: today } } });
  api([parent, child]);
  const ui = render(<App />);
  await waitFor(() => assert.ok(ui.getByLabelText('当天根任务')));
  const left = within(ui.getByLabelText('当天根任务'));
  assert.equal(left.queryByText(parent.title), null);
  assert.equal(left.queryByText(child.title), null);
  fireEvent.click(ui.getByTitle(child.title + ' · ' + parent.title));
  await waitFor(() => assert.ok(within(ui.getByLabelText('只读任务详情')).getByText(child.title)));
  assert.ok(!ui.queryByLabelText('任务标题'));
  assert.ok(within(ui.getByLabelText('完整任务树')).getByText(parent.title));
});

test('编辑失败保留输入，dirty切日须经过自定义保存/放弃确认', async () => {
  const item = todo('保留输入');
  api([item], { editTodo: async () => ({ ok: false, error: '磁盘写入失败' }) });
  const ui = render(<App />);
  await waitFor(() => assert.ok(ui.getByLabelText('当天根任务')));
  fireEvent.click(within(ui.getByLabelText('当天根任务')).getByText(item.title));
  fireEvent.click(ui.getByRole('button', { name: '编辑任务' }));
  fireEvent.change(ui.getByLabelText('任务标题'), { target: { value: '尚未保存的标题' } });
  fireEvent.click(ui.getByRole('button', { name: '后一周' }));
  assert.ok(ui.getByRole('alertdialog'));
  fireEvent.click(ui.getByRole('button', { name: '保存并切换' }));
  await waitFor(() => assert.ok(within(ui.getByRole('alertdialog')).getByText('磁盘写入失败')));
  assert.equal(ui.container.querySelector('.notification'), null);
  assert.ok(ui.getByRole('alertdialog'));
  assert.equal((ui.getByLabelText('任务标题') as HTMLInputElement).value, '尚未保存的标题');
  fireEvent.click(ui.getByRole('button', { name: '继续编辑' }));
  assert.ok(!ui.queryByRole('alertdialog'));
});

test('排序失败保持原序，排序成功按所选日保存', async () => {
  const first = todo('任务甲'), second = todo('任务乙');
  let fail = true;
  const calls: string[] = [];
  api([first, second], { setDayOrder: async (day, ids) => { calls.push(day); return fail ? { ok: false, error: '排列保存失败' } : { ok: true, data: ids }; } });
  const ui = render(<App />);
  await waitFor(() => assert.equal((ui.getByRole('button', { name: '下移 任务甲' }) as HTMLButtonElement).disabled, false));
  fireEvent.click(ui.getByRole('button', { name: '下移 任务甲' }));
  await waitFor(() => assert.ok(ui.getByText('排列保存失败')));
  assert.ok(ui.getByText('排列保存失败').closest('.task-panel-scroll'));
  assert.equal(ui.container.querySelector('.planner-app > [role=alert]'), null);
  let titles = within(ui.getByLabelText('当天根任务')).getAllByRole('button').filter(b => b.classList.contains('root-title'));
  assert.equal(titles[0]?.textContent?.includes('任务甲'), true);
  fail = false;
  fireEvent.click(ui.getByRole('button', { name: '下移 任务甲' }));
  await waitFor(() => { titles = within(ui.getByLabelText('当天根任务')).getAllByRole('button').filter(b => b.classList.contains('root-title')); assert.equal(titles[0]?.textContent?.includes('任务乙'), true); });
  assert.deepEqual(calls, [today, today]);
});

test('新建根任务默认仅日期且今天，表单位于左侧滚动体', async () => {
  api([]);
  const ui = render(<App />);
  await waitFor(() => assert.ok(ui.getByLabelText('创建根任务')));
  const scrollBody = ui.getByLabelText('当天根任务').closest('.task-panel-scroll') as HTMLElement;
  scrollBody.scrollTop = 900;
  fireEvent.click(ui.getByLabelText('创建根任务'));
  assert.equal(scrollBody.scrollTop, 0);
  assert.equal(ui.getByLabelText('新任务标题').closest('.task-panel-scroll'), scrollBody);
  assert.equal((ui.getByLabelText('截止方式') as HTMLSelectElement).value, 'date');
  assert.equal((ui.getByLabelText('截止日期') as HTMLInputElement).value, today);
  fireEvent.change(ui.getByLabelText('新任务标题'), { target: { value: '今日的新任务' } });
  fireEvent.click(ui.getByRole('button', { name: '创建任务' }));
  await waitFor(() => assert.ok(within(ui.getByLabelText('当天根任务')).getByText('今日的新任务')));
  assert.ok(within(ui.getByRole('region', { name: '每周计划' })).getByText('今日的新任务'));
});


test('切日后忽略上一日迟到的排序响应', async () => {
  const first = todo('旧日顺序甲'), second = todo('旧日顺序乙');
  let resolveOld: ((value: { ok: true; data: string[] }) => void) | undefined;
  const calls: string[] = [];
  api([first, second], { getDayOrder: async day => {
    calls.push(day);
    if (day === today) return await new Promise(resolve => { resolveOld = resolve; });
    return { ok: true, data: [first.id, second.id] };
  } });
  const ui = render(<App />);
  await waitFor(() => assert.ok(resolveOld));
  fireEvent.click(ui.getByRole('button', { name: '后一周' }));
  await waitFor(() => assert.equal(calls.length, 14));
  resolveOld!({ ok: true, data: [second.id, first.id] });
  await waitFor(() => {
    const titles = within(ui.getByLabelText('当天根任务')).getAllByRole('button').filter(b => b.classList.contains('root-title'));
    assert.equal(titles[0]?.textContent?.includes(first.title), true);
  });
  assert.ok(calls.includes(shiftDay(mondayOf(today), 7)));
});


test('选择任务默认只读，显式编辑预填，取消及保存成功退出编辑', async () => {
  const item = todo('默认只读任务', { notes: '现有的多行\n备注', due: { mode: 'value', value: { kind: 'date', date: today } } });
  api([item]);
  const ui = render(<App />);
  await waitFor(() => assert.ok(ui.getByLabelText('当天根任务')));
  fireEvent.click(within(ui.getByLabelText('当天根任务')).getByText(item.title));
  const details = ui.getByLabelText('只读任务详情');
  assert.equal(details.querySelectorAll('input,textarea,select').length, 0);
  assert.ok(!ui.queryByLabelText('任务标题'));
  assert.equal(within(details).getByText(/现有的多行/).textContent, item.notes);
  fireEvent.click(ui.getByRole('button', { name: '编辑任务' }));
  assert.equal((ui.getByLabelText('任务标题') as HTMLInputElement).value, item.title);
  assert.equal((ui.getByLabelText('备注') as HTMLTextAreaElement).value, item.notes);
  assert.equal((ui.getByLabelText('截止日期') as HTMLInputElement).value, today);
  fireEvent.change(ui.getByLabelText('任务标题'), { target: { value: '取消的标题' } });
  fireEvent.click(ui.getByRole('button', { name: '取消' }));
  assert.ok(!ui.queryByLabelText('任务标题'));
  assert.ok(within(ui.getByLabelText('只读任务详情')).getByText(item.title));
  fireEvent.click(ui.getByRole('button', { name: '编辑任务' }));
  fireEvent.change(ui.getByLabelText('任务标题'), { target: { value: '已保存的新标题' } });
  fireEvent.click(ui.getByRole('button', { name: '保存更改' }));
  await waitFor(() => assert.ok(!ui.queryByLabelText('任务标题')));
  assert.ok(within(ui.getByLabelText('只读任务详情')).getByText('已保存的新标题'));
});

test('前后周返回本周自动选今天，其他周选择周一', async () => {
  api([]);
  const ui = render(<App />);
  await waitFor(() => assert.ok(ui.getByLabelText('当天根任务')));
  fireEvent.click(ui.getByRole('button', { name: '前一周' }));
  let cells = Array.from(ui.container.querySelectorAll('.day-cell'));
  assert.ok(cells[0]?.classList.contains('selected-day'));
  fireEvent.click(ui.getByRole('button', { name: '后一周' }));
  assert.ok(ui.container.querySelector('.is-today.selected-day'));
  fireEvent.click(ui.getByRole('button', { name: '后一周' }));
  cells = Array.from(ui.container.querySelectorAll('.day-cell'));
  assert.ok(cells[0]?.classList.contains('selected-day'));
  fireEvent.click(ui.getByRole('button', { name: '前一周' }));
  assert.ok(ui.container.querySelector('.is-today.selected-day'));
});

test('点击周格空白和空态选中该日，标题按钮仍可键盘使用', async () => {
  api([]);
  const ui = render(<App />);
  await waitFor(() => assert.ok(ui.getByLabelText('当天根任务')));
  const cells = Array.from(ui.container.querySelectorAll<HTMLElement>('.day-cell'));
  const other = cells.find(cell => !cell.classList.contains('selected-day'))!;
  fireEvent.click(other);
  assert.ok(other.classList.contains('selected-day'));
  const another = cells.find(cell => cell !== other)!;
  fireEvent.click(another.querySelector('.day-empty')!);
  assert.ok(another.classList.contains('selected-day'));
  const header = other.querySelector('button.day-heading')!;
  fireEvent.click(header);
  assert.equal(header.getAttribute('aria-pressed'), 'true');
});


test('选择其他日期后新建仍默认今天，取消新建子项返回其直接父项只读详情', async () => {
  const root = todo('根事项');
  const parent = todo('嵌套父事项', { parentId: root.id });
  api([root, parent]);
  const ui = render(<App />);
  await waitFor(() => assert.ok(ui.getByLabelText('当天根任务')));
  const other = ui.container.querySelector('.day-cell:not(.is-today)')!;
  fireEvent.click(other);
  fireEvent.click(ui.getByLabelText('创建根任务'));
  assert.equal((ui.getByLabelText('截止日期') as HTMLInputElement).value, today);
  fireEvent.click(ui.getByRole('button', { name: '取消' }));
  fireEvent.click(within(ui.getByLabelText('当天根任务')).getByText(root.title));
  fireEvent.click(ui.getByLabelText('为 ' + parent.title + ' 添加子任务'));
  assert.equal((ui.getByLabelText('截止方式') as HTMLSelectElement).value, 'date');
  assert.equal((ui.getByLabelText('截止日期') as HTMLInputElement).value, today);
  fireEvent.change(ui.getByLabelText('新任务标题'), { target: { value: '不保存的子项' } });
  fireEvent.click(ui.getByRole('button', { name: '取消' }));
  assert.ok(within(ui.getByLabelText('只读任务详情')).getByText(parent.title));
  assert.equal(ui.queryByLabelText('新任务标题'), null);
  assert.equal(ui.queryByText('完整任务树'), null);
  assert.equal(ui.queryByRole('button', { name: '标记完成' }), null);
  assert.equal(ui.queryByRole('button', { name: '添加子任务' }), null);
  assert.ok(ui.getByLabelText('为 ' + parent.title + ' 添加子任务'));
  assert.ok(within(ui.getByLabelText('完整任务树')).getByRole('button', { name: '完成 ' + parent.title }));
});

test('周格小圆点可完成再取消完成，不改变日期和当前详情；失败不改变状态', async () => {
  const current = todo('保留详情事项');
  const preview = todo('周格快捷事项', { due: { mode: 'value', value: { kind: 'date', date: today } } });
  let fail = false;
  const calls: boolean[] = [];
  api([current, preview], { setCompleted: async (_id, completed) => {
    calls.push(completed);
    if (fail) return { ok: false, error: '完成保存失败' };
    preview.completed = completed; preview.revision++;
    return { ok: true, data: { value: structuredClone(preview), warnings: [] } };
  } });
  const ui = render(<App />);
  await waitFor(() => assert.ok(ui.getByLabelText('当天根任务')));
  fireEvent.click(ui.container.querySelector('.day-cell:not(.is-today)')!);
  fireEvent.click(within(ui.getByLabelText('当天根任务')).getByText(current.title));
  const selectedDay = ui.container.querySelector('.selected-day');
  const week = within(ui.getByRole('region', { name: '每周计划' }));
  fireEvent.click(week.getByRole('button', { name: '完成 ' + preview.title }));
  await waitFor(() => assert.ok(week.getByRole('button', { name: '重新打开 ' + preview.title })));
  assert.equal(ui.queryByText('已完成任务'), null);
  assert.equal(ui.queryByText('已重新打开任务'), null);
  assert.equal(ui.queryByText('完成状态'), null);
  assert.equal(ui.container.querySelector('.selected-day'), selectedDay);
  assert.ok(within(ui.getByLabelText('只读任务详情')).getByText(current.title));
  fireEvent.click(week.getByRole('button', { name: '重新打开 ' + preview.title }));
  await waitFor(() => assert.ok(week.getByRole('button', { name: '完成 ' + preview.title })));
  fail = true;
  fireEvent.click(week.getByRole('button', { name: '完成 ' + preview.title }));
  await waitFor(() => assert.ok(ui.getByText('完成保存失败')));
  assert.equal(week.getByRole('button', { name: '完成 ' + preview.title }).getAttribute('aria-pressed'), 'false');
  assert.deepEqual(calls, [true, false, true]);
});


const transfer = () => ({ effectAllowed: '', dropEffect: '', setData() {} });
function listTitles(container: HTMLElement) { return Array.from(container.querySelectorAll('.root-title strong')).map(item => item.textContent); }
function weekTitles(container: HTMLElement) { return Array.from(container.querySelectorAll('.is-today .week-entry-title>span')).map(item => item.textContent); }

test('整条任务拖动时列表与周格实时预览，drop仅保存一次，dragEnd不回滚，重开保持顺序', async () => {
  const a = todo('拖甲', { due: { mode: 'value', value: { kind: 'date', date: today } } });
  const b = todo('拖乙', { due: a.due });
  let saved: string[] = []; let writes = 0;
  api([a, b], { getDayOrder: async date => ({ ok: true, data: date === today ? saved : [] }), setDayOrder: async (_day, ids) => { writes++; saved = [...ids]; return { ok: true, data: ids }; } });
  let ui = render(<App />);
  await waitFor(() => assert.equal(ui.container.querySelector('.root-row')?.getAttribute('draggable'), 'true'));
  const first = ui.container.querySelectorAll<HTMLElement>('.root-row')[0]!, second = ui.container.querySelectorAll<HTMLElement>('.root-row')[1]!;
  const dataTransfer = transfer();
  fireEvent.dragStart(first, { dataTransfer }); fireEvent.dragOver(second, { dataTransfer });
  assert.deepEqual(listTitles(ui.container), ['拖乙', '拖甲']);
  assert.deepEqual(weekTitles(ui.container), ['拖乙', '拖甲']); assert.equal(writes, 0);
  fireEvent.drop(second, { dataTransfer }); fireEvent.dragEnd(first, { dataTransfer });
  await waitFor(() => assert.equal(ui.container.querySelector('.root-row')?.getAttribute('draggable'), 'true'));
  assert.equal(writes, 1); assert.deepEqual(saved, [b.id, a.id]);
  ui.unmount(); ui = render(<App />);
  await waitFor(() => assert.deepEqual(weekTitles(ui.container), ['拖乙', '拖甲']));
  assert.deepEqual(listTitles(ui.container), ['拖乙', '拖甲']);
});

test('拖出列表取消、Esc和保存失败均回滚两个视图；编辑未保存时不能拖动', async () => {
  const a = todo('回滚甲', { due: { mode: 'value', value: { kind: 'date', date: today } } });
  const b = todo('回滚乙', { due: a.due }); let writes = 0;
  api([a, b], { setDayOrder: async () => { writes++; return { ok: false, error: '顺序磁盘失败' }; } });
  const ui = render(<App />); const dataTransfer = transfer();
  await waitFor(() => assert.equal(ui.container.querySelector('.root-row')?.getAttribute('draggable'), 'true'));
  const row = (title: string) => within(ui.getByLabelText('当天根任务')).getByText(title).closest('article')!;
  fireEvent.dragStart(row(a.title), { dataTransfer }); fireEvent.dragOver(row(b.title), { dataTransfer });
  fireEvent.dragEnd(row(a.title));
  assert.deepEqual(listTitles(ui.container), [a.title, b.title]); assert.deepEqual(weekTitles(ui.container), [a.title, b.title]); assert.equal(writes, 0);
  fireEvent.dragStart(row(a.title), { dataTransfer }); fireEvent.dragOver(row(b.title), { dataTransfer });
  fireEvent.keyDown(window, { key: 'Escape' });
  assert.deepEqual(weekTitles(ui.container), [a.title, b.title]);
  fireEvent.dragStart(row(a.title), { dataTransfer }); fireEvent.dragOver(row(b.title), { dataTransfer }); fireEvent.drop(row(b.title), { dataTransfer });
  await waitFor(() => assert.ok(ui.getByText('顺序磁盘失败')));
  assert.deepEqual(listTitles(ui.container), [a.title, b.title]); assert.deepEqual(weekTitles(ui.container), [a.title, b.title]); assert.equal(writes, 1);
  fireEvent.click(within(ui.getByLabelText('当天根任务')).getByText(a.title)); fireEvent.click(ui.getByRole('button', { name: '编辑任务' }));
  fireEvent.change(ui.getByLabelText('任务标题'), { target: { value: '未保存' } });
  assert.equal(row(a.title).getAttribute('draggable'), 'false'); fireEvent.dragStart(row(a.title), { dataTransfer }); fireEvent.dragOver(row(b.title), { dataTransfer });
  assert.deepEqual(listTitles(ui.container), [a.title, b.title]); assert.equal((ui.getByLabelText('任务标题') as HTMLInputElement).value, '未保存');
});

test('未选中日期的周格也加载持久顺序，切日与列表一致', async () => {
  const target = shiftDay(mondayOf(today), (new Date(today + 'T12:00:00').getDay() % 7 + 2) % 7);
  const date = target === today ? shiftDay(mondayOf(today), (new Date(today + 'T12:00:00').getDay() % 7 + 3) % 7) : target;
  const a = todo('异日甲', { due: { mode: 'value', value: { kind: 'date', date } } }); const b = todo('异日乙', { due: a.due });
  api([a, b], { getDayOrder: async day => ({ ok: true, data: day === date ? [b.id, a.id] : [] }) });
  const ui = render(<App />);
  await waitFor(() => assert.deepEqual(Array.from(ui.container.querySelectorAll('.week-entry-title>span')).map(node => node.textContent), [b.title, a.title]));
  fireEvent.click(ui.getByTitle(b.title).closest('.day-cell')!);
  assert.deepEqual(listTitles(ui.container), [b.title, a.title]);
});


test('创建错误留在表单内，重试成功静默且无任何顶部反馈容器', async () => {
  let fail = true;
  const created = todo('静默创建');
  let state: Todo[] = [];
  api([], {
    getCanvasSource: async () => ({ ok: true, data: null }),
    listTodos: async () => ({ ok: true, data: structuredClone(state) }),
    createTodo: async () => {
      if (fail) return { ok: false, error: '创建暂时失败' };
      state = [created];
      return { ok: true, data: { value: created, warnings: [] } };
    },
  });
  const ui = render(<App />);
  await waitFor(() => assert.ok(ui.getByLabelText('当天根任务')));
  fireEvent.click(ui.getByLabelText('创建根任务'));
  fireEvent.change(ui.getByLabelText('新任务标题'), { target: { value: created.title } });
  fireEvent.click(ui.getByRole('button', { name: '创建任务' }));
  await waitFor(() => assert.ok(ui.getByRole('alert')));
  assert.ok(ui.getByRole('alert').closest('form.root-create-form'));
  assert.equal(ui.container.querySelector('.notification'), null);
  assert.equal(ui.container.querySelector('.planner-app > [role=alert]'), null);
  fail = false;
  fireEvent.click(ui.getByRole('button', { name: '创建任务' }));
  await waitFor(() => assert.ok(within(ui.getByLabelText('当天根任务')).getByText(created.title)));
  assert.equal(ui.queryByRole('alert'), null);
  assert.equal(ui.container.querySelector('.planner-app > [role=status]'), null);
  assert.equal(ui.container.querySelector('.notification'), null);
  assert.equal(ui.queryByText('已保存。'), null);
});


test('主窗悬浮入口与跨窗同步保留编辑草稿及原revision', async () => {
  const item = todo('跨窗任务'); let state = [item]; const listeners = new Set<() => void>(); const changed = () => { for(const listener of [...listeners]) listener(); }; let enabled = false; let sentRevision = 0;
  window.widget = {
    getState: async () => ({ enabled }), setEnabled: async value => { enabled = value; },
    setExpanded: async () => {}, close: async () => {}, showMain: async () => {}, quit: async () => {},
    onChanged: callback => { listeners.add(callback); return () => { listeners.delete(callback); }; },
  };
  try {
    api([], { getCanvasSource: async () => ({ ok: true, data: null }),
    listTodos: async () => ({ ok: true, data: structuredClone(state) }), editTodo: async (_id, _patch, _completed, revision) => { sentRevision = revision; return { ok: false, error: '版本已变化，请重新核对' }; } });
    const ui = render(<App />);
    await waitFor(() => assert.ok(within(ui.getByLabelText('当天根任务')).getByText(item.title)));
    fireEvent.click(ui.getByRole('button', { name: '开启悬浮窗' }));
    await waitFor(() => assert.ok(ui.getByRole('button', { name: '关闭悬浮窗' })));
    fireEvent.click(within(ui.getByLabelText('当天根任务')).getByText(item.title));
    fireEvent.click(ui.getByRole('button', { name: '编辑任务' }));
    fireEvent.change(ui.getByLabelText('任务标题'), { target: { value: '尚未保存的编辑' } });
    state = [{ ...item, title: '悬浮窗修改', revision: 2 }]; changed();
    await waitFor(() => assert.ok(within(ui.getByLabelText('当天根任务')).getByText('悬浮窗修改')));
    assert.equal((ui.getByLabelText('任务标题') as HTMLInputElement).value, '尚未保存的编辑');
    fireEvent.click(ui.getByRole('button', { name: '保存更改' }));
    await waitFor(() => assert.ok(ui.getByText('版本已变化，请重新核对')));
    assert.equal(sentRevision, 1);
  } finally { delete window.widget; }
});


test('当天列表完成项沉底但不写用户排序，取消完成恢复原位，组内移动保留另一组原位置',async()=>{
 const done=todo('已完成老项',{completed:true}),a=todo('进行甲'),b=todo('进行乙');const calls=api([done,a,b]);const ui=render(<App/>);
 const titles=()=>Array.from(ui.container.querySelectorAll('.root-title strong')).map(n=>n.textContent);
 await waitFor(()=>assert.deepEqual(titles(),['进行甲','进行乙','已完成老项']));
 fireEvent.click(within(ui.getByLabelText('当天根任务')).getByRole('button',{name:'完成 进行甲'}));
 await waitFor(()=>assert.deepEqual(titles(),['进行乙','已完成老项','进行甲']));assert.equal(calls.length,0);
 fireEvent.click(within(ui.getByLabelText('当天根任务')).getByRole('button',{name:'重新打开 进行甲'}));
 await waitFor(()=>assert.deepEqual(titles(),['进行甲','进行乙','已完成老项']));assert.equal(calls.length,0);
 fireEvent.click(ui.getByRole('button',{name:'下移 进行甲'}));await waitFor(()=>assert.equal(calls.length,1));assert.deepEqual(calls[0].ids,[done.id,b.id,a.id]);
});
