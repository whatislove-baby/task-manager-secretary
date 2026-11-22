"""Production queue workers with real graphs/stores and a blocking model fixture."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
import threading
import time
import unittest

from langchain_core.messages import AIMessage, HumanMessage
from task_backend.agent_worker import run_queues
from task_backend.service import BackendService
import test_agent_runtime as support


class BlockingModel(support.ScriptedModel):
    entered: object
    release: object

    async def _astream(self, messages, **kwargs):
        self.entered.set()
        # Deliberately synchronous, as a conversion/vision tool may be. This
        # would freeze both chats if they shared one asyncio event-loop thread.
        if not self.release.wait(12):
            raise AssertionError('Background fixture was not released')
        async for item in super()._astream(messages, **kwargs):
            yield item


class WorkerQueueTests(unittest.TestCase):
    setUp = support.AgentRuntimeTests.setUp
    tearDown = support.AgentRuntimeTests.tearDown
    open_service = support.AgentRuntimeTests.open_service
    model = support.AgentRuntimeTests.model
    start = support.AgentRuntimeTests.start

    def child(self, **kwargs):
        home = self.service.call('agent:getSecretary')
        return self.service.agent.create_child(dict(parentConversationId=home['id'],
            title='后台核对', goal='核对资料后告诉我。'), **kwargs)

    def until(self, predicate, timeout=8):
        deadline = time.monotonic()+timeout
        while time.monotonic() < deadline:
            if predicate(): return
            time.sleep(.03)
        self.fail('Timed out waiting for production worker state')

    def test_blocked_child_does_not_block_home_and_checkpoints_remain_separate(self):
        home = self.service.call('agent:getSecretary')
        child = self.child()
        child_run = child['activeRun']['id']
        entered, release, stop = threading.Event(), threading.Event(), threading.Event()
        owners, created, calls = {}, [], []
        background = BlockingModel(script=[AIMessage(content='后台已核对。')], entered=entered, release=release)
        secretary = support.ScriptedModel(script=[
            support.tool_call('get_child_tasks', dict(conversation_id=child['id']), 'check-progress'),
            AIMessage(content='任务还在进行中，可以打开子对话看进度。')])

        def factory(directory, worker):
            service = BackendService(directory, worker=worker)
            lane = threading.current_thread().name
            owners[lane] = threading.get_ident()
            created.append(service)
            service.settings.for_request = lambda: dict(support.CONFIG)
            service.agent.model_factory = lambda config, reasoning: secretary if lane.endswith('secretary') else background
            original = service.agent._model
            def model(config, reasoning):
                calls.append((lane, threading.get_ident()))
                return original(config, reasoning)
            service.agent._model = model
            return service

        with ThreadPoolExecutor(max_workers=1) as pool:
            worker = pool.submit(run_queues, self.directory, stop, factory)
            try:
                self.assertTrue(entered.wait(6))
                self.assertEqual(self.service.agent._run(child_run)['status'], 'running')
                home_run = self.start('刚才的任务做到哪一步了？', conversationId=home['id'])
                self.until(lambda: self.service.agent._run(home_run['id'])['status'] in ('completed','failed'))
                self.assertEqual(self.service.agent._run(home_run['id'])['status'], 'completed')
                self.assertFalse(release.is_set())
                self.assertEqual(self.service.agent._run(child_run)['status'], 'running')
                content = str(secretary.seen[-1])
                self.assertIn('running', content)
                self.assertEqual(self.service.call('agent:getConversation', child['id'])['turnCount'], 1)
                release.set()
                self.until(lambda: self.service.agent._run(child_run)['status'] in ('completed','failed'))
                self.assertEqual(self.service.agent._run(child_run)['status'], 'completed')
                self.assertEqual(self.service.call('agent:getConversation', child['id'])['turns'][-1]['content'], '后台已核对。')
                self.assertEqual(self.service.call('agent:getConversation', home['id'])['turns'][-1]['content'], '任务还在进行中，可以打开子对话看进度。')
            finally:
                release.set(); stop.set()
                worker.result(timeout=12)
        self.assertEqual(len(set(owners.values())), 2)
        self.assertTrue(all(owners[lane] == owner for lane, owner in calls))
        self.assertTrue(all(service.agent.closed for service in created))
        # Continue both persisted official graphs from another service instance.
        # Assert visible history instead of depending on the saver byte schema.
        home_model = self.model(AIMessage(content='秘书继续。'))
        self.start('继续刚才的话题', conversationId=home['id'])
        self.assertTrue(asyncio.run(self.service.agent.run_next(queue='secretary')))
        self.assertTrue(any(isinstance(m, HumanMessage) and '刚才的任务' in m.content for m in home_model.seen[0]))
        child_model = self.model(AIMessage(content='子对话继续。'))
        self.start('继续核对', conversationId=child['id'])
        self.assertTrue(asyncio.run(self.service.agent.run_next(queue='background')))
        self.assertTrue(any(isinstance(m, HumanMessage) and '核对资料后告诉我' in m.content for m in child_model.seen[0]))
        self.assertFalse(any(isinstance(m, HumanMessage) and '刚才的任务' in m.content for m in child_model.seen[0]))

    def test_queue_selection_and_delegating_turn_completion(self):
        home = self.service.call('agent:getSecretary')
        parent = self.start('请单独核对', conversationId=home['id'])
        self.service.agent._update(parent['id'], {'status':'running', '_executing':True})
        child = self.child(source_run_id=parent['id'])
        self.assertFalse(asyncio.run(self.service.agent.run_next(queue='background')))
        self.assertEqual(self.service.agent._run(child['activeRun']['id'])['status'], 'queued')
        self.service.agent._update(parent['id'], {'status':'completed', '_executing':False})
        self.model(AIMessage(content='子任务已核对。'))
        self.assertFalse(asyncio.run(self.service.agent.run_next(queue='secretary')))
        self.assertTrue(asyncio.run(self.service.agent.run_next(queue='background')))
        ordinary = self.start('普通对话')
        self.assertFalse(asyncio.run(self.service.agent.run_next(queue='secretary')))
        self.model(AIMessage(content='普通回答。'))
        self.assertTrue(asyncio.run(self.service.agent.run_next(queue='background')))
        self.assertEqual(self.service.agent._run(ordinary['id'])['status'], 'completed')
        with self.assertRaises(ValueError):
            asyncio.run(self.service.agent.run_next(queue='invalid'))

    def test_cancel_blocking_child_keeps_cancellation_and_home_can_continue(self):
        home = self.service.call('agent:getSecretary')
        child = self.child()
        child_run = child['activeRun']['id']
        entered, release, stop = threading.Event(), threading.Event(), threading.Event()
        background = BlockingModel(script=[AIMessage(content='不应成为已完成结果。')], entered=entered, release=release)
        secretary = support.ScriptedModel(script=[AIMessage(content='可以继续处理其他事项。')])
        def factory(directory, worker):
            service = BackendService(directory, worker=worker)
            service.settings.for_request = lambda: dict(support.CONFIG)
            model = secretary if threading.current_thread().name.endswith('secretary') else background
            service.agent.model_factory = lambda config, reasoning: model
            return service
        with ThreadPoolExecutor(max_workers=1) as pool:
            worker = pool.submit(run_queues, self.directory, stop, factory)
            try:
                self.assertTrue(entered.wait(6))
                self.service.call('agent:cancelRun', child_run)
                parent = self.start('继续处理其他事项', conversationId=home['id'])
                self.until(lambda: self.service.agent._run(parent['id'])['status'] == 'completed')
                self.assertFalse(release.is_set())
                release.set()
                self.until(lambda: not self.service.agent._run(child_run).get('_executing'))
                self.assertEqual(self.service.agent._run(child_run)['status'], 'canceled')
                self.assertEqual(self.service.call('agent:getConversation', child['id'])['turnCount'], 1)
            finally:
                release.set(); stop.set(); worker.result(timeout=12)

    def test_lane_failure_stops_other_lane_and_closes_on_owner_threads(self):
        from types import SimpleNamespace
        stop = threading.Event(); closed = []; owners = {}
        class Agent:
            async def run_next(self, queue=None):
                if queue == 'secretary': raise RuntimeError('private-provider-details')
                await asyncio.sleep(.01)
                return False
        def factory(directory, worker):
            name = threading.current_thread().name
            owners[name] = threading.get_ident()
            return SimpleNamespace(agent=Agent(), close=lambda: closed.append((name, threading.get_ident())))
        with self.assertRaisesRegex(RuntimeError, '^Agent worker stopped unexpectedly$'):
            run_queues(self.directory, stop, factory)
        self.assertTrue(stop.is_set())
        self.assertEqual(len(closed), 2)
        self.assertTrue(all(owners[name] == owner for name, owner in closed))


if __name__ == '__main__':
    unittest.main()
