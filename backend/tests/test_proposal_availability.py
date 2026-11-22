"""Real AgentService proposal availability, revisions and official compaction.

Only the provider responses and summary text are scripted. Production runtime,
official graph/middleware, persisted checkpoints and proposal stores are real.
No skill execution, approval or external network access is granted.
"""
from network_guard import no_external_network
import asyncio
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / 'backend'), str(ROOT / 'backend/tests')]

from deepagents.middleware.summarization import SummarizationMiddleware
from langchain_core.messages import AIMessage, ToolMessage
from langgraph.checkpoint.sqlite import SqliteSaver
from task_backend.service import BackendService
from test_agent_runtime import CONFIG, tool_call
from test_deepagents_runtime import RecordingModel


class ProposalAvailabilityTests(unittest.TestCase):
    def setUp(self):
        self.enterContext(patch.dict(os.environ, {'LANGSMITH_TRACING': 'false',
            'LANGCHAIN_TRACING_V2': 'false', 'LANGCHAIN_TRACING': 'false'}))
        self.enterContext(no_external_network())
        self.temp = tempfile.TemporaryDirectory()
        self.directory = Path(self.temp.name)
        self.service = self.open_service()

    def open_service(self):
        service = BackendService(self.directory, worker=True)
        service.settings.for_request = lambda: dict(CONFIG)
        service.agent.schedule = lambda: None
        return service

    def tearDown(self):
        self.service.close()
        self.temp.cleanup()

    def reopen(self):
        self.service.close()
        self.service = self.open_service()

    def model(self, *responses):
        model = RecordingModel(script=list(responses))
        self.service.agent.model_factory = lambda config, reasoning: model
        return model

    def execute(self, content, conversation=None):
        args = dict(text=content)
        if conversation:
            args['conversationId'] = conversation
        run = self.service.call('agent:startTurn', args)
        self.assertTrue(asyncio.run(self.service.agent.run_next()))
        result = self.service.call('agent:getRun', run['id'])
        self.assertEqual(result['status'], 'completed', result.get('error'))
        return result

    def proposal(self, name, instruction='Remember only the column layout.'):
        value = dict(title='Customer layout ' + name,
            scope=dict(kind='personal', label='My layout', emails=[], contextKey=name),
            instruction=instruction, sources=[])
        if name == 'propose_learning':
            return dict(value, kind='note')
        return dict(value, kind='memory', rationale='Reusable layout preference, no fixed commercial terms.')

    def library(self, name):
        return self.service.learning if name == 'propose_learning' else self.service.skills

    def tool_result(self, model, call_id):
        msg = next(m for m in model.seen[-1] if isinstance(m, ToolMessage) and m.tool_call_id == call_id)
        self.assertEqual(msg.status, 'success', msg.content)
        value = json.loads(msg.content)
        self.assertNotIn('error', value)
        return value

    def assert_available(self, model):
        for binding in model.bindings:
            self.assertIn('propose_learning', binding)
            self.assertIn('propose_skill', binding)
            # We are not turning on all large or executable operations.
            self.assertNotIn('run_skill', binding)
            self.assertNotIn('run_learning', binding)
            self.assertNotIn('create_business_document', binding)

    def test_proposals_are_available_without_loading_skill_or_creating_artifacts(self):
        model = self.model(AIMessage(content='No tools called.'))
        self.execute('Please correct the reusable rule.')
        self.assertIn('propose_skill', model.bindings[0])
        self.assertIn('propose_learning', model.bindings[0])
        self.assertEqual(self.service.skills.search(status='all'), [])
        self.assertEqual(self.service.learning.search(status='all'), [])

    def test_promoted_tools_create_and_revise_across_turn_and_worker_restart(self):
        for name in ('propose_learning', 'propose_skill'):
            with self.subTest(tool=name):
                initial = self.proposal(name)
                model = self.model(tool_call(name, dict(value=initial), name + '-initial'), AIMessage(content='Draft created.'))
                first = self.execute('Save the layout only, for my review.')
                parent = self.tool_result(model, name + '-initial')
                self.assert_available(model)
                corrected = dict(initial, parentId=parent['id'], instruction='Layout only. Currency and tax must be read from each order.')
                model = self.model(tool_call(name, dict(value=corrected), name + '-correct'), AIMessage(content='Correction drafted.'))
                self.execute('Do not save currency or tax as a permanent rule.', first['conversationId'])
                child = self.tool_result(model, name + '-correct')
                self.assert_available(model)
                self.assertEqual(child['parentId'], parent['id'])
                self.assertEqual(child['version'], 2)
                self.assertEqual(self.library(name).get(child['id'])['proposal']['instruction'], corrected['instruction'])
                self.reopen()
                corrected = dict(initial, instruction='Layout only; keep source and customer scope separate.')
                model = self.model(tool_call(name, dict(value=corrected, parentId=child['id']), name + '-reopen'), AIMessage(content='Revision drafted after restart.'))
                self.execute('Also keep the source scope explicit.', first['conversationId'])
                latest = self.tool_result(model, name + '-reopen')
                self.assert_available(model)
                self.assertEqual(latest['version'], 3)
                self.assertEqual(latest['parentId'], child['id'])
                self.assertEqual(self.library(name).search(status='approved'), [])
                self.assertTrue(all(v['status'] == 'draft' for v in self.library(name).search(status='all')))
                self.assertFalse(self.service.files.list_files())

    def test_promoted_tools_survive_actual_official_summary_without_skill_reread(self):
        summaries = []
        original_summary = SummarizationMiddleware
        def forced_summary(model, **kwargs):
            def summarize(messages):
                summaries.append(messages)
                return 'User wants only the layout saved. Currency and tax vary by order. Correct the existing draft; no approval.'
            kwargs.update(trigger=('messages', 5), keep=('messages', 2), summarizer=summarize)
            return original_summary(model, **kwargs)
        for name in ('propose_learning', 'propose_skill'):
            with self.subTest(tool=name):
                initial = self.proposal(name)
                model = self.model(tool_call('read_file', dict(file_path='/skills/builtin/learning/SKILL.md'), name + '-skill'),
                    tool_call(name, dict(value=initial), name + '-initial'), AIMessage(content='Draft ready for review.'))
                first = self.execute('Learn the reusable layout.')
                parent = self.tool_result(model, name + '-initial')
                self.reopen()
                corrected = dict(initial, parentId=parent['id'], instruction='Layout only; each order establishes its own commercial terms.')
                model = self.model(tool_call(name, dict(value=corrected), name + '-summary-correct'), AIMessage(content='Corrected draft; not approved.'))
                before = len(summaries)
                with patch('deepagents.middleware.summarization.SummarizationMiddleware', forced_summary):
                    self.execute('Correct the prior draft, without fixing currency or tax.', first['conversationId'])
                self.assertGreater(len(summaries), before)
                self.assertTrue(any('learning/SKILL.md' in str(m) for group in summaries[before:] for m in group))
                # The original skill read is no longer in the actual model context.
                self.assertFalse(any(isinstance(m, ToolMessage) and m.tool_call_id == name + '-skill' for m in model.seen[0]))
                for binding in model.bindings:
                    self.assertIn(name, binding)
                child = self.tool_result(model, name + '-summary-correct')
                self.assertEqual(child['parentId'], parent['id'])
                self.assertEqual(child['version'], 2)
                self.assertEqual(self.library(name).get(child['id'])['proposal']['instruction'], corrected['instruction'])
                self.assertEqual(self.library(name).search(status='approved'), [])
                self.assertFalse(any(call['name'] == 'read_file' for group in model.seen for m in group if isinstance(m, AIMessage) for call in m.tool_calls))
                with SqliteSaver.from_conn_string(str(self.directory / 'agent-checkpoints.sqlite')) as saver:
                    saved = saver.get_tuple({'configurable': {'thread_id': first['conversationId']}})
                    self.assertTrue(saved.checkpoint['channel_values'].get('_summarization_event'))

    def test_promoted_schema_keeps_parent_conflict_unknown_field_and_approval_guards(self):
        for name in ('propose_learning', 'propose_skill'):
            with self.subTest(tool=name):
                initial = self.proposal(name)
                model = self.model(tool_call(name, dict(value=initial), name + '-initial'), AIMessage(content='Draft created.'))
                first = self.execute('Save layout for review.')
                parent = self.tool_result(model, name + '-initial')
                for label, args in (
                    ('conflict', dict(value=dict(initial, parentId=parent['id']), parentId=str(uuid4()))),
                    ('unknown', dict(value=dict(initial, parentId=parent['id']), approved=True)),
                ):
                    call_id = name + '-' + label
                    model = self.model(tool_call(name, args, call_id), AIMessage(content='No valid change applied.'))
                    self.execute('Correct the draft.', first['conversationId'])
                    self.assert_available(model)
                    msg = next(m for m in model.seen[-1] if isinstance(m, ToolMessage) and m.tool_call_id == call_id)
                    self.assertEqual(msg.status, 'error', msg.content)
                    self.assertEqual(len(self.library(name).search(status='all')), 1)
                self.assertEqual(self.library(name).search(status='approved'), [])


if __name__ == '__main__':
    unittest.main(verbosity=2)
