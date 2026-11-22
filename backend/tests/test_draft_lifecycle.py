"""Lifecycle regression: actual temporary databases, synthetic SMTP only."""
from copy import deepcopy
from unittest.mock import patch
import json
import threading
from uuid import uuid4
from task_backend.agent_tools import AgentTools
from task_backend.draft_lifecycle import DraftLifecycle
from task_backend.service import BackendService
try:
    from .test_mail_compose import MailComposeTests,FakeSMTP
except ImportError:
    from test_mail_compose import MailComposeTests,FakeSMTP


class DraftLifecycleTests(MailComposeTests):
    def setUp(self):
        super().setUp()
        self.ctx=AgentTools(self.service,'lifecycle-tests','conversation')
        self.addCleanup(self.ctx.close)
    def draft(self,call='initial',body='最初数量80件'):
        return self.ctx.create_mail_draft('订单确认',body,'customer@example.invalid',self.binding,call_id=call)
    def change(self,old,call='revision',body='最新数量100件'):
        return self.ctx.replace_mail_draft(old['id'],old['revision'],'订单确认',body,call_id=call)
    def preview(self,draft):
        return self.prepare(to=draft['to'],subject=draft['subject'],body=draft['body'],originDraftId=draft['id'],expectedRevision=draft['revision'])

    def test_replace_retains_old_payload_but_only_one_current_pending(self):
        old=self.draft();new=self.change(old)
        self.assertEqual(new['revision'],2);self.assertEqual(new['familyId'],old['id'])
        historical=self.ctx.read_draft(old['id'])
        self.assertEqual(historical['body'],'最初数量80件')
        self.assertEqual(historical['lifecycleStatus'],'superseded')
        self.assertEqual(historical['supersededBy'],new['id'])
        self.assertEqual([x['id'] for x in self.ctx.list_drafts()['items']],[new['id']])
        self.assertEqual(self.change(old)['id'],new['id'])  # durable call replay
        with self.assertRaisesRegex(ValueError,'替代'):
            self.change(old,call='stale-concurrent')
        self.assertEqual(len(self.ctx.list_drafts(include_history=True)['items']),2)

    def test_old_preview_and_old_editor_cannot_send_after_replacement(self):
        old=self.draft();receipt=self.preview(old);new=self.change(old)
        with self.assertRaisesRegex(ValueError,'替代'):self.preview(old)
        failed=self.compose.send(receipt['id'])
        self.assertEqual(failed['status'],'failed');self.assertEqual(self.smtp.events,[])
        current=self.preview(new);sent=self.compose.send(current['id'])
        self.assertEqual(sent['status'],'sent');self.assertEqual(len(self.smtp.payloads),1)
        with self.assertRaisesRegex(ValueError,'替代|发送'):self.change(new,call='after-send')

    def test_second_prepared_snapshot_cannot_send_same_draft_twice(self):
        draft=self.draft();a=self.preview(draft);b=self.preview(draft)
        self.assertEqual(self.compose.send(a['id'])['status'],'sent')
        self.assertEqual(self.compose.send(b['id'])['status'],'failed')
        self.assertEqual(len(self.smtp.payloads),1)

    def test_sent_receipt_must_belong_to_this_draft(self):
        a=self.draft();b=self.draft('another','另一笔订单')
        sent=self.compose.send(self.preview(a)['id'])
        with self.assertRaisesRegex(ValueError,'不属于'):self.ctx.resolve_mail_draft(b['id'],sent['id'])
        self.assertEqual(self.ctx.resolve_mail_draft(a['id'],sent['id'])['resolution'],'sent')

    def test_pending_task_replacement_blocks_old_save_and_preserves_evidence(self):
        old=self.ctx.create_task_draft('问仓库两行都是2号',call_id='task-old')
        new=self.ctx.replace_task_draft(old['id'],old['revision'],'问仓库第一行2号第二行5号',call_id='task-new')
        self.assertEqual(len(self.ctx.list_tasks()['pendingDrafts']),1)
        with self.assertRaisesRegex(ValueError,'替代'):
            self.service.mail.save_analysis(old['id'],self.service.mail.store.get_analysis(old['id'])['drafts'])
        self.assertEqual(self.service.repo.call('listTodos'),[])
        saved=self.service.mail.save_analysis(new['id'],self.service.mail.store.get_analysis(new['id'])['drafts'])
        self.assertEqual(saved['count'],1)
        self.assertEqual(self.ctx.list_tasks()['pendingDrafts'],[])
        with self.assertRaisesRegex(ValueError,'替代|保存'):
            self.ctx.replace_task_draft(new['id'],new['revision'],'不应新增重复任务',call_id='after-save')
        self.assertEqual(len(self.service.repo.call('listTodos')),1)

    def test_local_write_commit_before_tool_journal_replay_recovers_same_revision(self):
        old=self.draft();new=self.change(old)
        self.ctx.db.execute('UPDATE agent_tool_results SET result=NULL WHERE id=?',(new['id'],))
        recovered=self.change(old)
        self.assertEqual(recovered['id'],new['id'])
        self.assertEqual(len(self.ctx.list_drafts(include_history=True)['items']),2)

    def test_failure_persisting_new_payload_does_not_retire_old(self):
        old=self.ctx.create_task_draft('原待办',call_id='original-task')
        with patch.object(self.service.mail.store,'save_analyses',side_effect=OSError('disk full')):
            with self.assertRaises(OSError):
                self.ctx.replace_task_draft(old['id'],1,'新待办',call_id='retry-task')
        self.assertEqual(self.ctx.read_draft(old['id'])['lifecycleStatus'],'active')
        result=self.ctx.replace_task_draft(old['id'],1,'新待办',call_id='retry-task')
        self.assertEqual(result['revision'],2)

    def test_family_lock_prevents_concurrent_replacement(self):
        old=self.draft()
        with DraftLifecycle(self.directory) as life:
            with life.lock(old['id']):
                with self.assertRaisesRegex(ValueError,'正在'):
                    self.change(old)
        self.assertEqual(self.ctx.read_draft(old['id'])['lifecycleStatus'],'active')

    def test_discard_actual_pending_state_and_replay(self):
        old=self.draft();receipt=self.preview(old)
        outcome=self.ctx.discard_draft(old['id'],1,call_id='discard')
        self.assertEqual(self.ctx.list_drafts()['items'],[])
        self.assertEqual(self.ctx.discard_draft(old['id'],1,call_id='discard'),outcome)
        self.assertEqual(self.compose.send(receipt['id'])['status'],'failed')
        self.assertEqual(self.smtp.events,[])

    def test_customer_and_account_do_not_change_in_revision(self):
        def mail(remote,sender):
            return self.service.mail.store.put(self.binding,'fixture',self.imap['email'],dict(id=remote,subject='00396',sender=sender,body='same name different customer',preview='',receivedAt='2026-11-20T10:00:00Z',unread=True,webUrl='',attachments=[]))['localId']
        a=mail('a','harbor@example.invalid');b=mail('b','ridge@example.invalid')
        old=self.ctx.create_mail_draft('00396','Harbor',reply_to_local_id=a,call_id='harbor')
        with self.assertRaisesRegex(ValueError,'客户'):
            self.ctx.replace_mail_draft(old['id'],1,'00396','Ridge',reply_to_local_id=b,call_id='wrong-customer')
        self.assertEqual(self.ctx.read_draft(old['id'])['lifecycleStatus'],'active')

    def test_file_revision_invalidates_prepared_receipt_and_old_attachments(self):
        old=self.service.files.create_document('quote','txt','Quote','80 pcs',[])
        with DraftLifecycle(self.directory) as life:life.ensure(old,'file')
        receipt=self.prepare(attachmentIds=[old['id']])
        new=self.service.files.create_document('quote','txt','Quote','100 pcs',[old['id']])
        with DraftLifecycle(self.directory) as life:
            with life.lock(old['id']):life.replace(old['id'],1,new,'file')
        self.assertEqual(self.compose.send(receipt['id'])['status'],'failed')
        self.assertEqual(self.smtp.events,[])
        with self.assertRaisesRegex(ValueError,'替代'):self.prepare(attachmentIds=[old['id']])
        with self.assertRaisesRegex(ValueError,'替代'):
            self.ctx.create_mail_draft('old file','body',attachment_ids=[old['id']],call_id='old-file')

    def test_manually_edited_preview_is_bound_to_origin_but_keeps_visible_content(self):
        old=self.draft()
        receipt=self.prepare(to=old['to'],subject=old['subject'],body='用户在编辑器确认的改稿',originDraftId=old['id'],expectedRevision=1)
        result=self.compose.send(receipt['id'])
        self.assertEqual(result['body'],'用户在编辑器确认的改稿')
        self.assertEqual(result['originDraftId'],old['id'])
        self.assertEqual(self.ctx.read_draft(old['id'])['body'],old['body'])

    def test_attachment_archived_after_precheck_cannot_become_current_draft(self):
        file=self.service.files.create_document('old','txt','Old','old output',[])
        original=DraftLifecycle.ensure
        def race(life,payload,kind):
            if kind=='mail_draft':
                with DraftLifecycle(self.directory) as other:
                    original(other,file,'file');other.set_status(file['id'],'archived')
            return original(life,payload,kind)
        with patch.object(DraftLifecycle,'ensure',race):
            with self.assertRaisesRegex(ValueError,'归档'):
                self.ctx.create_mail_draft('Order','Attached',attachment_ids=[file['id']],call_id='raced-draft')
        self.assertEqual(self.ctx.list_drafts()['items'],[])

    def test_replacement_transaction_rechecks_archival_after_prevalidation(self):
        draft=self.draft()
        file=self.service.files.create_document('new','txt','New','new output',[])
        original=DraftLifecycle.replace
        def race(life,old_id,revision,payload,kind):
            with DraftLifecycle(self.directory) as other:
                other.ensure(file,'file');other.set_status(file['id'],'archived')
            return original(life,old_id,revision,payload,kind)
        with patch.object(DraftLifecycle,'replace',race):
            with self.assertRaisesRegex(ValueError,'归档'):
                self.ctx.replace_mail_draft(draft['id'],1,'Order','Attached',attachment_ids=[file['id']],call_id='raced-revision')
        self.assertEqual(self.ctx.read_draft(draft['id'])['lifecycleStatus'],'active')

    def test_same_family_current_file_replaces_old_attachment_without_drop_reason(self):
        old=self.service.files.create_document('quote','txt','Quote','80 pcs',[])
        draft=self.ctx.create_mail_draft('Order','Attached',attachment_ids=[old['id']],call_id='old-attached')
        new=self.service.files.create_document('quote','txt','Quote','100 pcs',[old['id']])
        with DraftLifecycle(self.directory) as life:
            life.ensure(old,'file')
            with life.lock(old['id']):life.replace(old['id'],1,new,'file')
        changed=self.ctx.replace_mail_draft(draft['id'],1,'Order','Updated',attachment_ids=[new['id']],call_id='new-attached')
        self.assertTrue(self.ctx.validate_mail_draft(changed['id'],[new['id']])['passed'])
        self.assertEqual(changed['attachmentIds'],[new['id']])

    def test_prepared_preview_holds_file_lock_against_archive(self):
        file=self.service.files.create_document('quote','txt','Quote','80 pcs',[])
        original=self.compose._prepare
        def racing_prepare(value):
            with DraftLifecycle(self.directory) as life:
                with self.assertRaisesRegex(ValueError,'正在'):
                    with life.lock(file['id']):self.fail('preview lost attachment lock')
            return original(value)
        with patch.object(self.compose,'_prepare',racing_prepare):
            result=self.prepare(attachmentIds=[file['id']])
        self.assertEqual(result['status'],'prepared')
        self.assertEqual(self.smtp.events,[])

# Avoid discovering the imported fixture base as a duplicate suite.
del MailComposeTests
