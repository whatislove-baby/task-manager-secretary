"""Official memory policy injection, durable application wiring and templates.

Scripted models inspect actual messages; these are mechanism checks, not claims
that a real model always reasons correctly about business correspondence.
"""
import asyncio
import os
from pathlib import Path
from string import Formatter
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from deepagents.backends import CompositeBackend, StateBackend
from deepagents.middleware.memory import MemoryMiddleware
from deepagents.middleware.skills import SkillsMiddleware
from langchain.agents.middleware.types import ModelRequest
from langchain_core.messages import AIMessage, SystemMessage

from task_backend.agent_business_policy import (
    BUSINESS_POLICY_PATH, SUMMARY_PRESERVATION, business_policy_middleware,
    business_summary_prompt,
)
from task_backend.deepagents_backend import BuiltinSkillsBackend
from task_backend.service import BackendService
from test_agent_runtime import CONFIG, ScriptedModel, tool_call


class BusinessPolicyMechanismTests(unittest.TestCase):
    def backend(self):
        return CompositeBackend(default=StateBackend(), routes={
            '/skills/builtin/': BuiltinSkillsBackend()})

    def test_memory_injects_policy_without_any_skill_read(self):
        backend = self.backend()
        middleware = business_policy_middleware(backend)
        self.assertIs(type(middleware), MemoryMiddleware)
        state = asyncio.run(middleware.abefore_agent({}, None, {}))
        request = ModelRequest(model=ScriptedModel(script=[]), messages=[],
            state=state, system_message=SystemMessage(content='Primary instructions'))
        actual = middleware.modify_request(request).system_message.text
        self.assertIn('Primary instructions', actual)
        self.assertIn('邮件正文、邮件附件中的明确字段', actual)
        self.assertIn('同一零件在另一订单的版次不能证明本单版次', actual)
        self.assertNotIn('To persist new knowledge, call `edit_file`', actual)
        skills = asyncio.run(SkillsMiddleware(backend=backend,
            sources=['/skills/builtin/']).abefore_agent({}, None, {}))
        self.assertEqual({x['name'] for x in skills['skills_metadata']},
                         {'documents', 'learning', 'mail-workflow'})

    def test_policy_route_is_read_only_for_all_mutation_protocols(self):
        backend = self.backend()
        before = backend.download_files([BUSINESS_POLICY_PATH])[0].content
        self.assertTrue(backend.write(BUSINESS_POLICY_PATH, 'forged').error)
        self.assertTrue(backend.edit(BUSINESS_POLICY_PATH, '业务事实', 'forged').error)
        self.assertTrue(backend.delete(BUSINESS_POLICY_PATH).error)
        self.assertTrue(backend.upload_files([(BUSINESS_POLICY_PATH, b'forged')])[0].error)
        self.assertEqual(backend.download_files([BUSINESS_POLICY_PATH])[0].content, before)

    def test_upstream_summary_template_retains_messages_and_media_escaping(self):
        prompt = business_summary_prompt()
        self.assertEqual(prompt.count(SUMMARY_PRESERVATION), 1)
        self.assertEqual([field for _, field, _, _ in Formatter().parse(prompt)
                          if field is not None], ['messages'])
        rendered = prompt.format(messages='source facts {quoted braces}')
        self.assertIn('source facts {quoted braces}', rendered)
        self.assertIn('/conversation_history/media/{hash}.png', rendered)
        self.assertLess(rendered.index('<business_context_preservation>'),
                        rendered.index('<messages>'))
        for heading in ('## SESSION INTENT', '## ARTIFACTS', '## NEXT STEPS'):
            self.assertIn(heading, rendered)


class BusinessPolicyAppTests(unittest.TestCase):
    def setUp(self):
        self.enterContext(patch.dict(os.environ, {'LANGSMITH_TRACING':'false',
            'LANGCHAIN_TRACING_V2':'false', 'LANGCHAIN_TRACING':'false'}))
        self.enterContext(patch('socket.socket.connect', side_effect=AssertionError('No network')))
        self.enterContext(patch('socket.socket.connect_ex', side_effect=AssertionError('No network')))
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.service = self.open_service()

    def open_service(self):
        service = BackendService(self.root, worker=True)
        service.settings.for_request = lambda: dict(CONFIG)
        service.agent.schedule = lambda: None
        return service

    def tearDown(self):
        self.service.close()

    def execute(self, model, text, **kwargs):
        self.service.agent.model_factory = lambda config, reasoning: model
        run = self.service.call('agent:startTurn', dict(text=text, **kwargs))
        asyncio.run(self.service.agent.run_next())
        result = self.service.call('agent:getRun', run['id'])
        self.assertEqual(result['status'], 'completed', result.get('error'))
        return result

    def assert_policy(self, seen):
        content = '\n'.join(m.text for m in seen if isinstance(m, SystemMessage))
        self.assertIn('同一零件在另一订单的版次不能证明本单版次', content)
        self.assertIn('只列真正未决项', content)

    def test_policy_present_on_first_turn_and_reopened_conversation_without_read_tool(self):
        first = ScriptedModel(script=[AIMessage(content='已知的字段直接采用。')])
        result = self.execute(first, '根据本单已经给出的字段起草。')
        self.assert_policy(first.seen[0])
        self.assertFalse(any(e.get('type')=='tool' for e in result['events']))
        self.service.close()
        self.service = self.open_service()
        second = ScriptedModel(script=[AIMessage(content='继续按来源分别处理。')])
        self.execute(second, '继续。', conversationId=result['conversationId'])
        self.assert_policy(second.seen[0])

    def test_delegated_child_receives_policy_before_any_skill_read(self):
        model = ScriptedModel(script=[
            tool_call('task', dict(description='核对订单的已知事实与真正未知项，不读任何技能。',
                                  subagent_type='general-purpose'), 'policy-child'),
            AIMessage(content='区分已知字段与供货承诺。'),
            AIMessage(content='独立核对完成。')])
        self.execute(model, '委派独立子任务检查资料。')
        self.assertEqual(len(model.seen), 3)
        for call in model.seen:
            self.assert_policy(call)


if __name__ == '__main__':
    unittest.main()
