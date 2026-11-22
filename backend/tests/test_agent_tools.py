"""Business tools use real temporary stores; no live model, mail or credentials."""
import asyncio
import json
import os
from pathlib import Path
import tempfile
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import patch
from uuid import NAMESPACE_URL, uuid5

from task_backend.agent_tools import AgentTools, build_tools
from task_backend.service import BackendService


class AgentToolsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.service = BackendService(self.root, worker=True)
        self.events = []
        self.ctx = AgentTools(self.service, 'fixture-execution', 'fixture-conversation', self.events.append)
        self.addCleanup(self.tmp.cleanup)
        self.addCleanup(self.service.close)
        self.addCleanup(lambda: self.ctx.close())
        self.enterContext(patch.dict(os.environ, {'LANGCHAIN_TRACING_V2': 'false', 'LANGSMITH_TRACING': 'false'}))
        self.enterContext(patch('socket.socket.connect', side_effect=AssertionError('Network forbidden')))

    def mail(self, body='Project 2 提交报告。', subject='课程通知', sender='teacher@example.invalid', attachments=None, remote='mail1', **extra):
        return self.service.mail.store.put('fixture-binding', 'fixture-identity', 'student@example.invalid',
            dict(id=remote, body=body, subject=subject, sender=sender, receivedAt='2026-11-20T10:00:00Z',
                 preview='', unread=True, webUrl='', attachments=attachments or [], **extra))['localId']

    def file(self, content='来源资料\n整理 Project 2 报告。', name='notes.txt'):
        path = self.root / name
        path.write_text(content, encoding='utf-8')
        return self.service.files.import_local(path)

    def reopen(self):
        self.ctx.close()
        self.ctx = AgentTools(self.service, 'fixture-execution', 'fixture-conversation', self.events.append)

    def test_full_body_search_sender_pagination_and_literal_sql_chars(self):
        wanted = self.mail(body='Please prepare the 5051 Project 2 slides.', remote='target')
        self.mail(body='Project 2 slides for 5051.', remote='target2')
        self.mail(body='Project 2 for another course.', remote='wrong-course')
        page = self.ctx.search_mail('5051 slides', 'teacher@example.invalid', limit=1)
        self.assertEqual(page['total'], 2)
        self.assertEqual(page['nextOffset'], 1)
        second = self.ctx.search_mail('5051 slides', 'teacher@example.invalid', offset=1, limit=1)
        self.assertIsNone(second['nextOffset'])
        self.assertIn(wanted, [p['id'] for p in page['items'] + second['items']])
        self.assertEqual(self.ctx.search_mail("' OR 1=1 --")['total'], 0)
        self.assertEqual(self.ctx.search_mail('%')['total'], 0)
        self.assertIn('未连接服务器', page['scope'])
        self.assertTrue(any(e['type'] == 'source' for e in self.events))

    def test_paginated_mail_read_reports_incomplete_sources_and_attachment_indexes(self):
        mid = self.mail(body='abcdef', bodyStatus='partial', attachments=[dict(name='guide.pdf', size=23, type='application/pdf')])
        first = self.ctx.read_mail(mid, limit=3)
        self.assertEqual(first['text'], 'abc')
        self.assertEqual(first['nextOffset'], 3)
        self.assertEqual(first['attachments'][0]['index'], 0)
        self.assertFalse(first['complete'])
        last = self.ctx.read_mail(mid, offset=3, limit=3)
        self.assertEqual(last['text'], 'def')
        self.assertFalse(last['complete'])
        self.assertIn('不完整', last['warning'])
        with self.assertRaises(ValueError):
            self.ctx.read_mail(mid, offset=-1)

    def test_file_search_goes_beyond_old_hundred_item_list_and_validates_copies(self):
        first = self.file(content='only-this-one contains rubric-marker')
        for i in range(101):
            self.service.files.create_document('generated', 'txt', 'title', 'filler ' + str(i), [])
        self.assertNotIn(first['id'], [f['id'] for f in self.service.files.list_files()])
        found = self.ctx.search_files('rubric-marker')
        self.assertEqual(found['total'], 1)
        self.assertEqual(found['items'][0]['id'], first['id'])
        result = self.ctx.read_file(first['id'], limit=7)
        self.assertEqual(result['text'], 'only-th')
        self.assertEqual(result['nextOffset'], 7)
        self.assertNotIn(str(self.root), json.dumps(result))
        (self.service.files.root / first['id']).write_text('tampered', encoding='utf-8')
        damaged = self.ctx.search_files('rubric-marker')['items'][0]
        self.assertIn('error', damaged)
        self.assertNotIn('excerpt', damaged)
        with self.assertRaises(ValueError):
            self.ctx.read_file(first['id'])
        with self.assertRaises(ValueError):
            self.ctx.read_file('/etc/passwd')

    def test_tasks_reflect_latest_completion_and_revision(self):
        task = self.service.repo.call('createTodo', dict(title='Project 2 slides', notes='5051'))['value']
        self.assertEqual(self.ctx.list_tasks('5051', False)['total'], 1)
        self.service.repo.call('setCompleted', task['id'], True, task['revision'])
        self.assertEqual(self.ctx.list_tasks('5051', False)['total'], 0)
        current = self.ctx.list_tasks('5051', True)['items'][0]
        self.assertTrue(current['completed'])
        self.assertGreater(current['revision'], task['revision'])

    def test_file_generation_is_idempotent_and_commit_before_journal_recovers(self):
        original = self.file()
        args = dict(name='revision', format='md', title='修订', content='新的报告内容', parent_ids=[original['id']])
        first = self.ctx.create_document(**args, call_id='document-1')
        self.ctx.db.execute('UPDATE agent_tool_results SET result=NULL WHERE id=?', (first['id'],))
        self.reopen()
        repeated = self.ctx.create_document(**args, call_id='document-1')
        self.assertEqual(first, repeated)
        self.assertEqual(len(self.service.files.list_files()), 2)
        self.assertEqual(self.service.files.get(original['id'])['sha256'], original['sha256'])
        self.assertEqual(sum(e.get('type') == 'artifact' for e in self.events), 2)
        with self.assertRaisesRegex(ValueError, '其他内容'):
            self.ctx.create_document(**{**args, 'content': 'different'}, call_id='document-1')

    def test_markdown_title_is_inserted_once_without_removing_body_headings(self):
        args = dict(name='checklist', format='md', title='检查清单',
                    content='\n# 检查清单\n\n# 检查清单\n\n正文\n\n## 检查清单\n细节\n\n# 检查清单\n附录')
        result = self.ctx.create_document(**args, call_id='title-1')
        expected = '# 检查清单\n\n正文\n\n## 检查清单\n细节\n\n# 检查清单\n附录\n'
        self.assertEqual(self.service.files.read(result['id']), expected)
        self.ctx.db.execute('UPDATE agent_tool_results SET result=NULL WHERE id=?', (result['id'],))
        self.reopen()
        self.assertEqual(self.ctx.create_document(**args, call_id='title-1'), result)
        self.assertEqual(len(self.service.files.list_files()), 1)
        other = self.ctx.create_document(name='different', format='md', title='报告',
                                         content='# 子主题\n正文', call_id='title-2')
        self.assertEqual(self.service.files.read(other['id']), '# 报告\n\n# 子主题\n正文\n')

    def test_task_draft_needs_review_and_quote_matches_every_cited_mail(self):
        mid = self.mail()
        other = self.mail(body='Unrelated notice', remote='other')
        with self.assertRaisesRegex(ValueError, '逐字引用'):
            self.ctx.create_task_draft('报告', source_mail_ids=[mid, other], source_quote='提交报告', call_id='invalid')
        args = dict(title='Project 2 报告', due_date='2026-11-23', source_mail_ids=[mid], source_quote='提交报告')
        first = self.ctx.create_task_draft(**args, call_id='task-1')
        self.assertEqual(self.service.repo.call('listTodos'), [])
        self.assertEqual(self.service.mail.store.get_local(mid)['status'], 'unanalyzed')
        # Replay after the real analysis commit but before the tool checkpoint.
        self.ctx.db.execute('UPDATE agent_tool_results SET result=NULL WHERE id=?', (first['id'],))
        self.reopen()
        self.assertEqual(first, self.ctx.create_task_draft(**args, call_id='task-1'))
        analysis = self.service.mail.store.get_analysis(first['id'])
        self.assertEqual(analysis['drafts'][0]['sourceMailIds'], [mid])
        self.assertEqual(self.service.mail.save_analysis(first['id'], analysis['drafts'])['count'], 1)
        self.assertEqual(self.service.mail.save_analysis(first['id'], analysis['drafts'])['count'], 0)
        saved = self.service.repo.call('listTodos')
        self.assertEqual(len(saved), 1)
        self.assertIn('本地邮件：' + mid, saved[0]['notes'])
        self.assertIn('原文依据：提交报告', saved[0]['notes'])
        with self.assertRaises(ValueError):
            self.ctx.create_task_draft('bad date', due_date='2026-02-30', call_id='invalid-date')

    def test_mail_draft_is_persistent_editable_and_cannot_send(self):
        attachment = self.file()
        self.service.compose.prepare = lambda *args: self.fail('Draft must not prepare SMTP receipt')
        self.service.compose.send = lambda *args: self.fail('Draft must not send')
        draft = self.ctx.create_mail_draft('关于项目', '请查收报告。', attachment_ids=[attachment['id']], call_id='mail-1')
        self.assertEqual(draft['to'], '')
        self.assertEqual(draft['status'], 'draft')
        self.assertEqual(draft['attachmentIds'], [attachment['id']])
        self.reopen()
        self.assertEqual(self.ctx.get_mail_draft(draft['id']), draft)
        self.assertEqual(self.ctx.list_mail_drafts(), [draft])
        again = self.ctx.create_mail_draft('关于项目', '请查收报告。', attachment_ids=[attachment['id']], call_id='mail-1')
        self.assertEqual(draft, again)
        with self.assertRaises(ValueError):
            self.ctx.create_mail_draft('x\nBcc: evil', 'body', call_id='bad-header')
        with self.assertRaises(ValueError):
            self.ctx.create_mail_draft('x', 'body', to='a@example.invalid,b@example.invalid', call_id='bad-to')
        self.assertEqual(self.service.compose.list_recent(), [])

    def test_attachment_import_checks_identity_and_recovers_without_duplicate_download(self):
        mid = self.mail(attachments=[dict(name='brief.txt', type='text/plain', size=10)])
        downloads = []
        reader = SimpleNamespace(get_attachment=lambda *args: (downloads.append(args) or '附件要求'.encode()))
        manager = self.service.mail.manager
        manager.check_identity = lambda *args: None
        manager.reader = lambda *args: reader
        first = self.ctx.import_attachment(mid, 0, call_id='attachment-1')
        self.ctx.db.execute('UPDATE agent_tool_results SET result=NULL WHERE id=?', (first['file']['id'],))
        self.reopen()
        repeated = self.ctx.import_attachment(mid, 0, call_id='attachment-1')
        self.assertEqual(first, repeated)
        self.assertEqual(len(downloads), 1)
        self.assertEqual(first['file']['sourceMailId'], mid)
        self.assertIn('附件要求', self.ctx.read_file(first['file']['id'])['text'])
        manager.check_identity = lambda *args: (_ for _ in ()).throw(ValueError('邮箱身份发生变化'))
        with self.assertRaisesRegex(ValueError, '身份'):
            self.ctx.import_attachment(mid, 0, call_id='attachment-2')

    def test_user_resolution_requires_actual_sent_receipt_or_explicit_dismissal(self):
        draft = self.ctx.create_mail_draft('待回复', '正文', call_id='resolve-draft')
        self.assertFalse(draft['resolved'])
        receipt_id = str(uuid5(NAMESPACE_URL, 'synthetic-receipt'))
        receipt = dict(id=receipt_id, status='uncertain',originDraftId=draft['id'],expectedRevision=draft['revision'])
        self.service.compose.db.execute('INSERT INTO compose_receipts VALUES(?,?,?,?)',
                                       (receipt_id, json.dumps(receipt), 'synthetic-identity', '{}'))
        with self.assertRaisesRegex(ValueError, '未知'):
            self.ctx.resolve_mail_draft(draft['id'], receipt_id)
        self.assertFalse(self.ctx.get_mail_draft(draft['id'])['resolved'])
        receipt['status'] = 'sent'
        self.service.compose.save_receipt(receipt)
        resolved = self.ctx.resolve_mail_draft(draft['id'], receipt_id)
        self.assertTrue(resolved['resolved'])
        self.assertEqual(resolved['resolution'], 'sent')
        self.assertEqual(resolved['receiptId'], receipt_id)
        self.assertEqual(self.ctx.resolve_mail_draft(draft['id'], receipt_id), resolved)
        self.reopen()
        self.assertTrue(self.ctx.get_mail_draft(draft['id'])['resolved'])
        immutable = json.loads(self.ctx.db.execute('SELECT result FROM agent_tool_results WHERE id=?', (draft['id'],)).fetchone()[0])
        self.assertNotIn('resolved', immutable)
        self.assertEqual(immutable['status'], 'draft')
        dismissed = self.ctx.create_mail_draft('忽略', '无需处理', call_id='dismiss-draft')
        value = self.ctx.resolve_mail_draft(dismissed['id'], dismiss=True)
        self.assertEqual(value['resolution'], 'dismissed')
        self.assertNotIn('receiptId', value)
        with self.assertRaises(ValueError):
            self.ctx.resolve_mail_draft(dismissed['id'], receipt_id, dismiss=True)
        self.assertNotIn('resolve_mail_draft', [tool.name for tool in build_tools(self.ctx)])
        with self.assertRaisesRegex(ValueError, '未知'):
            self.ctx.execute('resolve_mail_draft', dict(id=draft['id'], dismiss=True), 'model-disallowed')

    def test_attachment_orphan_recovery_does_not_follow_symlink(self):
        mid = self.mail(attachments=[dict(name='brief.txt', type='text/plain', size=10)])
        self.service.mail.manager.check_identity = lambda *args: None
        self.service.mail.manager.reader = lambda *args: SimpleNamespace(get_attachment=lambda *a: b'new valid text')
        id = str(uuid5(NAMESPACE_URL, 'task-manager-agent:fixture-execution:orphan'))
        (self.service.files.root / id).write_bytes(b'partial orphan')
        self.ctx.import_attachment(mid, 0, call_id='orphan')
        self.assertEqual(self.service.files.read(id), 'new valid text')
        second_id = str(uuid5(NAMESPACE_URL, 'task-manager-agent:fixture-execution:link'))
        target = self.root / 'outside.txt'
        target.write_text('preserve', encoding='utf-8')
        (self.service.files.root / second_id).symlink_to(target)
        with self.assertRaisesRegex(ValueError, '路径异常'):
            self.ctx.import_attachment(mid, 0, call_id='link')
        self.assertEqual(target.read_text(), 'preserve')

    def test_errors_are_visible_and_control_stop_propagates(self):
        result = self.ctx.execute('read_file', dict(file_id='/etc/passwd'), 'bad')
        self.assertEqual(result['error'], 'tool_failed')
        self.assertEqual([e['status'] for e in self.events], ['running', 'failed'])
        class Stopped(Exception):
            control_stop = True
        self.ctx.read_file = lambda **kwargs: (_ for _ in ()).throw(Stopped())
        with self.assertRaises(Stopped):
            self.ctx.execute('read_file', dict(file_id='x'), 'stopped')

    def test_account_listing_redacts_connection_details(self):
        self.service.mail.manager.all = lambda: [dict(id='binding', method='imap', state=dict(
            account=dict(email='student@example.invalid', name='Student'),
            imap=dict(password='private-marker', host='internal.private', username='internal-login'), clientId='secret-marker'))]
        result = self.ctx.list_accounts()
        self.assertEqual(result['items'][0]['email'], 'student@example.invalid')
        self.assertNotIn('private', json.dumps(result))
        self.assertNotIn('secret', json.dumps(result))

    def test_real_framework_async_tools_use_worker_thread_and_hide_runtime_arguments(self):
        from langchain.agents import create_agent
        from langchain_core.language_models.chat_models import BaseChatModel
        from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
        from langchain_core.outputs import ChatGeneration, ChatResult
        from pydantic import Field

        class Model(BaseChatModel):
            answers: list = Field(default_factory=list)
            @property
            def _llm_type(self): return 'fixture'
            def bind_tools(self, tools, **kwargs): return self
            def _generate(self, messages, **kwargs):
                return ChatResult(generations=[ChatGeneration(message=self.answers.pop(0))])
            async def _agenerate(self, messages, stop=None, run_manager=None, **kwargs):
                return self._generate(messages, **kwargs)

        owner = threading.get_ident()
        observed = []
        original = self.ctx.search_mail
        def search(**kwargs):
            observed.append(threading.get_ident())
            return original(**kwargs)
        self.ctx.search_mail = search
        self.mail()
        definitions = build_tools(self.ctx)
        names = [t.name for t in definitions]
        self.assertNotIn('send', names)
        self.assertNotIn('delete', names)
        for definition in definitions:
            self.assertNotIn('runtime', definition.tool_call_schema.model_fields)
            self.assertNotIn('call_id', definition.tool_call_schema.model_fields)
        model = Model(answers=[
            AIMessage(content='', tool_calls=[dict(name='search_mail', args=dict(query='Project'), id='search-1', type='tool_call')]),
            AIMessage(content='已找到 Project 2 的通知。')])
        result = asyncio.run(create_agent(model, definitions).ainvoke(dict(messages=[HumanMessage(content='找 Project 邮件')])))
        self.assertEqual(observed, [owner])
        reply = next(m for m in result['messages'] if isinstance(m, ToolMessage))
        self.assertEqual(json.loads(reply.content)['total'], 1)


    def test_generated_but_never_attached_is_not_complete_delivery(self):
        file=self.ctx.create_document('confirmation','md','Order 00381','100 pieces',call_id='output')
        draft=self.ctx.create_mail_draft('Order 00381','Please find confirmation attached.',call_id='missing')
        check=self.ctx.validate_mail_draft(draft['id'],[file['id']])
        self.assertFalse(check['passed'])
        self.assertEqual(check['missingAttachmentIds'],[file['id']])
        self.assertEqual(check['actualAttachmentIds'],[])
        # The validator never guesses or attaches something to a recipient.
        self.assertEqual(self.ctx.read_draft(draft['id'])['attachmentIds'],[])
        self.assertFalse(self.ctx.validate_mail_draft(draft['id'])['expectationsProvided'])
        fixed=self.ctx.replace_mail_draft(draft['id'],1,draft['subject'],draft['body'],
                                           attachment_ids=[file['id']],call_id='attach')
        self.assertTrue(self.ctx.validate_mail_draft(fixed['id'],[file['id']])['passed'])
        self.assertFalse(self.ctx.validate_mail_draft(draft['id'],[])['passed'])
        plain=self.ctx.create_mail_draft('Hello','Thank you.',call_id='plain')
        self.assertTrue(self.ctx.validate_mail_draft(plain['id'],[])['passed'])

    def test_body_edits_keep_files_and_empty_attachment_list_needs_intent(self):
        file=self.file();draft=self.ctx.create_mail_draft('Order','body',attachment_ids=[file['id']],call_id='attached')
        updated=self.ctx.replace_mail_draft(draft['id'],1,'Order','corrected body',call_id='body')
        self.assertEqual(updated['attachmentIds'],[file['id']])
        with self.assertRaisesRegex(ValueError,'attachment_removal_reason'):
            self.ctx.replace_mail_draft(updated['id'],2,'Order','oops',attachment_ids=[],call_id='accidental-empty')
        self.assertEqual(self.ctx.read_draft(updated['id'])['lifecycleStatus'],'active')
        removed=self.ctx.replace_mail_draft(updated['id'],2,'Order','body only',attachment_ids=[],
                  attachment_removal_reason='User requested a body-only reply.',call_id='intentional-empty')
        self.assertEqual(removed['attachmentIds'],[])
        self.assertIn('body-only',removed['attachmentRemovalReason'])

    def test_delivery_validator_detects_tampered_and_archived_attachment(self):
        from task_backend.draft_lifecycle import DraftLifecycle
        file=self.ctx.create_document('order','md','Order','80 pieces',call_id='file')
        draft=self.ctx.create_mail_draft('Order','body',attachment_ids=[file['id']],call_id='draft')
        with DraftLifecycle(self.root) as life:
            life.ensure(self.service.files.get(file['id']),'file')
            life.set_status(file['id'],'archived')
        check=self.ctx.validate_mail_draft(draft['id'],[file['id']])
        self.assertFalse(check['passed']);self.assertEqual(check['invalidAttachments'][0]['id'],file['id'])
        with DraftLifecycle(self.root) as life:life.set_status(file['id'],'active')
        (self.service.files.root/file['id']).write_bytes(b'corrupted')
        self.assertFalse(self.ctx.validate_mail_draft(draft['id'],[file['id']])['passed'])

    def test_read_draft_tool_events_use_real_distinct_call_ids(self):
        draft=self.ctx.create_mail_draft('Order','body',call_id='draft')
        definitions={t.name:t for t in build_tools(self.ctx)}
        asyncio.run(definitions['list_drafts'].coroutine(runtime=SimpleNamespace(tool_call_id='list-1')))
        asyncio.run(definitions['read_draft'].coroutine(draft['id'],runtime=SimpleNamespace(tool_call_id='read-2')))
        asyncio.run(definitions['validate_mail_draft'].coroutine(draft['id'],runtime=SimpleNamespace(tool_call_id='check-3'),expected_attachment_ids=[]))
        calls={event['callId'] for event in self.events if event.get('type')=='tool'}
        self.assertEqual(calls,{'list-1','read-2','check-3'})


if __name__ == '__main__':
    unittest.main()
