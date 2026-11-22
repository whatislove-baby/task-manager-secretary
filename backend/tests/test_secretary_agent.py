"""Fixed home, child handoff and acknowledgement through the official graph."""
import asyncio
from unittest.mock import patch
from uuid import uuid4
import unittest

from langchain_core.messages import AIMessage
from task_backend.agent_tools import AgentTools
import test_agent_runtime as support


class SecretaryAgentTests(unittest.TestCase):
    setUp = support.AgentRuntimeTests.setUp
    tearDown = support.AgentRuntimeTests.tearDown
    open_service = support.AgentRuntimeTests.open_service
    reopen = support.AgentRuntimeTests.reopen
    model = support.AgentRuntimeTests.model
    start = support.AgentRuntimeTests.start
    execute_run = support.AgentRuntimeTests.execute_run

    def home(self):
        return self.service.call('agent:getSecretary')

    def test_fixed_home_survives_restart_and_does_not_replace_old_chats(self):
        self.model(AIMessage(content='普通聊天仍然保留。'))
        ordinary = self.execute_run(self.start('普通问题'))
        home = self.home()
        self.assertEqual(home['kind'], 'secretary')
        self.assertEqual(home['turns'], [])
        self.reopen()
        self.assertEqual(self.home()['id'], home['id'])
        self.assertEqual(len(self.service.call('agent:listConversations')), 2)
        self.assertEqual(self.service.call('agent:getConversation', ordinary['conversationId'])['kind'], 'chat')
        with self.assertRaisesRegex(ValueError, '固定保留'):
            self.service.call('agent:deleteConversation', home['id'])

    def test_manual_child_queues_once_and_preserves_exact_goal(self):
        home = self.home()
        goal = '把已上传资料写成一页简报；不确定的数字先问我。'
        value = dict(parentConversationId=home['id'], title='简报', goal=goal)
        child = self.service.call('agent:createChild', value)
        self.assertEqual(child['kind'], 'task')
        self.assertEqual(child['parentConversationId'], home['id'])
        self.assertEqual(child['handoff']['userRequest'], goal)
        self.assertEqual(child['turns'][0]['content'], goal)
        self.assertEqual(child['activeRun']['status'], 'queued')
        self.assertEqual(self.home()['children'][0]['status'], 'queued')
        with self.assertRaisesRegex(ValueError, '秘书主对话'):
            self.service.call('agent:createChild', {**value, 'parentConversationId': child['id']})

    def test_invalid_child_inputs_leave_no_orphan_and_stable_retry_reuses_task(self):
        value = dict(parentConversationId=self.home()['id'], title='一件事', goal='只处理已确认的内容')
        with self.assertRaises(ValueError):
            self.service.call('agent:createChild', {**value, 'mailIds':['0'*64]})
        self.assertEqual(len(self.service.call('agent:listConversations')), 1)
        id = str(uuid4())
        child = self.service.agent.create_child(value, stable_id=id)
        retry = self.service.agent.create_child(value, stable_id=id)
        self.assertEqual(child['activeRun']['id'], retry['activeRun']['id'])
        self.assertEqual(len(self.service.agent._runs(id)), 1)
        with self.assertRaisesRegex(ValueError, '另一个任务'):
            self.service.agent.create_child({**value, 'goal':'改变要求'}, stable_id=id)

    def test_official_graph_handoff_child_execution_and_parent_result_lookup(self):
        mail = self.service.mail.store.put('school', 'inbox', 'student@example.invalid',
            dict(id='notice', subject='实习资料', sender='mentor@example.invalid', receivedAt='2026-11-22T00:00:00Z',
                 body='先核实资料，不承诺已经发送。', preview='', attachments=[]))['localId']
        home = self.home()
        user = '这份实习资料单独处理一下，先核实内容，别发。'
        self.model(support.tool_call('create_child_conversation', dict(title='实习资料处理',
            goal=user, mail_ids=[mail]), 'delegate'), AIMessage(content='已经分派，点子对话查看进度。'))
        parent_run = self.execute_run(self.start(user, conversationId=home['id'], mailIds=[mail]))
        self.assertEqual(parent_run['status'], 'completed', parent_run.get('error'))
        child_id = next(a['id'] for a in parent_run['artifacts'] if a['kind']=='conversation')
        child = self.service.call('agent:getConversation', child_id)
        self.assertEqual(child['handoff']['userRequest'], user)
        self.assertEqual(child['mailIds'], [mail])
        self.assertEqual(child['activeRun']['status'], 'queued')
        self.assertEqual(self.service.call('agent:listPending'), [])
        self.model(support.tool_call('read_mail', dict(message_id=mail), 'child-read'),
                   AIMessage(content='已阅读，资料要求先核实，尚未发送。'))
        self.assertTrue(asyncio.run(self.service.agent.run_next()))
        self.assertEqual(self.service.call('agent:getConversation', child_id)['lastRun']['status'], 'completed')
        self.reopen()
        context = AgentTools(self.service, 'read-only', conversation_id=home['id'])
        try:
            actual = context.get_child_tasks(child_id)
        finally:
            context.close()
        self.assertEqual(actual['items'][0]['status'], 'completed')
        self.assertIn('尚未发送', actual['items'][0]['answer'])
        self.assertEqual(actual['items'][0]['mailIds'], [mail])
        self.assertEqual(self.home()['id'], home['id'])

    def test_refresh_setup_failure_is_visible_and_acknowledged_only_after_answer(self):
        home = self.home()
        self.model(support.tool_call('refresh_inbox', {}, 'refresh'), AIMessage(content='尚未连接邮箱，需要先配置。'))
        run = self.start('刷新一下', conversationId=home['id'])
        observations = []
        original = self.service.secretary.mark_briefed
        def mark(id):
            state = self.service.call('agent:getRun', id)
            conversation = self.service.call('agent:getConversation', home['id'])
            observations.append((state['status'], conversation['turns'][-1]['role']))
            return original(id)
        with patch.object(self.service.secretary, 'mark_briefed', mark):
            result = self.execute_run(run)
        self.assertEqual(result['status'], 'completed')
        self.assertEqual(observations, [('completed','assistant')])
        self.assertEqual(self.service.secretary.get_refresh(run['id'])['status'], 'needs_setup')
        card = self.service.call('agent:getBriefing', run['id'])
        self.assertIn('尚无已连接邮箱', card['warning'])
        self.assertEqual(card['items'], [])
        self.assertEqual(self.service.call('agent:listPending'), [])

    def test_model_failure_after_refresh_does_not_consume_briefing(self):
        self.model(support.tool_call('refresh_inbox', {}, 'refresh'), ValueError('synthetic model failure'))
        run = self.start('有什么要处理的', conversationId=self.home()['id'])
        with patch.object(self.service.secretary, 'mark_briefed', wraps=self.service.secretary.mark_briefed) as mark:
            result = self.execute_run(run)
            mark.assert_not_called()
        self.assertEqual(result['status'], 'failed')
        self.assertEqual(self.service.secretary.get_refresh(run['id'])['status'], 'needs_setup')

    def test_restart_recovers_only_completed_briefing_ack_window(self):
        self.model(support.tool_call('refresh_inbox', {}, 'refresh'), AIMessage(content='请先连接邮箱。'))
        run = self.start('刷新一下', conversationId=self.home()['id'])
        with patch.object(self.service.secretary, 'mark_briefed', side_effect=ValueError('simulated ack interruption')):
            result = self.execute_run(run)
        self.assertEqual(result['status'], 'completed')
        from task_backend.secretary import SecretaryService
        calls = []
        original = SecretaryService.mark_briefed
        def recover(instance, id, *args):
            calls.append(id)
            return original(instance, id, *args)
        with patch.object(SecretaryService, 'mark_briefed', recover):
            self.reopen()
        self.assertIn(run['id'], calls)

    def test_canceled_queued_job_cannot_be_resurrected_while_worker_claims_it(self):
        self.model(AIMessage(content='不应执行'))
        run = self.start('稍后取消', conversationId=self.home()['id'])
        original = self.service.agent._runs
        canceled = False
        def cancel_after_selection(conversation_id=None):
            nonlocal canceled
            rows = original(conversation_id)
            if not canceled and any(r['id']==run['id'] and r['status']=='queued' for r in rows):
                canceled = True
                self.service.call('agent:cancelRun', run['id'])
            return rows
        with patch.object(self.service.agent, '_runs', cancel_after_selection):
            self.execute_run(run)
        self.assertTrue(canceled)
        self.assertEqual(self.service.call('agent:getRun', run['id'])['status'], 'canceled')
        self.assertEqual(len(self.home()['turns']), 1)

    def test_same_turn_identical_delegation_with_different_call_ids_has_one_child(self):
        home = self.home()
        run = self.start('把这份简报单独处理', conversationId=home['id'])
        tools = AgentTools(self.service, run['id'], conversation_id=home['id'])
        try:
            first = tools.create_child_conversation('简报', '只用已确认资料', call_id='first-call')
            second = tools.create_child_conversation('简报', '只用已确认资料', call_id='second-call')
        finally:
            tools.close()
        self.assertEqual(first['id'], second['id'])
        self.assertEqual(len(self.home()['children']), 1)
        self.assertEqual(len(self.service.agent._runs(first['id'])), 1)

    def test_new_child_round_does_not_present_old_answer_as_current_result(self):
        child = self.service.call('agent:createChild', dict(parentConversationId=self.home()['id'],
                    title='简报', goal='汇总已确认资料'))
        self.model(AIMessage(content='第一版已做好，金额100元。'))
        first = self.execute_run(child['activeRun'])
        second = self.start('现在请改成200元', conversationId=child['id'])
        tools = AgentTools(self.service, 'read-only')
        try:
            item = tools.get_child_tasks(child['id'])['items'][0]
        finally:
            tools.close()
        self.assertEqual(item['status'], 'queued')
        self.assertEqual(item['currentRunId'], second['id'])
        self.assertEqual(item['answer'], '')
        self.assertEqual(item['previousAnswerRunId'], first['id'])
        self.assertIn('100元', item['previousAnswer'])

