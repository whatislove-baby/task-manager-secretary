"""Actual file/draft stores: recoverable cleanup without guessing business scope."""
from network_guard import no_external_network
from pathlib import Path
import json
import tempfile
import unittest
from unittest.mock import patch
from uuid import uuid4

from task_backend.agent_tools import AgentTools
from task_backend.deepagents_backend import ManagedFilesBackend
from task_backend.draft_lifecycle import DraftLifecycle
from task_backend.service import BackendService


class FileArchiveTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.service = BackendService(self.root, worker=True)
        self.ctx = AgentTools(self.service, 'archive-test', 'archive-conversation')
        self.addCleanup(self.tmp.cleanup)
        self.addCleanup(self.service.close)
        self.addCleanup(self.ctx.close)
        self.enterContext(no_external_network())

    def generated(self, name='old.txt', content='Older report', parents=None):
        return self.service.files._save(name, content.encode(), 'generated', parents=parents or [])

    def archive(self, ids, call='archive'):
        return self.ctx.archive_generated_files(ids, '用户明确要求只保留最后的交付版本', call_id=call)

    def test_pdf_separate_families_archive_old_560_keep_final_542_and_source(self):
        source = self.service.files._save('customer-request.txt', b'Customer Harbor H7314', 'local')
        old = self.ctx.create_document('Harbor560', 'pdf', 'Harbor / H7314', 'Total 560.00', [source['id']], call_id='old')
        new = self.ctx.create_document('Harbor542', 'pdf', 'Harbor / H7314', 'Total 542.00', [source['id']], call_id='new')
        old_bytes = self.service.files.bytes(old['id'])
        result = self.archive([old['id']])
        self.assertEqual(result['status'], 'archived')
        self.assertEqual({f['id'] for f in self.service.files.list_files()}, {source['id'], new['id']})
        self.assertEqual(self.service.files.bytes(old['id']), old_bytes)
        self.assertIn('560.00', self.ctx.read_file(old['id'])['text'])
        self.assertEqual(self.service.files.get(old['id'])['lifecycleStatus'], 'archived')
        self.assertEqual(self.ctx.search_files('560')['total'], 0)
        self.assertFalse(any(old['id'] in item['path'] for item in ManagedFilesBackend(self.service).ls('/').entries))
        self.assertTrue(any(new['id'] in item['path'] for item in ManagedFilesBackend(self.service).ls('/').entries))

    def test_word_duplicates_cleanup_is_explicit_not_shared_template_or_sender_guess(self):
        template = self.service.files._save('template.txt', b'Shared template', 'local')
        old = self.ctx.create_document('Harbor381old', 'docx', 'Harbor / 00381', 'Date November 20', [template['id']], call_id='word1')
        new = self.ctx.create_document('Harbor381new', 'docx', 'Harbor / 00381', 'Date November 28', [template['id']], call_id='word2')
        other_order = self.generated('Harbor396.txt', 'Harbor / 00396', [template['id']])
        other_customer = self.generated('Ridge381.txt', 'Ridge / 00381', [template['id']])
        self.archive([old['id']])
        self.assertEqual({f['id'] for f in self.service.files.list_files()},
                         {template['id'], new['id'], other_order['id'], other_customer['id']})
        self.assertEqual(self.service.files.get(old['id'])['supersededBy'], None)
        self.assertIn('November 28', self.service.files.read(new['id']))

    def test_source_protection_and_batch_validation_are_atomic(self):
        generated = self.generated()
        for source in ('local', 'mail', 'archive'):
            original = self.service.files._save(source + '.txt', b'Original reference', source)
            with self.subTest(source=source), self.assertRaisesRegex(ValueError, '原始'):
                self.archive([generated['id'], original['id']], call=source)
            self.assertEqual(self.service.files.get(generated['id']).get('lifecycleStatus', 'active'), 'active')
            self.assertEqual(self.service.files.bytes(original['id']), b'Original reference')

    def test_current_draft_must_be_replaced_before_archiving_then_attachment_stays_current(self):
        old = self.generated(); new = self.generated('new.txt', 'Updated report')
        draft = self.ctx.create_mail_draft('Report', 'Attached', to='client@example.invalid', attachment_ids=[old['id']], call_id='mail')
        with self.assertRaisesRegex(ValueError, draft['id']):
            self.archive([old['id']])
        current = self.ctx.replace_mail_draft(draft['id'], draft['revision'], 'Report', 'Updated attached', [new['id']], attachment_removal_reason='用户确认新版文件代替旧版', call_id='mail2')
        self.archive([old['id']])
        self.assertEqual(self.ctx.read_draft(current['id'])['attachmentIds'], [new['id']])
        with self.assertRaisesRegex(ValueError, '替代|撤回|归档'):
            self.ctx.create_mail_draft('Bad', 'Old attachment', attachment_ids=[old['id']], call_id='bad')

    def test_pending_legacy_draft_also_blocks_archive(self):
        old = self.generated()
        draft = self.ctx.create_mail_draft('Report', 'Attached', to='client@example.invalid', attachment_ids=[old['id']], call_id='legacy')
        with DraftLifecycle(self.root) as life:
            life.db.execute('DELETE FROM draft_versions WHERE id=?', (draft['id'],))
        with self.assertRaisesRegex(ValueError, draft['id']):
            self.archive([old['id']])

    def test_prepared_and_uncertain_receipts_block_even_without_active_draft(self):
        old = self.generated()
        receipt_id = str(uuid4())
        payload = dict(id=receipt_id, status='prepared', attachments=[old])
        self.service.compose.db.execute('INSERT INTO compose_receipts VALUES(?,?,?,?)', (receipt_id, json.dumps(payload), 'synthetic', '{}'))
        for status in ('prepared', 'sending', 'uncertain'):
            payload['status'] = status
            self.service.compose.db.execute('UPDATE compose_receipts SET payload=? WHERE id=?', (json.dumps(payload), receipt_id))
            with self.subTest(status=status), self.assertRaisesRegex(ValueError, receipt_id):
                self.archive([old['id']])
        payload['status'] = 'sent'
        self.service.compose.db.execute('UPDATE compose_receipts SET payload=? WHERE id=?', (json.dumps(payload), receipt_id))
        self.archive([old['id']])
        self.assertEqual(self.service.files.bytes(old['id']), b'Older report')

    def test_old_preview_of_superseded_draft_does_not_prevent_cleanup(self):
        old = self.generated(); new = self.generated('new.txt')
        draft = self.ctx.create_mail_draft('Report', 'Attached', to='client@example.invalid', attachment_ids=[old['id']], call_id='mail')
        receipt_id = str(uuid4())
        payload = dict(id=receipt_id, status='prepared', attachments=[old], originDraftId=draft['id'])
        self.service.compose.db.execute('INSERT INTO compose_receipts VALUES(?,?,?,?)', (receipt_id, json.dumps(payload), 'synthetic', '{}'))
        self.ctx.replace_mail_draft(draft['id'], draft['revision'], 'Report', 'Attached', [new['id']], attachment_removal_reason='用户确认新版文件代替旧版', call_id='mail2')
        self.archive([old['id']])
        self.assertEqual(self.service.files.get(old['id'])['lifecycleStatus'], 'archived')

    def test_archive_recovery_and_restore_keep_bytes_and_audit_after_tool_commit_gap(self):
        old = self.generated(); before = self.service.files.bytes(old['id'])
        result = self.archive([old['id']])
        self.ctx.db.execute('UPDATE agent_tool_results SET result=NULL WHERE id=?', (result['id'],))
        self.assertEqual(self.archive([old['id']]), result)
        restored = self.ctx.restore_generated_file(old['id'], 1, call_id='restore')
        self.assertEqual(restored['status'], 'restored')
        self.assertEqual(self.service.files.get(old['id'])['lifecycleStatus'], 'active')
        self.assertEqual(self.service.files.bytes(old['id']), before)
        self.ctx.db.execute('UPDATE agent_tool_results SET result=NULL WHERE id=?', (restored['id'],))
        self.assertEqual(self.ctx.restore_generated_file(old['id'], 1, call_id='restore'), restored)
        with DraftLifecycle(self.root) as life:
            self.assertEqual(life.db.execute('SELECT count(*) FROM file_archive_actions').fetchone()[0], 2)

    def test_wrong_revision_superseded_and_corrupt_files_do_not_archive_or_restore(self):
        old = self.generated()
        with self.assertRaisesRegex(ValueError, '版本'):
            self.ctx.archive_generated_files([old['id']], 'Explicit cleanup', {old['id']: 2}, call_id='stale')
        self.archive([old['id']])
        with self.assertRaisesRegex(ValueError, '版本'):
            self.ctx.restore_generated_file(old['id'], 2, call_id='bad-restore')
        self.ctx.restore_generated_file(old['id'], 1, call_id='restore')
        replacement = self.generated('replacement.txt')
        with DraftLifecycle(self.root) as life:
            with life.lock(old['id']):
                life.replace(old['id'], 1, replacement, 'file')
        with self.assertRaisesRegex(ValueError, '替代'):
            self.ctx.restore_generated_file(old['id'], 1, call_id='restore-superseded')
        historical=self.archive([old['id']], call='archive-superseded')
        self.assertEqual(historical['status'],'unchanged')
        self.assertEqual(historical['files'][0]['lifecycleStatus'],'superseded')
        (self.service.files.root / replacement['id']).write_bytes(b'bad')
        with self.assertRaisesRegex(ValueError, '变化'):
            self.archive([replacement['id']], call='corrupt')

    def test_competing_family_lock_blocks_archive_without_partial_write(self):
        first = self.generated(); second = self.generated('second.txt')
        with DraftLifecycle(self.root) as life:
            life.ensure(second, 'file')
            with life.lock(second['id']):
                with self.assertRaisesRegex(ValueError, '正在'):
                    self.archive([first['id'], second['id']])
        self.assertTrue(all(f.get('lifecycleStatus', 'active') == 'active' for f in self.service.files.list_files()))

    def test_batch_old_and_current_versions_of_same_family_only_archives_current(self):
        first = self.generated(); second = self.generated('second.txt')
        with DraftLifecycle(self.root) as life:
            life.ensure(first,'file')
            with life.lock(first['id']):life.replace(first['id'],1,second,'file')
        result=self.archive([first['id'],second['id']])
        self.assertEqual(result['archivedFileIds'],[second['id']])
        self.assertEqual(result['alreadyInactiveFileIds'],[first['id']])
        self.assertEqual([f['lifecycleStatus'] for f in result['files']],['superseded','archived'])
        self.assertEqual(self.service.files.list_files(),[])
        again=self.archive([first['id'],second['id']],call='another-cleanup')
        self.assertEqual(again['status'],'unchanged')
        self.assertEqual(again['archivedFileIds'],[])
        with self.assertRaisesRegex(ValueError,'替代'):self.ctx.restore_generated_file(first['id'],1,call_id='restore-old')
        self.ctx.restore_generated_file(second['id'],2,call_id='restore-current')
        self.assertEqual(self.service.files.get(second['id'])['lifecycleStatus'],'active')

    def test_reopened_store_retains_archival_and_restore_is_not_a_new_mail(self):
        old = self.generated()
        self.archive([old['id']])
        # A separate normal service instance sees persisted state and can
        # restore it; the original worker is not needed for the operation.
        reopened = BackendService(self.root, worker=True)
        tools = AgentTools(reopened, 'reopened-worker', 'another-chat')
        try:
            self.assertEqual(reopened.files.list_files(), [])
            self.assertEqual(reopened.files.read(old['id']), 'Older report')
            tools.restore_generated_file(old['id'], 1, call_id='reopen-restore')
            self.assertEqual([f['id'] for f in reopened.files.list_files()], [old['id']])
            self.assertEqual(tools.list_drafts()['total'], 0)
            self.assertEqual(reopened.compose.list_recent(), [])
        finally:
            tools.close(); reopened.close()


if __name__ == '__main__':
    unittest.main()
