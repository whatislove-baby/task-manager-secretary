import React from 'react';
import { test, afterEach } from 'node:test';
import assert from 'node:assert/strict';
import { JSDOM } from 'jsdom';
import { createRequire } from 'node:module';
import { Tools } from '../src/renderer/Tools';
import type { DesktopAPI } from '../src/shared/contracts';
import type { Todo, TodoDraft } from '../src/shared/todo';

const dom = new JSDOM('<!doctype html><html><body></body></html>', { url: 'http://localhost' });
for (const [key, value] of Object.entries({ window: dom.window, document: dom.window.document, navigator: dom.window.navigator, HTMLElement: dom.window.HTMLElement })) Object.defineProperty(globalThis, key, { value, configurable: true });
const { render, fireEvent, waitFor, cleanup, within } = createRequire(process.cwd() + '/package.json')('@testing-library/react') as typeof import('@testing-library/react');
afterEach(() => cleanup());
const ok = <T,>(data: T) => ({ ok: true as const, data });
const settings = { baseUrl: 'https://example.org/v1', model: 'user-model', hasApiKey: true, dataDirectory: 'local-data' };
function setup(overrides: Partial<DesktopAPI> = {}) {
  window.desktop = { getSettings: async () => ok(settings), getCompletionConfig: async () => ok({ cascadeComplete: true, autoCompleteParent: true }), ...overrides } as DesktopAPI;
}
const onChanged = async () => {};
const onClose = () => {};
const drafts: TodoDraft[] = [
  { tempId: 'p', parentTempId: null, title: '父项', notes: '', due: { mode: 'none' }, source: '原文第一行' },
  { tempId: 'c', parentTempId: 'p', title: '子项', notes: '', due: { mode: 'inherit' }, source: '原文第二行' },
];

test('设置写入失败保留输入，关闭需要明确放弃，焦点限制在对话框内', async () => {
  setup({ saveSettings: async () => ({ ok: false, error: '配置写入失败' }) });
  let closed = 0;
  const ui = render(<Tools section="settings" todos={[]} onClose={() => { closed++; }} onChanged={onChanged} />);
  await waitFor(() => assert.ok(ui.getByLabelText('模型名称')));
  fireEvent.change(ui.getByLabelText('模型名称'), { target: { value: 'my-edit' } });
  fireEvent.click(ui.getByRole('button', { name: '保存服务配置' }));
  await waitFor(() => assert.ok(ui.getByRole('alert')));
  assert.equal((ui.getByLabelText('模型名称') as HTMLInputElement).value, 'my-edit');
  fireEvent.keyDown(document, { key: 'Escape' });
  const confirm = ui.getByRole('alertdialog');
  assert.equal(document.activeElement, within(confirm).getByRole('button', { name: '继续编辑' }));
  fireEvent.keyDown(document, { key: 'Tab', shiftKey: true });
  assert.equal(document.activeElement, within(confirm).getByRole('button', { name: '放弃并关闭' }));
  assert.equal(closed, 0);
  fireEvent.click(within(confirm).getByRole('button', { name: '继续编辑' }));
  assert.equal(ui.queryByRole('alertdialog'), null);
  fireEvent.click(ui.getByRole('button', { name: '关闭工具面板' }));
  fireEvent.click(ui.getByRole('button', { name: '放弃并关闭' }));
  assert.equal(closed, 1);
});

test('AI 需主动生成，勾选层级保持合法，保存保留依据并仅提交一次', async () => {
  let extracted = 0, saves = 0, changed = 0;
  let stored: TodoDraft[] = [];
  setup({ extract: async text => { extracted++; assert.equal(text, '完整原文'); return ok({ drafts: structuredClone(drafts), notes: '核对截止时间', confirmationToken: 'token-one' }); }, saveDrafts: async (items, token) => { saves++; assert.equal(token, 'token-one'); stored = items; return ok({ value: items.length, warnings: [] }); } });
  const ui = render(<Tools section="ai" todos={[]} onClose={onClose} onChanged={async () => { changed++; }} />);
  await waitFor(() => assert.ok(ui.getByLabelText('邮件或其他原文')));
  assert.equal(extracted, 0);
  assert.ok(ui.getByText('服务：https://example.org/v1/chat/completions'));
  fireEvent.change(ui.getByLabelText('邮件或其他原文'), { target: { value: '完整原文' } });
  fireEvent.click(ui.getByRole('button', { name: '生成待办草稿' }));
  await waitFor(() => assert.ok(ui.getByLabelText('保存草稿 1')));
  fireEvent.click(ui.getByLabelText('保存草稿 1'));
  assert.equal((ui.getByLabelText('保存草稿 2') as HTMLInputElement).checked, false);
  fireEvent.click(ui.getByLabelText('保存草稿 2'));
  assert.equal((ui.getByLabelText('保存草稿 1') as HTMLInputElement).checked, true);
  fireEvent.change(ui.getAllByLabelText('标题')[1], { target: { value: '修订子项' } });
  const save = ui.getByRole('button', { name: '确认保存 2 项待办' });
  fireEvent.click(save); fireEvent.click(save);
  await waitFor(() => assert.equal(changed, 1));
  assert.equal(saves, 1); assert.equal(stored[1].source, '原文第二行'); assert.equal(stored[1].title, '修订子项');
  assert.equal((save as HTMLButtonElement).disabled, true);
});

test('AI 错误不清除原文，空结果不保存', async () => {
  let fail = true, saves = 0;
  setup({ extract: async () => fail ? { ok: false, error: '网络未连接' } : ok({ drafts: [], notes: '', confirmationToken: 'empty' }), saveDrafts: async () => { saves++; return ok({ value: 0, warnings: [] }); } });
  const ui = render(<Tools section="ai" todos={[]} onClose={onClose} onChanged={onChanged} />);
  await waitFor(() => assert.ok(ui.getByLabelText('邮件或其他原文')));
  fireEvent.change(ui.getByLabelText('邮件或其他原文'), { target: { value: '保留原文' } });
  fireEvent.click(ui.getByRole('button', { name: '生成待办草稿' }));
  await waitFor(() => assert.ok(ui.getByText('网络未连接')));
  assert.equal((ui.getByLabelText('邮件或其他原文') as HTMLTextAreaElement).value, '保留原文');
  fail = false;
  fireEvent.click(ui.getByRole('button', { name: '生成待办草稿' }));
  await waitFor(() => assert.ok(ui.getByText('这段原文中没有识别到待办。可以编辑原文后再次生成。')));
  assert.equal(ui.queryByRole('button', { name: /确认保存/ }), null); assert.equal(saves, 0);
});

test('回收站按批展示并整批恢复，成功刷新列表', async () => {
  const base: Todo = { id: 'p', parentId: null, title: '删除的父项', notes: '', due: { mode: 'none' }, completed: false, createdAt: '2026-10-01', updatedAt: '2026-10-02', revision: 1, deletedAt: '2026-10-29T00:00:00Z', trashBatchId: 'batch-one' };
  let changed = 0, restores = 0;
  setup({ restoreBatch: async batch => { restores++; assert.equal(batch, 'batch-one'); return ok({ value: [base], warnings: [] }); } });
  const ui = render(<Tools section="trash" todos={[base, { ...base, id: 'c', title: '删除的子项', parentId: 'p' }]} onClose={onClose} onChanged={async () => { changed++; }} />);
  await waitFor(() => assert.ok(ui.getByRole('button', { name: '恢复整批' })));
  assert.equal(ui.getAllByRole('button', { name: '恢复整批' }).length, 1);
  fireEvent.click(ui.getByRole('button', { name: '恢复整批' }));
  await waitFor(() => assert.equal(changed, 1)); assert.equal(restores, 1);
  assert.equal(ui.queryByRole('button', { name: /永久|清空/ }), null);
});

test('设置分别保存服务与完成联动，空密钥保留已有凭据且不联网', async () => {
  let serviceSaves = 0, configSaves = 0, clearCalls = 0;
  setup({
    saveSettings: async input => { serviceSaves++; assert.equal(input.apiKey, ''); assert.equal(input.model, 'user-model'); return ok(settings); },
    setCompletionConfig: async config => { configSaves++; assert.equal(config.cascadeComplete, false); assert.equal(config.autoCompleteParent, true); return ok(config); },
    clearKey: async () => { clearCalls++; return ok(undefined); },
  });
  const ui = render(<Tools section="settings" todos={[]} onClose={onClose} onChanged={onChanged} />);
  await waitFor(() => assert.ok(ui.getByLabelText('API 密钥')));
  fireEvent.click(ui.getByRole('button', { name: '保存服务配置' }));
  await waitFor(() => assert.equal((ui.getByRole('button', { name: '保存服务配置' }) as HTMLButtonElement).disabled, false));
  assert.equal(serviceSaves, 1);
  assert.equal(ui.queryByText("AI 服务配置已保存。"), null);
  assert.equal(ui.container.querySelector(".notification"), null);
  fireEvent.click(ui.getByLabelText('完成父待办时，同时完成所有子待办'));
  fireEvent.click(ui.getByRole('button', { name: '保存完成联动' }));
  await waitFor(() => assert.equal((ui.getByRole('button', { name: '保存完成联动' }) as HTMLButtonElement).disabled, false));
  assert.equal(configSaves, 1);
  fireEvent.click(ui.getByRole('button', { name: '清除已保存密钥' }));
  await waitFor(() => assert.equal((ui.getByRole('button', { name: '清除已保存密钥' }) as HTMLButtonElement).disabled, true));
  assert.equal(clearCalls, 1);
});

test('卸载工具后焦点回到打开面板的控件', async () => {
  setup();
  const opener = document.createElement('button'); document.body.append(opener); opener.focus();
  const ui = render(<Tools section="trash" todos={[]} onClose={onClose} onChanged={onChanged} />);
  await waitFor(() => assert.ok(ui.getByText('回收站是空的')));
  assert.ok(ui.getByRole('dialog').contains(document.activeElement));
  ui.unmount(); assert.equal(document.activeElement, opener); opener.remove();
});
import type { MailAnalysis, MailWorkspaceAPI } from '../src/shared/mail-workspace';
const mailAnalysis:MailAnalysis={id:'analysis-one',messageIds:['mail-a','mail-b'],instructions:'找出下周要做的事',notes:'汇总说明',drafts:structuredClone(drafts),createdAt:'2026-10-30T08:00:00Z',saved:false,model:'mail-model',baseUrl:'https://example.org/v1'};

test('邮件批量分析直接审核，保留用户要求，确认走邮件分析保存接口且仅一次',async()=>{
 let calls=0;setup({saveDrafts:async()=>{throw new Error('不应走旧接口');}});
 window.mailWorkspace={saveAnalysis:async(id,chosen)=>{calls++;assert.equal(id,'analysis-one');assert.equal(chosen[0].title,'修改后的标题');return ok({count:chosen.length});}} as MailWorkspaceAPI;
 const ui=render(<Tools section="ai" todos={[]} initialAnalysis={mailAnalysis} onClose={onClose} onChanged={onChanged}/>);
 await waitFor(()=>assert.ok(ui.getByText('邮件分析记录 · 2 封')));
 assert.ok(ui.queryByLabelText('邮件或其他原文')===null);assert.ok(ui.getByText('你的要求：找出下周要做的事'));assert.ok(ui.getByText('汇总说明'));
 fireEvent.change(ui.getAllByLabelText('标题')[0],{target:{value:'修改后的标题'}});
 const save=ui.getByRole('button',{name:'确认保存 2 项待办'});fireEvent.click(save);fireEvent.click(save);
 await waitFor(()=>assert.equal(calls,1));await waitFor(()=>assert.ok((save as HTMLButtonElement).disabled));
 assert.ok(ui.getByText('本批待办已全部保存，可关闭面板；此记录仅供查看。'));
});

test('无待办的邮件分析保留结果且可直接关闭，重开已保存批次只读',async()=>{
 setup();let closed=0;
 const ui=render(<Tools section="ai" todos={[]} initialAnalysis={{...mailAnalysis,drafts:[]}} onClose={()=>{closed++;}} onChanged={onChanged}/>);
 await waitFor(()=>assert.ok(ui.getByText('分析已保存在本机，本次没有生成待办。')));
 fireEvent.click(ui.getByRole('button',{name:'关闭工具面板'}));assert.equal(closed,1);assert.ok(ui.queryByRole('alertdialog')===null);ui.unmount();
 const saved=render(<Tools section="ai" todos={[]} initialAnalysis={{...mailAnalysis,saved:true}} onClose={()=>{closed++;}} onChanged={onChanged}/>);
 await waitFor(()=>assert.ok(saved.getAllByLabelText('标题').length===2));assert.ok((saved.getAllByLabelText('标题')[0] as HTMLInputElement).closest('fieldset')?.disabled);
 fireEvent.click(saved.getByRole('button',{name:'关闭工具面板'}));assert.equal(closed,2);
});
