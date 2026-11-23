import React from 'react';
import { test, afterEach } from 'node:test';
import assert from 'node:assert/strict';
import { JSDOM } from 'jsdom';
import { createRequire } from 'node:module';
import { Tools } from '../src/renderer/Tools';
import type { DesktopAPI } from '../src/shared/contracts';
import type { MailAnalysis, MailWorkspaceAPI } from '../src/shared/mail-workspace';
import type { Todo, TodoDraft } from '../src/shared/todo';
const dom=new JSDOM('<!doctype html><html><body></body></html>',{url:'http://localhost'});
for(const [key,value] of Object.entries({window:dom.window,document:dom.window.document,navigator:dom.window.navigator,HTMLElement:dom.window.HTMLElement}))Object.defineProperty(globalThis,key,{value,configurable:true});
const {render,fireEvent,waitFor,cleanup,within}=createRequire(process.cwd()+'/package.json')('@testing-library/react') as typeof import('@testing-library/react');
afterEach(()=>{cleanup();setupTodos();});
const ok=<T,>(data:T)=>({ok:true as const,data});
function setupTodos(listTodos: DesktopAPI['listTodos'] = async () => ok([])) {
 window.desktop={getSettings:async()=>ok({baseUrl:'https://example.org/v1',model:'m',hasApiKey:true,dataDirectory:'d'}),listTodos} as DesktopAPI;
}
setupTodos();
const drafts:TodoDraft[]=[
 {tempId:'p',parentTempId:null,title:'父项',notes:'',due:{mode:'none'},source:'来源 A'},
 {tempId:'c',parentTempId:'p',title:'子项',notes:'',due:{mode:'inherit'},source:'来源 B'},
 {tempId:'x',parentTempId:null,title:'独立项',notes:'',due:{mode:'none'},source:'来源 C'},
];
function analysis(id='analysis-review',items=drafts):MailAnalysis{return {id,messageIds:['mail-a','mail-b'],instructions:'整理',notes:'说明',drafts:structuredClone(items),createdAt:'2026-10-30T00:00:00Z',saved:false,model:'m',baseUrl:'https://example.org/v1'};}

test('审核窗口：点击遮罩、Esc、关闭按钮都只收起，重新打开保留编辑',async()=>{
 let closed=0;const opener=document.createElement('button');opener.textContent='opener';document.body.append(opener);opener.focus();
 const ui=render(<Tools section="ai" todos={[]} initialAnalysis={analysis('analysis-close')} onClose={()=>{closed++;}} onChanged={async()=>{}}/>);
 await waitFor(()=>assert.ok(ui.getAllByLabelText('标题')));
 fireEvent.change(ui.getAllByLabelText('标题')[0],{target:{value:'父项已修改'}});
 const backdrop=ui.container.querySelector('.tools-backdrop')!;
 fireEvent.click(backdrop);assert.equal(closed,1);assert.equal(ui.queryByRole('alertdialog'),null);
 ui.unmount();assert.equal(document.activeElement,opener);
 const reopened=render(<Tools section="ai" todos={[]} initialAnalysis={analysis('analysis-close')} onClose={()=>{closed++;}} onChanged={async()=>{}}/>);
 await waitFor(()=>assert.ok(reopened.getAllByLabelText('标题')));
 assert.equal((reopened.getAllByLabelText('标题')[0] as HTMLInputElement).value,'父项已修改');
 fireEvent.keyDown(document,{key:'Escape'});assert.equal(closed,2);
 reopened.unmount();
 const third=render(<Tools section="ai" todos={[]} initialAnalysis={analysis('analysis-close')} onClose={()=>{closed++;}} onChanged={async()=>{}}/>);
 await waitFor(()=>assert.ok(third.getAllByLabelText('标题')));fireEvent.click(third.getByRole('button',{name:'关闭工具面板'}));assert.equal(closed,3);
 opener.remove();
});

test('点击弹窗内部和日期选择器不会误关闭',async()=>{
 let closed=0;const ui=render(<Tools section="ai" todos={[]} initialAnalysis={analysis('analysis-inside')} onClose={()=>{closed++;}} onChanged={async()=>{}}/>);
 await waitFor(()=>assert.ok(ui.getAllByLabelText('标题')));
 fireEvent.click(ui.getByRole('dialog'));fireEvent.click(ui.getAllByLabelText('截止方式')[0]);fireEvent.change(ui.getAllByLabelText('截止方式')[0],{target:{value:'date'}});
 assert.equal(closed,0);
});

test('一键全部添加覆盖未勾选草稿并保留层级与来源，已添加项不重复',async()=>{
 const calls:MailAnalysis['drafts'][]=[];let closed=0;
 window.mailWorkspace={saveAnalysis:async(_id:string,chosen:TodoDraft[])=>{calls.push(structuredClone(chosen));return ok({count:chosen.length});}} as unknown as MailWorkspaceAPI;
 const ui=render(<Tools section="ai" todos={[]} initialAnalysis={analysis('analysis-all')} onClose={()=>{closed++;}} onChanged={async()=>{}}/>);
 await waitFor(()=>assert.ok(ui.getByRole('button',{name:'全部添加（3 项）'})));
 fireEvent.change(ui.getAllByLabelText('标题')[2],{target:{value:'独立项已修改'}});
 fireEvent.click(ui.getByLabelText('保存草稿 3')); // 取消勾选第三项
 fireEvent.click(ui.getByRole('button',{name:'全部添加（3 项）'}));
 await waitFor(()=>assert.equal(calls.length,1));
 assert.equal(calls[0]!.length,3);assert.equal(calls[0]![2]!.title,'独立项已修改');assert.equal(calls[0]![1]!.parentTempId,'p');assert.equal(calls[0]![1]!.source,'来源 B');assert.equal(calls[0]![2]!.source,'来源 C');
 assert.ok(ui.getAllByText('已添加').length===3);assert.ok((ui.getByRole('button',{name:'全部添加（3 项）'}) as HTMLButtonElement).disabled);
 assert.equal(closed,0);
});

test('批量保存前统一校验并定位具体草稿，保留编辑内容',async()=>{
 let called=0;window.mailWorkspace={saveAnalysis:async()=>{called++;return ok({count:1});}} as unknown as MailWorkspaceAPI;
 const ui=render(<Tools section="ai" todos={[]} initialAnalysis={analysis('analysis-invalid')} onClose={()=>{}} onChanged={async()=>{}}/>);
 await waitFor(()=>assert.ok(ui.getByRole('button',{name:'全部添加（3 项）'})));
 fireEvent.change(ui.getAllByLabelText('标题')[1],{target:{value:'   '}});
 fireEvent.click(ui.getByRole('button',{name:'全部添加（3 项）'}));
 await waitFor(()=>assert.ok(ui.getByRole('alert')));
 assert.equal(called,0);assert.ok(ui.getByRole('alert').textContent?.includes('草稿 2'));
 assert.equal((ui.getAllByLabelText('标题')[1] as HTMLInputElement).value,'   ');
 assert.ok(ui.container.querySelector('.tools-draft-invalid'));
});

test('分批添加后仍可继续添加剩余草稿，父子层级保持',async()=>{
 const calls:MailAnalysis['drafts'][]=[];window.mailWorkspace={saveAnalysis:async(_id:string,chosen:TodoDraft[])=>{calls.push(structuredClone(chosen));return ok({count:1});}} as unknown as MailWorkspaceAPI;
 const partial={...analysis('analysis-partial'),addedTempIds:['p'],addedTodos:{p:'old-parent'}};
 setupTodos(async () => ok([existingTodo({ title: '父项', notes: `本地邮件：${messageId}` })]));
 partial.drafts[0].sourceMailIds = [messageId];
 const ui=render(<Tools section="ai" todos={[]} initialAnalysis={partial} onClose={()=>{}} onChanged={async()=>{}}/>);
 await waitFor(()=>assert.ok(ui.getAllByText('已添加').length===1));
 fireEvent.click(ui.getByRole('button',{name:'确认保存 3 项待办'}));
 await waitFor(()=>assert.equal(calls.length,1));
 assert.ok(calls[0]!.some(d=>d.tempId==='c'));assert.ok(calls[0]!.some(d=>d.tempId==='x'));
 assert.ok(calls[0]!.some(d=>d.tempId==='p'));
 assert.ok(ui.getAllByText('已添加').length===3);
});

const messageId = 'ab'.repeat(32);
const otherMessageId = 'cd'.repeat(32);
const dated = (date: string) => ({ mode: 'value', value: { kind: 'date', date } } as const);
function existingTodo(overrides: Partial<Todo>): Todo {
 return {
  id: 'old-parent', parentId: null, title: '提交报告', notes: '', completed: false,
  due: dated('2026-11-24'), createdAt: '2026-11-20T12:00:00Z', updatedAt: '2026-11-20T12:00:00Z',
  revision: 1, deletedAt: null, trashBatchId: null, ...overrides,
 };
}
function reviewItems(): TodoDraft[] {
 return [
  { ...drafts[0], title: '提交报告', sourceMailIds: [messageId], due: dated('2026-11-30') },
  { ...drafts[1], title: '核对附件', sourceMailIds: [messageId] },
  { ...drafts[2], title: '预约面试', sourceMailIds: [messageId], due: dated('2026-11-30') },
 ];
}

test('未指定截止方式的子草稿继承父日期并提示重复，显式无截止时间后可保存', async () => {
 const { due: _due, ...child } = drafts[1];
 const items = [{ ...drafts[0], due: dated('2026-11-24') }, child];
 const calls: TodoDraft[][] = [];
 setupTodos(async () => ok([existingTodo({ title: child.title })]));
 window.mailWorkspace = { saveAnalysis: async (_id, chosen) => { calls.push(chosen); return ok({ count: chosen.length }); } } as MailWorkspaceAPI;
 const ui = render(<Tools section="ai" todos={[]} initialAnalysis={analysis('analysis-default-inherit', items)} onClose={()=>{}} onChanged={async()=>{}}/>);
 const save = await ui.findByRole('button', { name: '确认保存 2 项待办' });
 fireEvent.click(save);
 await ui.findByRole('button', { name: '仍然添加 2 项待办' });
 assert.equal(calls.length, 0);
 const childDue = ui.getAllByLabelText('截止方式')[1] as HTMLSelectElement;
 assert.equal(childDue.value, 'inherit');
 fireEvent.change(childDue, { target: { value: 'none' } });
 fireEvent.click(save);
 await waitFor(() => assert.equal(calls.length, 1));
 assert.deepEqual(calls[0][1].due, { mode: 'none' });
});

test('同一审核窗口分批保存后，子草稿按数据库中父待办的新截止日期检查', async () => {
 const parent = existingTodo({ id: 'persisted-parent', title: drafts[0].title, due: dated('2026-11-24') });
 const record = analysis('analysis-parent-updated', [{ ...drafts[0], due: parent.due }, drafts[1]]);
 const matchingChild = existingTodo({ id: 'existing-child', title: drafts[1].title, due: dated('2026-11-30') });
 let localTodos = [matchingChild];
 let failProgressRead = true;
 const calls: TodoDraft[][] = [];
 setupTodos(async () => ok(localTodos));
 window.mailWorkspace = {
  saveAnalysis: async (_id, chosen) => {
   calls.push(chosen);
   if (calls.length === 1) localTodos = [...localTodos, parent];
   return ok({ count: 1 });
  },
  getAnalysis: async id => {
   assert.equal(id, record.id);
   if (failProgressRead) return { ok: false, error: '分析记录暂时无法读取' };
   return ok({ ...record, addedTempIds: ['p'], addedTodos: { p: parent.id } });
  },
 } as MailWorkspaceAPI;
 const ui = render(<Tools section="ai" todos={[]} initialAnalysis={record} onClose={()=>{}} onChanged={async()=>{}}/>);
 await ui.findByRole('button', { name: '确认保存 2 项待办' });
 fireEvent.click(ui.getByLabelText('保存草稿 2'));
 fireEvent.click(ui.getByRole('button', { name: '确认保存 1 项待办' }));
 await ui.findByText('已添加');
 assert.deepEqual(calls[0].map(({ tempId }) => tempId), ['p']);
 localTodos = [matchingChild, { ...parent, due: dated('2026-11-30'), revision: 2 }];
 fireEvent.click(ui.getByLabelText('保存草稿 2'));
 const childNotes = ui.getAllByLabelText('备注')[1] as HTMLTextAreaElement;
 fireEvent.change(childNotes, { target: { value: '保留编辑' } });
 const saveChildren = ui.getByRole('button', { name: '确认保存 2 项待办' });
 fireEvent.click(saveChildren);
 assert.match((await ui.findByRole('alert')).textContent ?? '', /分析记录暂时无法读取/);
 assert.equal(calls.length, 1);
 assert.equal(childNotes.value, '保留编辑');
 failProgressRead = false;
 fireEvent.click(saveChildren);
 const confirm = await ui.findByRole('button', { name: '仍然添加 1 项待办' });
 assert.equal(calls.length, 1);
 const matches = within(ui.getByRole('region', { name: /可能重复的已有待办/ }));
 assert.ok(matches.getByText('截止日期相同', { exact: false }));
 fireEvent.click(confirm);
 await waitFor(() => assert.equal(calls.length, 2));
 assert.deepEqual(calls[1].map(({ tempId, parentTempId }) => [tempId, parentTempId]), [['p', null], ['c', 'p']]);
 assert.equal(calls[1][1].notes, '保留编辑');
});

test('全部添加重新读取本地待办，显示改期与已完成状态，取消父草稿同时排除子草稿', async () => {
 const calls: TodoDraft[][] = [];
 const mailNote = `邮件分析来源：\n本地邮件：${messageId}\n收件时间：2026-11-18`;
 setupTodos(async () => ok([
  existingTodo({ title: '提交报告！', notes: mailNote, completed: true }),
  existingTodo({ id: 'unrelated', title: '回复邀请', notes: mailNote, due: dated('2026-11-30') }),
  existingTodo({ id: 'trash', title: '提交报告。', notes: mailNote, deletedAt: '2026-11-21T12:00:00Z', trashBatchId: 'trash-batch' }),
  existingTodo({ id: 'other-mail', title: '预约面试！', notes: `本地邮件：${otherMessageId}`, due: dated('2026-11-30') }),
 ]));
 window.mailWorkspace = { saveAnalysis: async (_id, items) => { calls.push(items); return ok({ count: items.length }); } } as MailWorkspaceAPI;
 const ui = render(<Tools section="ai" todos={[]} initialAnalysis={analysis('analysis-duplicates', reviewItems())} onClose={()=>{}} onChanged={async()=>{}}/>);
 const saveAll = await ui.findByRole('button', { name: '全部添加（3 项）' });
 fireEvent.click(ui.getByLabelText('保存草稿 1'));
 fireEvent.click(saveAll);
 await ui.findByRole('button', { name: '仍然添加 3 项待办' });
 assert.equal(calls.length, 0);
 const matches = within(ui.getByRole('region', { name: /可能重复的已有待办/ }));
 assert.ok(matches.getByText('提交报告！'));
 assert.ok(matches.getByText('2026-11-24', { exact: false }));
 assert.ok(matches.getByText('已完成'));
 assert.ok(matches.getByText('来源邮件相同', { exact: false }));
 assert.ok(matches.getByText('截止时间不同', { exact: false }));
 assert.equal(matches.queryByText('提交报告。'), null);
 assert.equal(matches.queryByText('回复邀请'), null);
 assert.equal(matches.queryByText('预约面试！'), null);
 assert.equal((ui.getByLabelText('保存草稿 2') as HTMLInputElement).checked, false);
 fireEvent.click(ui.getByRole('button', { name: '确认保存 1 项待办' }));
 await waitFor(() => assert.equal(calls.length, 1));
 assert.deepEqual(calls[0].map(item => item.tempId), ['x']);
});

test('明确继续会再次检查，新增候选必须重新确认，且按原批次保存', async () => {
 const first = existingTodo({ notes: `本地邮件：${messageId}` });
 let localTodos = [first], reads = 0;
 const calls: TodoDraft[][] = [];
 setupTodos(async () => { reads++; return ok(localTodos); });
 window.mailWorkspace = { saveAnalysis: async (_id, items) => { calls.push(items); return ok({ count: items.length }); } } as MailWorkspaceAPI;
 const ui = render(<Tools section="ai" todos={[]} initialAnalysis={analysis('analysis-recheck', reviewItems())} onClose={()=>{}} onChanged={async()=>{}}/>);
 fireEvent.click(await ui.findByRole('button', { name: '确认保存 3 项待办' }));
 const confirm = () => ui.findByRole('button', { name: '仍然添加 3 项待办' });
 const firstConfirm = await confirm();
 assert.equal(calls.length, 0);
 localTodos = [...localTodos, existingTodo({ id: 'another-task', title: '提交报告。', notes: `本地邮件：${messageId}` })];
 fireEvent.click(firstConfirm);
 await ui.findByText('提交报告。');
 assert.equal(calls.length, 0);
 fireEvent.click(await confirm());
 await waitFor(() => assert.equal(calls.length, 1));
 assert.equal(reads, 3);
 assert.deepEqual(calls[0].map(({ tempId, parentTempId }) => [tempId, parentTempId]), [['p', null], ['c', 'p'], ['x', null]]);
});
