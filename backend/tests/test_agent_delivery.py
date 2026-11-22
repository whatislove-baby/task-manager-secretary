"""Actual persisted delivery checks and official graph corrective routing."""
from network_guard import no_external_network
import asyncio
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from langchain_core.messages import AIMessage, HumanMessage
from task_backend.agent_delivery import delivery_snapshot
from task_backend.agent_tools import AgentTools
from task_backend.service import BackendService
from test_agent_runtime import CONFIG, ScriptedModel, tool_call


class DeliveryReviewTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.service = BackendService(Path(self.tmp.name), worker=True)
        self.addCleanup(self.service.close)
        self.service.settings.for_request = lambda: dict(CONFIG)
        self.service.agent.schedule = lambda: None
        self.enterContext(patch.dict(os.environ, {'LANGSMITH_TRACING': 'false', 'LANGCHAIN_TRACING_V2': 'false'}))
        self.enterContext(no_external_network())
        self.run = self.service.agent.call('startTurn', {'text': '修改这份报告并附入回复草稿，先不发送。'})
        self.ctx = self.context(self.run['id'], self.run['conversationId'])

    def context(self, run, conversation):
        ctx = AgentTools(self.service, run, conversation)
        self.addCleanup(ctx.close)
        return ctx

    def file(self, ctx=None):
        return (ctx or self.ctx).create_document('report.md', 'md', 'Report', 'Current values.', call_id='file')['id']

    def draft(self, ids=None, ctx=None):
        return (ctx or self.ctx).create_mail_draft('Report', 'Please see the report.',
            to='client@example.invalid', attachment_ids=ids, call_id='draft')

    def execute(self, *script):
        model = ScriptedModel(script=list(script))
        self.service.agent.model_factory = lambda config, reasoning: model
        asyncio.run(self.service.agent.run_next())
        result = self.service.agent.call('getRun', self.run['id'])
        self.assertEqual(result['status'], 'completed', result.get('error'))
        return result, model

    def test_file_only_and_other_conversation_are_not_missing_mail(self):
        self.file()
        self.assertIsNone(delivery_snapshot(self.ctx))
        other = self.context('elsewhere', 'different-conversation')
        self.draft(ctx=other)
        self.assertIsNone(delivery_snapshot(self.ctx))

    def test_unlinked_is_only_candidate_and_no_auto_attachment(self):
        fid = self.file(); draft = self.draft()
        snapshot = delivery_snapshot(self.ctx)
        self.assertEqual([f['id'] for f in snapshot['unattachedGeneratedFiles']], [fid])
        self.assertEqual(snapshot['invalidDrafts'], [])
        self.assertEqual(self.ctx.read_draft(draft['id'])['attachmentIds'], [])

    def test_official_after_model_routes_back_and_model_really_attaches(self):
        fid = self.file(); draft = self.draft()
        result, model = self.execute(AIMessage(content='已完成，附件也准备好了。'),
            tool_call('replace_mail_draft', dict(draft_id=draft['id'], expected_revision=1,
                subject=draft['subject'], body=draft['body'], attachment_ids=[fid]), 'fix-attachment'), AIMessage(content='已实际附入报告，待审核。'))
        active = self.ctx.list_drafts(kind='mail_draft')['items']
        self.assertEqual(len(active), 1)
        current = self.ctx.read_draft(active[0]['id'])
        self.assertEqual(current['attachmentIds'], [fid])
        self.assertEqual(self.ctx.read_draft(draft['id'])['lifecycleStatus'], 'superseded')
        self.assertEqual(result['partialText'], '已实际附入报告，待审核。')
        self.assertTrue(any(isinstance(m, HumanMessage) and m.id.endswith(':delivery-review')
                            for m in model.seen[1] if m.id))
        self.assertFalse(any(isinstance(m, AIMessage) and m.content == '已完成，附件也准备好了。'
                             for m in model.seen[1]))
        self.assertIsNone(delivery_snapshot(self.ctx))

    def test_model_ignoring_review_is_bounded_and_limitation_is_visible(self):
        fid = self.file(); draft = self.draft()
        result, model = self.execute(AIMessage(content='都完成了。'), AIMessage(content='都完成了。'))
        self.assertEqual(len(model.seen), 2)
        self.assertIn('尚未附到', result['partialText'])
        self.assertIn(fid, result['partialText'])
        self.assertEqual(self.ctx.read_draft(draft['id'])['attachmentIds'], [])
        saved = self.service.agent.call('getConversation', self.run['conversationId'])['turns'][-1]['content']
        self.assertEqual(saved, result['partialText'])

    def test_archive_disappears_from_candidate_check_but_history_preserved(self):
        fid = self.file(); self.draft()
        self.ctx.archive_generated_files([fid], 'obsolete work copy', call_id='archive')
        self.assertIsNone(delivery_snapshot(self.ctx))
        self.assertEqual(self.service.files.get(fid)['lifecycleStatus'], 'archived')
        self.assertIn('Current values.', self.service.files.read(fid))
