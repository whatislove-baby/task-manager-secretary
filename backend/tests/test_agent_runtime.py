"""Production Agent integration with real framework/checkpoints and synthetic I/O.

Only the model boundary is scripted. Business tools, SQLite stores, the Agent
worker coroutine, stream handling and public RPC methods execute for real.
"""
from network_guard import no_external_network
import asyncio
import json
import os
from pathlib import Path
import tempfile
import unittest
from typing import Any
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, AIMessageChunk, HumanMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatGenerationChunk, ChatResult
from pydantic import Field

from task_backend.service import BackendService


CONFIG = dict(baseUrl='https://offline.invalid/api/v1', model='synthetic-agent', apiKey='not-a-real-key')


class ScriptedModel(BaseChatModel):
    script: list[Any]
    seen: list[Any] = Field(default_factory=list)
    bound: list[str] = Field(default_factory=list)
    hook: Any = None
    stream_prefix: str = Field(default_factory=lambda: str(uuid4()))

    @property
    def _llm_type(self):
        return 'production-agent-offline-test'

    def bind_tools(self, tools, **kwargs):
        self.bound = [t.name for t in tools]
        return self

    def _next(self, messages):
        self.seen.append(list(messages))
        if not self.script:
            raise AssertionError('Unexpected model call')
        value = self.script.pop(0)
        if isinstance(value, Exception):
            raise value
        return value

    def _generate(self, messages, **kwargs):
        return ChatResult(generations=[ChatGeneration(message=self._next(messages))])

    async def _astream(self, messages, **kwargs):
        response = self._next(messages)
        message_id = response.id or self.stream_prefix + ':' + str(len(self.seen))
        if response.tool_calls:
            chunk = AIMessageChunk(content=response.content, id=message_id,
                additional_kwargs=response.additional_kwargs,
                tool_call_chunks=[dict(index=i, name=c['name'], args=json.dumps(c['args']), id=c['id'])
                                  for i, c in enumerate(response.tool_calls)])
            yield ChatGenerationChunk(message=chunk)
            await asyncio.sleep(0)
            if self.hook:
                self.hook()
        else:
            for index in range(0, len(response.content), 128):
                yield ChatGenerationChunk(message=AIMessageChunk(content=response.content[index:index+128], id=message_id))
                await asyncio.sleep(0)
                if self.hook:
                    self.hook()
        yield ChatGenerationChunk(message=AIMessageChunk(content='', id=message_id,
            response_metadata=response.response_metadata or {'finish_reason': 'stop'}))


def tool_call(name, args, id='call-1'):
    return AIMessage(content='', tool_calls=[dict(name=name, args=args, id=id, type='tool_call')])


class AgentRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.enterContext(patch.dict(os.environ, {'LANGSMITH_TRACING': 'false',
                                                 'LANGCHAIN_TRACING_V2': 'false', 'LANGCHAIN_TRACING': 'false'}))
        self.enterContext(no_external_network())
        self.tmp = tempfile.TemporaryDirectory()
        self.directory = Path(self.tmp.name)
        self.service = self.open_service()

    def open_service(self):
        service = BackendService(self.directory, worker=True)
        service.settings.for_request = lambda: dict(CONFIG)
        service.agent.schedule = lambda: None
        return service

    def reopen(self):
        self.service.close()
        self.service = self.open_service()

    def tearDown(self):
        self.service.close()
        self.tmp.cleanup()

    def model(self, *responses, hook=None):
        model = ScriptedModel(script=list(responses), hook=hook)
        self.service.agent.model_factory = lambda config, reasoning: model
        return model

    def start(self, content='你好', **kwargs):
        return self.service.call('agent:startTurn', dict(text=content, **kwargs))

    def execute_run(self, run):
        self.assertTrue(asyncio.run(self.service.agent.run_next()))
        return self.service.call('agent:getRun', run['id'])

    def test_natural_long_answer_streams_and_persists_without_draft_json(self):
        answer = '这是一段允许完整展开的解释，包含分析与依据。\n' * 150
        run = self.start('请详细分析，不需要待办。')
        partials = []
        self.model(AIMessage(content=answer), hook=lambda: partials.append(
            self.service.call('agent:getRun', run['id'])['partialText']))
        result = self.execute_run(run)
        self.assertEqual(result['status'], 'completed', result.get('error'))
        self.assertEqual(result['partialText'], answer)
        self.assertTrue(any(0 < len(partial) < len(answer) for partial in partials))
        conversation = self.service.call('agent:getConversation', run['conversationId'])
        self.assertEqual([t['role'] for t in conversation['turns']], ['user', 'assistant'])
        self.assertEqual(conversation['turns'][-1]['content'], answer)
        self.assertGreater(len(answer), 2000)
        self.assertFalse(any(key.startswith('_') for key in result))
        self.assertEqual(self.service.call('agent:listPending'), [])
        self.assertFalse(asyncio.run(self.service.agent.run_next()))

    def test_history_survives_new_service_and_stays_within_its_conversation(self):
        self.model(AIMessage(content='记下了：这次讨论 Project 2。'))
        first = self.start('这次讨论 Project 2。')
        self.assertEqual(self.execute_run(first)['status'], 'completed')
        self.reopen()
        model = self.model(AIMessage(content='我们在讨论 Project 2。'))
        followup = self.start('刚才在讨论什么？', conversationId=first['conversationId'])
        self.assertEqual(self.execute_run(followup)['status'], 'completed')
        history = model.seen[0]
        self.assertTrue(any(isinstance(m, HumanMessage) and m.content == '这次讨论 Project 2。' for m in history))
        self.assertTrue(any(isinstance(m, AIMessage) and '记下了' in m.content for m in history))
        other_model = self.model(AIMessage(content='这是独立的新对话。'))
        other = self.start('另一门课程。')
        self.assertEqual(self.execute_run(other)['status'], 'completed')
        self.assertFalse(any(isinstance(m, HumanMessage) and 'Project 2' in m.content for m in other_model.seen[0]))

    def test_clarification_reopens_then_resumes_original_task_once(self):
        self.model(tool_call('ask_user', dict(question='是哪个 project？', options=['Project 2', 'Final'])))
        run = self.start('分析项目要求并帮我找缺项。')
        waiting = self.execute_run(run)
        self.assertEqual(waiting['status'], 'waiting', waiting.get('error'))
        self.assertEqual(waiting['clarification']['options'], ['Project 2', 'Final'])
        with self.assertRaises(ValueError):
            self.service.call('agent:resumeRun', dict(id=run['id']))
        with self.assertRaises(ValueError):
            self.start('另外一个问题', conversationId=run['conversationId'])
        self.reopen()
        model = self.model(AIMessage(content='继续分析 Project 2 的缺项。'))
        resume = dict(id=run['id'], answer='Project 2', questionId=waiting['clarification']['id'])
        resumed = self.service.call('agent:resumeRun', resume)
        self.service.call('agent:resumeRun', resume)
        self.assertEqual(self.execute_run(resumed)['status'], 'completed')
        self.assertTrue(any(isinstance(m, ToolMessage) and m.content == 'Project 2' for m in model.seen[0]))
        self.assertTrue(any(isinstance(m, HumanMessage) and '找缺项' in m.content for m in model.seen[0]))
        conversation = self.service.call('agent:getConversation', run['conversationId'])
        self.assertEqual(sum(t['content'] == 'Project 2' for t in conversation['turns']), 1)

    def test_real_mail_tools_execute_and_sources_are_exposed(self):
        mail = self.service.mail.store.put('fixture-binding', 'fixture-identity', 'student@example.invalid', dict(
            id='fixture-mail', subject='Course notification', sender='teacher@example.invalid',
            receivedAt='2026-11-21T00:00:00Z', preview='', unread=True, webUrl='', imported=False,
            body='Project 2 is due on 23 November 2026.'))
        mail_id = mail['localId']
        model = self.model(
            tool_call('search_mail', dict(query='Project 2'), 'search'),
            tool_call('read_mail', dict(message_id=mail_id), 'read'),
            AIMessage(content=f'Project 2 于 11 月 23 日提交。[课程通知](mail://{mail_id})'))
        run = self.start('找一下 Project 2 的最新通知。')
        result = self.execute_run(run)
        self.assertEqual(result['status'], 'completed', result.get('error'))
        self.assertTrue(any(s['id'] == mail_id and s['kind'] == 'mail' for s in result['sources']))
        self.assertTrue(any(e.get('tool') == 'search_mail' for e in result['events']))
        self.assertTrue(any(e.get('tool') == 'read_mail' for e in result['events']))
        self.assertTrue(any(isinstance(m, ToolMessage) and '23 November' in m.content for m in model.seen[-1]))
        self.assertNotIn('send_mail', model.bound)
        self.assertNotIn('shell', model.bound)

    def citation_mail(self, remote_id):
        return self.service.mail.store.put('citation-binding', 'citation-identity', 'reader@example.invalid', dict(
            id=remote_id, subject='Course notice ' + remote_id, sender=remote_id + '@example.invalid',
            receivedAt='2026-11-21T00:00:00Z', preview='', unread=True, webUrl='', imported=False,
            body='The report is due on 23 November.'))['localId']

    def test_historical_hash_mail_citation_restores_run_and_transcript_without_new_read(self):
        mail_id = self.citation_mail('original')
        self.assertRegex(mail_id, r'^[a-f0-9]{64}$')
        answer = f'原通知见[课程邮件](mail://{mail_id})。'
        self.model(tool_call('read_mail', dict(message_id=mail_id), 'read-original'), AIMessage(content=answer))
        first = self.start('读一下这封邮件。')
        original = self.execute_run(first)
        self.assertEqual(original['status'], 'completed', original.get('error'))
        expected = next(s for s in original['sources'] if s['id'] == mail_id)
        self.reopen()
        model = self.model(AIMessage(content=answer))
        result = self.execute_run(self.start('再给我刚才那个来源链接。', conversationId=first['conversationId']))
        self.assertEqual(result['status'], 'completed', result.get('error'))
        self.assertEqual(len(model.seen), 1)
        self.assertFalse(any(e.get('tool') in ('search_mail', 'read_mail') for e in result['events']))
        self.assertEqual(result['sources'], [expected])
        conversation = self.service.call('agent:getConversation', first['conversationId'])
        self.assertEqual(conversation['turns'][-1]['sources'], [expected])
        self.assertEqual(conversation['turns'][-1]['content'], answer)

    def test_historical_citations_do_not_authorize_unknown_other_chat_or_deleted_mail(self):
        known, unread, other, deleted = [self.citation_mail(name) for name in ('known', 'unread', 'other', 'deleted')]
        self.model(tool_call('read_mail', dict(message_id=known), 'read-known'),
                   tool_call('read_mail', dict(message_id=deleted), 'read-deleted'), AIMessage(content='已读两份来源。'))
        first = self.start('核对两份通知。')
        self.assertEqual(self.execute_run(first)['status'], 'completed')
        self.model(tool_call('read_mail', dict(message_id=other), 'read-other'), AIMessage(content='另一会话的来源。'))
        self.assertEqual(self.execute_run(self.start('独立查看第三份通知。'))['status'], 'completed')
        self.service.mail.store.delete_local([deleted])
        answer = '\n'.join(f'[来源](mail://{ident})' for ident in (known, unread, other, deleted, '0' * 64))
        self.model(AIMessage(content=answer))
        result = self.execute_run(self.start('只列之前引用过且仍存在的来源。', conversationId=first['conversationId']))
        self.assertEqual(result['status'], 'completed', result.get('error'))
        self.assertEqual([(s['kind'], s['id']) for s in result['sources']], [('mail', known)])
        self.assertEqual(self.service.call('agent:getConversation', first['conversationId'])['turns'][-1]['sources'], result['sources'])

    def test_historical_task_citations_keep_trash_but_reject_permanently_removed_records(self):
        task = self.service.repo.call('createTodo', dict(title='Course discussion', due={'mode': 'none'}))['value']
        self.model(tool_call('list_tasks', dict(query='Course discussion'), 'find-task'), AIMessage(content='已找到待办。'))
        first = self.start('找到课程讨论。')
        self.assertEqual(self.execute_run(first)['status'], 'completed')
        self.service.repo.call('trashTodo', task['id'], task['revision'])
        answer = f'[过去的讨论](task://{task["id"]})'
        self.model(AIMessage(content=answer))
        trashed = self.execute_run(self.start('给我原任务链接。', conversationId=first['conversationId']))
        self.assertEqual(trashed['status'], 'completed', trashed.get('error'))
        self.assertEqual([(s['kind'], s['id']) for s in trashed['sources']], [('task', task['id'])])
        with self.service.repo.db:
            self.service.repo.db.execute('DELETE FROM todos WHERE id=?', (task['id'],))
        self.model(AIMessage(content=answer))
        removed = self.execute_run(self.start('再找一次。', conversationId=first['conversationId']))
        self.assertEqual(removed['status'], 'completed', removed.get('error'))
        self.assertEqual(removed['sources'], [])

    def test_historical_citations_require_complete_identifiers_and_scheme_boundaries(self):
        mail_id = self.citation_mail('boundary')
        file = self.service.files._save('reference.md', b'Course reference.', 'local')
        self.model(tool_call('read_mail', dict(message_id=mail_id), 'read-mail'),
                   tool_call('read_managed_file', dict(file_id=file['id']), 'read-file'), AIMessage(content='已读来源。'))
        first = self.start('读邮件和文件。')
        self.assertEqual(self.execute_run(first)['status'], 'completed')
        malformed = [f'[错误链接]({kind}://{ident}{suffix})'
                     for kind, ident in (('mail', mail_id), ('file', file['id']))
                     for suffix in ('a', '_extra', '/child', '?query=1', '#fragment', '.json', '%2f')]
        malformed.extend((f'[错误类型](file://{mail_id})', f'[错误类型](mail://{file["id"]})',
                          f'[错误协议](xmail://{mail_id})', f'[外部链接](https://example.invalid/mail://{mail_id})',
                          f'[外部参数](https://example.invalid/?next=mail://{mail_id})'))
        self.model(AIMessage(content='\n'.join(malformed)))
        result = self.execute_run(self.start('检查这些链接。', conversationId=first['conversationId']))
        self.assertEqual(result['status'], 'completed', result.get('error'))
        self.assertEqual(result['sources'], [])
        self.model(AIMessage(content=f'[邮件](mail://{mail_id}) [文件](file://{file["id"]})'))
        valid = self.execute_run(self.start('使用完整的原链接。', conversationId=first['conversationId']))
        self.assertEqual(valid['status'], 'completed', valid.get('error'))
        self.assertEqual({(s['kind'], s['id']) for s in valid['sources']}, {('mail', mail_id), ('file', file['id'])})

    def test_pause_stream_then_retry_does_not_duplicate_user_or_assistant(self):
        run = self.start('给我一个完整答复。')
        paused = []
        def pause():
            if not paused:
                paused.append(True)
                self.service.call('agent:pauseRun', run['id'])
        self.model(AIMessage(content='部分内容。' * 100), hook=pause)
        self.assertEqual(self.execute_run(run)['status'], 'paused')
        self.reopen()
        self.model(AIMessage(content='恢复后完整答复。'))
        resumed = self.service.call('agent:resumeRun', dict(id=run['id']))
        self.service.call('agent:resumeRun', dict(id=run['id']))
        result = self.execute_run(resumed)
        self.assertEqual(result['status'], 'completed', result.get('error'))
        turns = self.service.call('agent:getConversation', run['conversationId'])['turns']
        self.assertEqual([t['role'] for t in turns], ['user', 'assistant'])
        self.assertEqual(turns[-1]['content'], '恢复后完整答复。')

    def test_retry_after_document_tool_does_not_duplicate_artifact(self):
        self.model(tool_call('task',dict(subagent_type='document',description='Generate the requested plan file.'),'delegate-plan'),
                   tool_call('create_document', dict(name='plan.md', format='md', title='计划', content='Synthetic plan.')),
                   RuntimeError('synthetic provider outage not-a-real-key'))
        run = self.start('生成一份计划文件。')
        first = self.execute_run(run)
        self.assertEqual(first['status'], 'failed')
        self.assertNotIn('not-a-real-key', first['error'])
        self.assertEqual(len(first['artifacts']), 1)
        artifact = first['artifacts'][0]['id']
        self.reopen()
        model = self.model(AIMessage(content='计划文件已生成。'),AIMessage(content='已保留原有计划文件。'))
        resumed = self.service.call('agent:resumeRun', dict(id=run['id']))
        done = self.execute_run(resumed)
        self.assertEqual(done['status'], 'completed', done.get('error'))
        self.assertEqual([a['id'] for a in done['artifacts']], [artifact])
        self.assertEqual(len(self.service.files.list_files()), 1)
        self.assertTrue(any(isinstance(m, ToolMessage) and artifact in m.content for m in model.seen[0]))

    def test_cancel_is_terminal_and_does_not_run_queued_model(self):
        model = self.model(AIMessage(content='不应执行'))
        run = self.start()
        self.service.call('agent:cancelRun', run['id'])
        self.assertFalse(asyncio.run(self.service.agent.run_next()))
        self.assertEqual(self.service.call('agent:cancelRun', run['id'])['status'], 'canceled')
        with self.assertRaises(ValueError):
            self.service.call('agent:resumeRun', dict(id=run['id']))
        self.assertFalse(model.seen)

    def test_delete_clears_conversation_and_framework_checkpoint(self):
        self.model(AIMessage(content='已回答。'))
        run = self.start('待删除的对话')
        with self.assertRaises(ValueError):
            self.service.call('agent:deleteConversation', run['conversationId'])
        self.assertEqual(self.execute_run(run)['status'], 'completed')
        self.service.call('agent:deleteConversation', run['conversationId'])
        self.assertEqual(self.service.call('agent:listConversations'), [])
        with self.assertRaises(ValueError):
            self.service.call('agent:getRun', run['id'])
        from langgraph.checkpoint.sqlite import SqliteSaver
        with SqliteSaver.from_conn_string(str(self.directory / 'agent-checkpoints.sqlite')) as saver:
            self.assertIsNone(saver.get_tuple({'configurable': {'thread_id': run['conversationId']}}))

    def test_configuration_change_blocks_retry_and_unknown_api_is_rejected(self):
        self.model(RuntimeError('synthetic failure'))
        run = self.start()
        self.assertEqual(self.execute_run(run)['status'], 'failed')
        self.service.settings.for_request = lambda: {**CONFIG, 'model': 'changed-model'}
        self.model(AIMessage(content='不应请求新模型'))
        self.service.call('agent:resumeRun', dict(id=run['id']))
        failed = self.execute_run(run)
        self.assertEqual(failed['status'], 'failed')
        self.assertIn('AI配置已改变', failed['error'])
        with self.assertRaises(ValueError):
            self.service.call('agent:executeShell', 'echo unsafe')

    def test_truncated_answer_retry_really_calls_model_again(self):
        self.model(AIMessage(content='被截断的回答', response_metadata={'finish_reason': 'length'}))
        run = self.start('完整解释这个问题。')
        failed = self.execute_run(run)
        self.assertEqual(failed['status'], 'failed')
        model = self.model(AIMessage(content='这次是完整回答。'))
        self.service.call('agent:resumeRun', dict(id=run['id']))
        retried = self.execute_run(run)
        self.assertEqual(retried['status'], 'completed', retried.get('error'))
        self.assertEqual(len(model.seen), 1)
        self.assertEqual(retried['partialText'], '这次是完整回答。')

    def test_cancel_waiting_run_then_new_turn_closes_old_tool_call(self):
        self.model(tool_call('ask_user', dict(question='旧项目是哪一个？', options=['A', 'B']), 'old-question'))
        first = self.start('旧任务需要澄清。')
        self.assertEqual(self.execute_run(first)['status'], 'waiting')
        self.service.call('agent:cancelRun', first['id'])
        model = self.model(AIMessage(content='现在只处理新任务。'))
        second = self.start('请开始一个新的任务。', conversationId=first['conversationId'])
        result = self.execute_run(second)
        self.assertEqual(result['status'], 'completed', result.get('error'))
        history = model.seen[0]
        call_ids = {call['id'] for message in history if isinstance(message, AIMessage) for call in message.tool_calls}
        result_ids = {message.tool_call_id for message in history if isinstance(message, ToolMessage)}
        self.assertFalse(call_ids - result_ids, 'New turn contains unpaired calls from canceled work')
        self.assertTrue(any(isinstance(m, HumanMessage) and '新的任务' in m.content for m in history))
        self.assertEqual(self.service.call('agent:getRun', first['id'])['status'], 'canceled')

    def test_stale_clarification_answer_cannot_answer_a_later_question(self):
        self.model(tool_call('ask_user', dict(question='哪个课程？', options=['A', 'B']), 'question-1'))
        run = self.start('先确定课程再确定项目。')
        first = self.execute_run(run)
        question1 = first['clarification']['id']
        self.model(tool_call('ask_user', dict(question='哪个项目？', options=['Project 2', 'Final']), 'question-2'))
        self.service.call('agent:resumeRun', dict(id=run['id'], answer='A', questionId=question1))
        second = self.execute_run(run)
        self.assertEqual(second['status'], 'waiting')
        self.assertNotEqual(second['clarification']['id'], question1)
        before = len(self.service.call('agent:getConversation', run['conversationId'])['turns'])
        with self.assertRaises(ValueError):
            self.service.call('agent:resumeRun', dict(id=run['id'], answer='A', questionId=question1))
        self.assertEqual(len(self.service.call('agent:getConversation', run['conversationId'])['turns']), before)

    def test_second_interrupt_checkpoint_recovers_without_reusing_first_answer(self):
        self.model(tool_call('ask_user', dict(question='哪个课程？', options=['A', 'B']), 'question-1'))
        run = self.start('先确定课程，再确定项目。')
        first = self.execute_run(run)
        self.model(tool_call('ask_user', dict(question='哪个项目？', options=['Project 2', 'Final']), 'question-2'))
        self.service.call('agent:resumeRun', dict(id=run['id'], answer='A', questionId=first['clarification']['id']))
        original_update = self.service.agent._update
        failed_once = []
        def fail_waiting_write(id, changes, **kwargs):
            if changes.get('status') == 'waiting' and changes.get('clarification', {}).get('question') == '哪个项目？' and not failed_once:
                failed_once.append(True)
                raise RuntimeError('Synthetic host crash after framework checkpoint')
            return original_update(id, changes, **kwargs)
        self.service.agent._update = fail_waiting_write
        failed = self.execute_run(run)
        self.assertEqual(failed['status'], 'failed')
        self.reopen()
        model = self.model(AIMessage(content='不能提前运行'))
        self.service.call('agent:resumeRun', dict(id=run['id']))
        recovered = self.execute_run(run)
        self.assertEqual(recovered['status'], 'waiting', recovered.get('error'))
        self.assertEqual(recovered['clarification']['question'], '哪个项目？')
        self.assertFalse(model.seen, 'An old answer unexpectedly satisfied the new interrupt')

    def test_delete_waits_for_stream_worker_to_acknowledge_pause(self):
        run = self.start('先输出一段内容。')
        checked = []
        def pause_and_try_delete():
            if checked:
                return
            checked.append(True)
            stopping = self.service.call('agent:pauseRun', run['id'])
            self.assertTrue(stopping['stopping'])
            with self.assertRaises(ValueError):
                self.service.call('agent:resumeRun', dict(id=run['id']))
            with self.assertRaises(ValueError):
                self.start('暂不能并发新任务。', conversationId=run['conversationId'])
            with self.assertRaises(ValueError):
                self.service.call('agent:deleteConversation', run['conversationId'])
        self.model(AIMessage(content='尚在输出中的内容。' * 100), hook=pause_and_try_delete)
        paused = self.execute_run(run)
        self.assertEqual(paused['status'], 'paused')
        self.assertTrue(checked)
        self.service.call('agent:deleteConversation', run['conversationId'])
        self.assertEqual(self.service.call('agent:listConversations'), [])

    def test_compacted_history_retains_run_ownership_for_host_commit_recovery(self):
        self.model(AIMessage(content='框架已完成、尚未写到界面的答案。'))
        run = self.start('需要持久恢复的原始任务。')
        original_save = self.service.agent._save_conversation
        failed_once = []
        def fail_final_visible_save(value):
            if any(turn['role'] == 'assistant' for turn in value['turns']) and not failed_once:
                failed_once.append(True)
                raise RuntimeError('Synthetic failure after model checkpoint before visible answer commit')
            return original_save(value)
        self.service.agent._save_conversation = fail_final_visible_save
        self.assertEqual(self.execute_run(run)['status'], 'failed')
        self.reopen()

        async def compact_checkpoint():
            import aiosqlite
            from typing import NotRequired
            from langchain.agents import AgentState, create_agent
            from langchain_core.messages import RemoveMessage
            from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
            from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
            class SameHostState(AgentState):
                host_run_id: NotRequired[str]
            async with aiosqlite.connect(self.directory / 'agent-checkpoints.sqlite') as connection:
                saver = AsyncSqliteSaver(connection, serde=JsonPlusSerializer(allowed_msgpack_modules=[]))
                graph = create_agent(ScriptedModel(script=[]), tools=[], checkpointer=saver, state_schema=SameHostState)
                config = {'configurable': {'thread_id': run['conversationId']}}
                await graph.aupdate_state(config, {'messages': [RemoveMessage(id=run['id'] + ':user'),
                    HumanMessage(content='历史已压缩；保留已有完成结果。', id='synthetic-summary')]}, as_node='model')
                state = await graph.aget_state(config)
                self.assertEqual(state.values['host_run_id'], run['id'])
                self.assertFalse(any(message.id == run['id'] + ':user' for message in state.values['messages']))
        asyncio.run(compact_checkpoint())
        model = self.model(AIMessage(content='不应重复请求模型。'))
        self.service.call('agent:resumeRun', dict(id=run['id']))
        result = self.execute_run(run)
        self.assertEqual(result['status'], 'completed', result.get('error'))
        self.assertEqual(result['partialText'], '框架已完成、尚未写到界面的答案。')
        self.assertFalse(model.seen)
        turns = self.service.call('agent:getConversation', run['conversationId'])['turns']
        self.assertEqual([turn['role'] for turn in turns], ['user', 'assistant'])

    def test_parent_reconciles_exited_worker_without_automatically_replaying(self):
        run = self.start('异常退出后恢复。')
        self.service.agent.worker = False
        self.service.agent._update(run['id'], {'status': 'running', '_executing': True})
        closed = []
        scheduled = []
        self.service.agent.process = SimpleNamespace(poll=lambda: 17,
                                                     stdin=SimpleNamespace(close=lambda: closed.append(True)))
        self.service.agent.schedule = lambda: scheduled.append(True)
        restored = self.service.call('agent:getRun', run['id'])
        self.assertEqual(restored['status'], 'paused')
        self.assertFalse(restored['stopping'])
        self.assertEqual(closed, [True])
        self.assertIsNone(self.service.agent.process)
        self.assertFalse(scheduled, 'A crashed worker must not silently restart external work')
        resumed = self.service.call('agent:resumeRun', {'id': run['id']})
        self.assertEqual(resumed['status'], 'queued')
        self.assertEqual(scheduled, [True])

        # A cancel issued just before the worker crashed must retain cancellation,
        # but release the execution ownership so the user can delete or start anew.
        self.service.agent._update(run['id'], {'status': 'canceled', '_executing': True})
        self.service.agent.process = SimpleNamespace(poll=lambda: 17, stdin=None)
        canceled = self.service.call('agent:getRun', run['id'])
        self.assertEqual(canceled['status'], 'canceled')
        self.assertFalse(canceled['stopping'])
        self.service.call('agent:deleteConversation', run['conversationId'])
        self.assertEqual(self.service.call('agent:listConversations'), [])

    def test_pause_cancel_cannot_relabel_completion_after_a_stale_read(self):
        self.model(AIMessage(content='已经完整完成的答案。'))
        run = self.start('完成这次任务。')
        self.assertEqual(self.execute_run(run)['status'], 'completed')
        original_read = self.service.agent._run
        for action in ('pauseRun', 'cancelRun'):
            stale_returned = []
            def stale_first_read(id):
                value = original_read(id)
                if id == run['id'] and not self.service.agent.db.in_transaction:
                    stale_returned.append(True)
                    # Simulate control handling reading running immediately before
                    # the worker's completion transaction commits.
                    value['status'] = 'running'
                    value['_executing'] = True
                return value
            with patch.object(self.service.agent, '_run', side_effect=stale_first_read):
                response = self.service.call('agent:' + action, run['id'])
            self.assertEqual(response['status'], 'completed')
            self.assertFalse(stale_returned, 'Control state must be read inside its write transaction')
            stored = self.service.call('agent:getRun', run['id'])
            self.assertEqual(stored['status'], 'completed')
            self.assertEqual(stored['partialText'], '已经完整完成的答案。')
            turns = self.service.call('agent:getConversation', run['conversationId'])['turns']
            self.assertEqual([turn['role'] for turn in turns], ['user', 'assistant'])

    def test_each_turn_discovers_unread_mail_from_actual_prior_sender_and_account(self):
        def mail(remote,binding='school',sender='teacher@example.invalid',body='not-yet-read-secret-body',date='2026-11-21T00:00:00Z'):
            return self.service.mail.store.put(binding,'identity','student@example.invalid',dict(id=remote,subject=remote,sender=sender,receivedAt=date,body=body,preview='',attachments=[]))['localId']
        old=mail('old',body='We are checking the exam room.',date='2026-11-20T00:00:00Z')
        self.model(tool_call('read_mail',dict(message_id=old),'read'),AIMessage(content='尚在核查。'))
        first=self.execute_run(self.start('看看老师的通知',mailIds=[old]))
        new=mail('final-confirmation')
        other=mail('same-name-other-person',sender='other@example.invalid')
        other_account=mail('other-account',binding='private')
        model=self.model(AIMessage(content='会先查新通知。'))
        self.execute_run(self.start('现在确认了吗？',conversationId=first['conversationId']))
        context=str(model.seen[0][0].content)
        self.assertIn(new,context);self.assertNotIn(other,context);self.assertNotIn(other_account,context)
        self.assertNotIn('not-yet-read-secret-body',context)
        self.assertIn('不代表已经读过',context)
        self.assertEqual(self.service.agent._related_mail_context({'conversationId':'fresh','_input':{}}),'')

    def test_related_mail_discovery_ignores_removed_sources(self):
        run=self.start('hello')
        self.service.agent._update(run['id'],{'sources':[{'kind':'mail','id':'missing-mail','title':'gone'}]})
        self.assertEqual(self.service.agent._related_mail_context(self.service.agent._run(run['id'])),'')


if __name__ == '__main__':
    unittest.main(verbosity=2)
