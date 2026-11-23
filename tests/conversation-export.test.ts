import test from 'node:test';
import assert from 'node:assert/strict';
import {createElement} from 'react';
import {renderToStaticMarkup} from 'react-dom/server';
import Markdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import {conversationFilename,conversationMarkdown} from '../src/shared/conversation-export';
import type {AgentConversation,AgentRun,AgentTurn} from '../src/shared/agent';

const createdAt='2026-11-21T01:00:00Z';
const updatedAt='2026-11-21T01:02:00Z';
const source={kind:'mail' as const,id:'internal-mail-id',title:'课程通知',excerpt:'隐藏邮件摘录'};
const artifact={kind:'file' as const,id:'internal-file-id',title:'报告.pdf'};
const question:AgentTurn={id:'user',role:'user',content:'原始问题\n\n  保留缩进和尾空格  ',createdAt,runId:'run'};
const answer:AgentTurn={id:'assistant',role:'assistant',content:'**已核对的答案**',createdAt:updatedAt,runId:'run',sources:[source],artifacts:[artifact]};
const run:AgentRun={id:'run',conversationId:'chat',status:'failed',createdAt,updatedAt,partialText:'尚未完成的助手文字',events:[{id:'event',type:'tool',label:'隐藏工具名',createdAt,detail:'隐藏请求内容'}],sources:[source],artifacts:[artifact],error:'隐藏异常内容'};
const conversation:AgentConversation={id:'chat',title:'课程讨论',createdAt,updatedAt,turnCount:2,turns:[answer,question],mailIds:[source.id],fileIds:[artifact.id],goal:'隐藏任务上下文'};

test('conversation Markdown orders visible turns and preserves their text and timestamps',()=>{
 const output=conversationMarkdown(conversation);
 assert.match(output,/^# 课程讨论\n\n创建时间：2026-11-21T01:00:00Z/);
 assert.ok(output.indexOf(question.content)<output.indexOf(answer.content));
 assert.ok(output.includes('## 用户 · '+createdAt));
 assert.ok(output.includes('## 助手 · '+updatedAt));
 assert.ok(output.includes(question.content));
 assert.doesNotMatch(output,/课程通知|报告\.pdf|隐藏|internal-/);
});

test('optional metadata prints source and artifact names without IDs, excerpts, or child conversations',()=>{
 const child={id:'child',title:'不在当前对话的子任务',createdAt,updatedAt,turnCount:1};
 const output=conversationMarkdown({...conversation,children:[child]},true);
 assert.ok(output.includes('课程通知'));
 assert.ok(output.includes('报告.pdf'));
 assert.doesNotMatch(output,/不在当前对话的子任务|隐藏|internal-/);
});

test('visible incomplete run is marked while a persisted answer takes precedence over its run',()=>{
 const unfinished={...conversation,turnCount:1,turns:[question],activeRun:run,lastRun:run};
 const partial=conversationMarkdown(unfinished,true);
 assert.match(partial,/## 助手（未完成 · 执行失败）/);
 assert.equal(partial.split(run.partialText).length,2);
 assert.ok(partial.includes('课程通知'));
 assert.doesNotMatch(partial,/隐藏|internal-/);
 const completed=conversationMarkdown({...conversation,lastRun:{...run,status:'completed',partialText:answer.content}},true);
 assert.equal(completed.split(answer.content).length,2);
 assert.equal(completed.split(source.title).length,2);
 assert.doesNotMatch(completed,/未完成/);
});

test('a pending clarification exports the visible question and choices even with no partial answer',()=>{
 const waiting={...run,status:'waiting' as const,partialText:'',clarification:{id:'hidden-question-id',question:'请确认课程。',options:['课程 A','课程 B']}};
 const output=conversationMarkdown({...conversation,turnCount:1,turns:[question],activeRun:waiting});
 assert.match(output,/## 助手（未完成 · 等待补充）/);
 assert.match(output,/请确认课程。\n\n- 课程 A\n- 课程 B/);
 assert.doesNotMatch(output,/hidden-question-id|隐藏/);
});

test('empty conversation has no exportable text',()=>{
 assert.equal(conversationMarkdown({...conversation,turnCount:0,turns:[]}), '');
});

test('titles and optional names cannot add headings or active Markdown links',()=>{
 const output=conversationMarkdown({...conversation,title:'标题\n## 注入 <script>',turns:[{...answer,sources:[{...source,title:'[附图](https://example.invalid)\n## 假标题'},{...source,title:'contact@example.invalid'}]}]},true);
 assert.doesNotMatch(output,/\n## 注入|\n## 假标题|<script>/);
 const rendered=renderToStaticMarkup(createElement(Markdown,{remarkPlugins:[remarkGfm],children:output}));
 assert.doesNotMatch(rendered,/<a |<img |<script>/);
});

test('export filename is a bounded Markdown basename on macOS and Windows',()=>{
 assert.equal(conversationFilename('课程讨论'),'课程讨论.md');
 assert.equal(conversationFilename('   '),'对话.md');
 assert.match(conversationFilename('CON.txt'),/^_CON\.txt\.md$/);
 assert.match(conversationFilename('lpt²'),/^_lpt²\.md$/);
 const hostile=conversationFilename('../x\\y::\n<报告>|?*\u0000. ');
 assert.doesNotMatch(hostile,/^[.]|[\\/:<>|?*\u0000-\u001f]/);
 assert.match(hostile,/\.md$/);
 assert.ok(Buffer.byteLength(conversationFilename('📝'.repeat(120)),'utf8')<255);
});
