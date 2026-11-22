"""Regression boundaries for one-pass delivery review in the official graph."""
import asyncio
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from task_backend.agent_tools import AgentTools
from task_backend.service import BackendService
from test_agent_runtime import CONFIG, ScriptedModel, tool_call


class DeliveryBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.enterContext(patch.dict(os.environ, {'LANGSMITH_TRACING':'false',
            'LANGCHAIN_TRACING_V2':'false', 'LANGCHAIN_TRACING':'false'}))
        self.enterContext(patch('socket.socket.connect', side_effect=AssertionError('No network')))
        self.enterContext(patch('socket.socket.connect_ex', side_effect=AssertionError('No network')))
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.service = BackendService(Path(self.temp.name), worker=True)
        self.addCleanup(self.service.close)
        self.service.settings.for_request = lambda: dict(CONFIG)
        self.service.agent.schedule = lambda: None
        self.run = self.service.agent.call('startTurn', {'text':'完成这份资料并附到邮件。'})
        self.ctx = self.context(self.run)
        self.file = self.ctx.create_document('Brief.md', 'md', 'Brief',
                                            'Final information.', call_id='file')['id']
        self.draft = self.ctx.create_mail_draft('Brief', 'See the brief.',
            to='recipient@example.invalid', call_id='draft')

    def context(self, run):
        value = AgentTools(self.service, run['id'], run['conversationId'])
        self.addCleanup(value.close)
        return value

    def execute(self, run, *script):
        model = ScriptedModel(script=list(script))
        self.service.agent.model_factory = lambda config, reasoning: model
        asyncio.run(self.service.agent.run_next())
        result = self.service.agent.call('getRun', run['id'])
        self.assertEqual(result['status'], 'completed', result.get('error'))
        return model, result

    def attach(self):
        return tool_call('replace_mail_draft', dict(draft_id=self.draft['id'],
            expected_revision=1, subject=self.draft['subject'], body=self.draft['body'],
            attachment_ids=[self.file]), 'attach')

    def test_rejected_completion_is_not_kept_as_an_unqualified_historical_fact(self):
        false_claim = 'Everything is delivered and all files are attached.'
        model, result = self.execute(self.run, AIMessage(content=false_claim),
                                    self.attach(), AIMessage(content='Now the file is attached.'))
        self.assertEqual(result['partialText'], 'Now the file is attached.')
        # Verify the actual next API context, not merely the visible transcript.
        self.assertFalse(any(isinstance(m, AIMessage) and m.text == false_claim
                             for call in model.seen[1:] for m in call))
        later = self.service.agent.call('startTurn', dict(text='What happened?',
                                            conversationId=self.run['conversationId']))
        next_model, _ = self.execute(later, AIMessage(content='The current attachment is present.'))
        self.assertFalse(any(isinstance(m, AIMessage) and m.text == false_claim
                             for m in next_model.seen[0]))

    def test_child_final_does_not_review_or_mutate_the_parent_deliverables(self):
        model, result = self.execute(self.run,
            tool_call('task', dict(description='Check the supplied wording only; do not manage delivery.',
                                  subagent_type='general-purpose'), 'child'),
            AIMessage(content='Wording review only: no changes needed.'),
            AIMessage(content='Ready for delivery.'),
            self.attach(), AIMessage(content='Attached the requested brief.'))
        # The third model call must be the parent receiving the child's final
        # report, not the child being diverted to fix the whole conversation.
        third = model.seen[2]
        self.assertTrue(any(isinstance(m, ToolMessage) and m.name=='task' for m in third))
        self.assertFalse(any(isinstance(m, HumanMessage) and m.id and
                             m.id.endswith(':delivery-review') for m in third))
        self.assertEqual(result['partialText'], 'Attached the requested brief.')

    def test_same_unresolved_snapshot_in_later_turn_does_not_silently_disappear(self):
        _, first = self.execute(self.run, AIMessage(content='Done.'), AIMessage(content='Done.'))
        self.assertIn('尚未附到', first['partialText'])
        later = self.service.agent.call('startTurn', dict(text='另外记个内部待办，然后核对交付。',
                                            conversationId=self.run['conversationId']))
        second_ctx = self.context(later)
        second_ctx.create_task_draft('Internal follow-up', call_id='todo')
        _, second = self.execute(later, AIMessage(content='Done again.'), AIMessage(content='Done again.'))
        self.assertIn('尚未附到', second['partialText'])
        self.assertIn(self.file, second['partialText'])


if __name__ == '__main__':
    unittest.main()
