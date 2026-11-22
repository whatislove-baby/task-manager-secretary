"""Existing task changes use real stores, approval, concurrency and restart."""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from task_backend.service import BackendService
from task_backend.agent_tools import AgentTools


class TaskChangeTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
        self.s=BackendService(self.root,worker=True)
        self.task=self.s.repo.call('createTodo',{'title':'STAT4102 final','due':{'mode':'value','value':{'kind':'minute','at':'2026-12-03T09:00:00+08:00','timeZone':'Asia/Hong_Kong'}}})['value']
        self.mail=self.s.mail.store.put('a','i','student@example.invalid',dict(id='1',subject='STAT4102 time correction',sender='teacher@example.invalid',body='Final exam moves to 4 December at 14:00 HKT.',receivedAt='2026-11-21T00:00:00Z',attachments=[]))

    def tearDown(self):self.s.close();self.tmp.cleanup()

    def proposal(self,action='update'):
        return self.s.task_changes.propose(dict(action=action,taskId=self.task['id'],expectedRevision=self.task['revision'],reason='Latest teacher correction',
            patch={} if action=='cancel' else {'due':{'mode':'value','value':{'kind':'minute','at':'2026-12-04T14:00:00+08:00','timeZone':'Asia/Hong_Kong'}}},
            evidence=[{'mailId':self.mail['localId'],'quote':'Final exam moves to 4 December at 14:00 HKT.'}]))

    def approve(self,p):return self.s.call('task-changes:approve',{'id':p['id'],'expectedHash':p['hash']})

    def test_review_updates_same_task_exact_time_once_and_survives_restart(self):
        p=self.proposal();self.assertEqual(self.s.repo.call('listTodos')[0],self.task)
        result=self.approve(p);self.assertEqual(result['status'],'approved')
        actual=self.s.repo.call('listTodos');self.assertEqual(len(actual),1);self.assertEqual(actual[0]['id'],self.task['id'])
        self.assertEqual(actual[0]['due']['value']['at'],'2026-12-04T06:00:00.000Z')
        revision=actual[0]['revision'];self.assertEqual(self.approve(p),result)
        self.s.close();self.s=BackendService(self.root,worker=True)
        self.assertEqual(self.approve(p),result);self.assertEqual(self.s.repo.call('listTodos')[0]['revision'],revision)

    def test_concurrent_user_edit_is_not_overwritten(self):
        p=self.proposal();self.s.repo.call('updateTodo',self.task['id'],{'notes':'Changed by user'},self.task['revision'])
        with self.assertRaisesRegex(ValueError,'发生变化'):self.approve(p)
        self.assertEqual(self.s.task_changes.get(p['id'])['status'],'stale')
        self.assertEqual(self.s.task_changes.list()[0]['status'],'stale')
        self.assertEqual(self.s.repo.call('listTodos')[0]['notes'],'Changed by user')

    def test_cancel_is_recoverable_and_never_marks_complete(self):
        p=self.proposal('cancel');result=self.approve(p)
        self.assertEqual(self.s.repo.call('listTodos'),[])
        self.assertIsNotNone(result['after']['deletedAt']);self.assertFalse(result['after']['completed'])
        self.assertTrue(result['trashBatchId'])

    def test_changed_hash_rejected_and_explicit_rejection_has_no_side_effect(self):
        p=self.proposal()
        with self.assertRaises(ValueError):self.s.task_changes.approve({'id':p['id'],'expectedHash':'wrong'})
        self.s.task_changes.reject({'id':p['id'],'expectedHash':p['hash']})
        with self.assertRaises(ValueError):self.approve(p)
        self.assertEqual(self.s.repo.call('listTodos')[0],self.task)

    def test_receipt_write_failure_rolls_back_task_and_receipt_together(self):
        p=self.proposal()
        with patch.object(self.s.task_changes,'_save',side_effect=RuntimeError('disk failure')):
            with self.assertRaises(RuntimeError):self.approve(p)
        self.assertEqual(self.s.repo.call('listTodos')[0],self.task)
        self.assertEqual(self.s.task_changes.get(p['id'])['status'],'pending')

    def test_new_child_after_proposal_blocks_cancel(self):
        p=self.proposal('cancel')
        self.s.repo.call('createTodo',{'title':'materials','parentId':self.task['id']})
        with self.assertRaises(ValueError):self.approve(p)
        self.assertEqual(len(self.s.repo.call('listTodos')),2)

    def test_task_draft_preserves_explicit_clock_and_mutations_need_user_review(self):
        ctx=AgentTools(self.s,'clock');self.addCleanup(ctx.close)
        draft=ctx.create_task_draft('Workshop',due_at='2026-12-05T18:30:00+08:00',call_id='clock')
        stored=self.s.mail.store.get_analysis(draft['id'])['drafts'][0]
        self.assertEqual(stored['due']['value']['at'],'2026-12-05T10:30:00.000Z')
        self.assertEqual(len(self.s.repo.call('listTodos')),1)
        with self.assertRaises(ValueError):ctx.execute('approve_learning',{},'no')

    def test_source_quote_and_task_revision_are_checked_before_proposing(self):
        value=dict(action='update',taskId=self.task['id'],expectedRevision=self.task['revision'],reason='bad',patch={'title':'new'},evidence=[{'mailId':self.mail['localId'],'quote':'invented'}])
        with self.assertRaises(ValueError):self.s.task_changes.propose(value)
        self.assertEqual(self.s.task_changes.list(),[])
