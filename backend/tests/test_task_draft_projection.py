"""Task draft history and current state, real stores and review RPC, no model."""
from network_guard import no_external_network
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from task_backend.agent_tools import AgentTools
from task_backend.archive import drafts_value
from task_backend.draft_lifecycle import DraftLifecycle
from task_backend.service import BackendService


class TaskDraftProjectionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.directory = Path(self.temp.name)
        self.service = BackendService(self.directory, worker=True)
        self.service.settings.for_request = lambda: dict(baseUrl='https://offline.invalid', model='fixture', apiKey='fixture')
        self.service.agent.schedule = lambda: None
        self.run = self.service.agent.call('startTurn', dict(text='Prepare tasks for review.'))
        self.service.agent._update(self.run['id'], dict(status='running', _executing=True))
        self.ctx = self.context()
        self.enterContext(no_external_network())

    def context(self):
        return AgentTools(self.service, self.run['id'], self.run['conversationId'],
            lambda event: self.service.agent._event(self.run['id'], event))

    def tearDown(self):
        self.ctx.close(); self.service.close(); self.temp.cleanup()

    def reopen(self):
        self.ctx.close(); self.service.close()
        self.service = BackendService(self.directory, worker=True)
        self.service.agent._update(self.run['id'], dict(status='running', _executing=True))
        self.ctx = self.context()

    def batch(self):
        # Multi-item analysis uses the existing persisted format and real journal.
        def persist(id, created):
            items = drafts_value([dict(tempId='a', parentTempId=None, title='Report', due=dict(mode='none')),
                dict(tempId='b', parentTempId=None, title='Discussion', due=dict(mode='none'))])
            analysis = dict(id=id, messageIds=[], drafts=items, saved=False, complete=True,
                instructions='Await review', notes='尚未写入任务或日历', createdAt=created)
            self.service.mail.store.save_analyses([analysis])
            payload = dict(id=id, kind='task_draft', title='Report and discussion', analysisId=id,
                status='pending_confirmation', detail='尚未写入任务或日历', analysis=analysis,
                conversationId=self.run['conversationId'])
            with DraftLifecycle(self.directory) as life: return life.ensure(payload, 'task_draft')
        result = self.ctx._once('create_task_draft', 'batch', {}, persist)
        self.ctx._artifact('task_draft', result['id'], result['title'])
        return result

    def immutable(self, id):
        with DraftLifecycle(self.directory) as life:
            return life.db.execute('SELECT payload FROM draft_versions WHERE id=?', (id,)).fetchone()[0]

    def save(self, draft, items=None):
        return self.service.call('mail-workspace:saveAnalysis', draft['id'], items or
            self.service.mail.store.get_analysis(draft['id'])['drafts'])

    def pending_ids(self):
        return [set(v['id'] for v in self.ctx.list_drafts(kind='task_draft')['items']),
            set(v['id'] for v in self.ctx.list_tasks()['pendingDrafts']),
            set(v['id'] for v in self.service.call('agent:listPending') if v['kind']=='task_draft')]

    def test_saved_view_refreshes_progress_without_rewriting_original_payload(self):
        draft = self.ctx.create_task_draft('Report', call_id='draft')
        original = self.immutable(draft['id']); self.save(draft)
        view = self.ctx.read_draft(draft['id'])
        self.assertTrue(view['analysis']['saved'])
        self.assertEqual(view['status'], 'saved')
        self.assertEqual(view['saveProgress']['state'], 'saved')
        self.assertEqual(view['saveProgress']['pendingTempIds'], [])
        self.assertEqual(view['currentTasks'][0]['state'], 'active')
        self.assertEqual(self.immutable(draft['id']), original)
        self.assertFalse(view['originalProposal']['analysis']['saved'])
        self.assertEqual(self.pending_ids(), [set(), set(), set()])

    def test_read_partial_save_never_finishes_it_and_all_pending_entrypoints_agree(self):
        draft = self.batch(); items = draft['analysis']['drafts']
        self.save(draft, items[:1])
        with DraftLifecycle(self.directory) as life: before = life.get(draft['id'])['lifecycleStatus']
        view = self.ctx.read_draft(draft['id'])
        with DraftLifecycle(self.directory) as life: after = life.get(draft['id'])['lifecycleStatus']
        self.assertEqual(after, before, 'read changed partial saving to fully saved')
        self.assertEqual(view['status'], 'partially_saved')
        self.assertEqual(view['saveProgress']['pendingTempIds'], ['b'])
        self.assertEqual(self.pending_ids(), [{draft['id']}]*3)
        self.assertEqual(self.save(draft, items)['count'], 1)
        self.assertEqual(len(self.service.call('listTodos')), 2)
        self.assertEqual(self.pending_ids(), [set(), set(), set()])

    def test_historical_saved_flag_from_old_read_bug_does_not_block_remaining_review(self):
        draft = self.batch(); items = draft['analysis']['drafts']; self.save(draft, items[:1])
        with DraftLifecycle(self.directory) as life: life.set_status(draft['id'], 'saved')
        view = self.ctx.read_draft(draft['id'])
        self.assertEqual(view['status'], 'partially_saved')
        self.assertEqual(view['storedLifecycleStatus'], 'saved')
        with DraftLifecycle(self.directory) as life:
            self.assertEqual(life.get(draft['id'])['lifecycleStatus'], 'saved', 'reading repaired a write-side record')
        self.assertEqual(self.pending_ids(), [{draft['id']}]*3)
        self.assertEqual(self.save(draft, items)['count'], 1)
        self.assertEqual(len(self.service.call('listTodos')), 2)
        self.assertTrue(self.ctx.read_draft(draft['id'])['analysis']['saved'])

    def test_current_tasks_follow_reschedule_cancel_restore_and_completion_after_reopen(self):
        draft = self.ctx.create_task_draft('Discussion', due_date='2026-11-25', call_id='draft'); self.save(draft)
        original = self.immutable(draft['id']); task = self.service.call('listTodos')[0]
        proposal = self.service.task_changes.propose(dict(action='update', taskId=task['id'], expectedRevision=1,
            patch=dict(due=dict(mode='value', value=dict(kind='date', date='2026-11-26'))), reason='Changed date'))
        self.service.call('task-changes:approve', dict(id=proposal['id'], expectedHash=proposal['hash']))
        self.reopen(); view = self.ctx.read_draft(draft['id']); current = view['currentTasks'][0]
        self.assertEqual(current['task']['due']['value']['date'], '2026-11-26')
        self.assertEqual(current['task']['revision'], 2)
        cancelled = self.service.task_changes.propose(dict(action='cancel', taskId=task['id'], expectedRevision=2, reason='Meeting cancelled'))
        self.service.call('task-changes:approve', dict(id=cancelled['id'], expectedHash=cancelled['hash']))
        self.reopen(); view = self.ctx.read_draft(draft['id']); current = view['currentTasks'][0]
        self.assertEqual(view['status'], 'saved')
        self.assertEqual(current['state'], 'cancelled')
        self.assertEqual(current['cancellationChangeId'], cancelled['id'])
        self.assertFalse(current['task']['completed'])
        self.assertEqual(self.pending_ids(), [set(), set(), set()])
        self.service.call('restoreBatch', current['task']['trashBatchId'])
        task = self.service.call('listTodos')[0]
        self.assertEqual(self.ctx.read_draft(draft['id'])['currentTasks'][0]['state'], 'active')
        self.service.call('setCompleted', task['id'], True, task['revision'])
        self.assertEqual(self.ctx.read_draft(draft['id'])['currentTasks'][0]['state'], 'completed')
        self.assertEqual(self.immutable(draft['id']), original)

    def test_manual_trash_is_not_mislabeled_as_a_cancelled_event(self):
        draft = self.ctx.create_task_draft('Task', call_id='draft'); self.save(draft)
        task = self.service.call('listTodos')[0]
        self.service.call('trashTodo', task['id'], task['revision'])
        current = self.ctx.read_draft(draft['id'])['currentTasks'][0]
        self.assertEqual(current['state'], 'trashed')
        self.assertIsNone(current.get('cancellationChangeId'))

    def test_superseded_or_dismissed_versions_do_not_reappear_pending_or_resave(self):
        old = self.ctx.create_task_draft('Old date', call_id='old')
        new = self.ctx.replace_task_draft(old['id'], 1, 'New date', call_id='new')
        self.assertEqual(self.ctx.read_draft(old['id'])['status'], 'superseded')
        self.assertEqual(self.pending_ids(), [{new['id']}]*3)
        with self.assertRaisesRegex(ValueError, '替代|撤回'): self.save(old)
        self.ctx.discard_draft(new['id'], 2, call_id='discard')
        self.assertEqual(self.ctx.read_draft(new['id'])['status'], 'dismissed')
        self.assertEqual(self.pending_ids(), [set(), set(), set()])
        with self.assertRaisesRegex(ValueError, '替代|撤回'): self.save(new)

    def test_commit_before_progress_crash_replays_review_without_duplicate_tasks(self):
        draft = self.batch(); items = draft['analysis']['drafts']
        with patch.object(self.service.mail.store, 'mark_progress', side_effect=OSError('simulated crash')):
            with self.assertRaises(OSError): self.save(draft, items[:1])
        self.reopen()
        self.assertEqual(self.pending_ids(), [{draft['id']}]*3)
        self.assertEqual(self.save(draft, items[:1])['count'], 1)
        self.assertEqual(self.save(draft, items)['count'], 1)
        self.assertEqual(len(self.service.call('listTodos')), 2)

    def test_missing_linked_todo_is_unknown_not_a_new_unsaved_draft(self):
        draft = self.ctx.create_task_draft('Task', call_id='draft'); self.save(draft)
        task = self.service.call('listTodos')[0]
        self.service.repo.db.execute('DELETE FROM todos WHERE id=?', (task['id'],))
        view = self.ctx.read_draft(draft['id'])
        self.assertEqual(view['status'], 'saved')
        self.assertEqual(view['currentTasks'][0]['state'], 'missing')
        self.assertFalse(view['isPending'])
        self.assertEqual(self.save(draft)['count'], 0)
        self.assertEqual(self.service.call('listTodos'), [])

    def test_legacy_fully_saved_without_mappings_stays_saved_and_unknown_after_reopen(self):
        draft = self.ctx.create_task_draft('Legacy task', call_id='legacy')
        self.service.mail.store.db.execute('UPDATE mail_analyses SET saved=1 WHERE id=?', (draft['id'],))
        self.reopen()
        view = self.ctx.read_draft(draft['id'])
        self.assertEqual(view['status'], 'saved')
        self.assertEqual(view['currentTasks'][0]['state'], 'unlinked')
        self.assertIsNone(view['currentTasks'][0]['taskId'])
        self.assertEqual(self.pending_ids(), [set(), set(), set()])
        self.assertEqual(self.save(draft)['count'], 0)
        self.assertEqual(self.service.call('listTodos'), [])

    def test_old_cancel_receipt_cannot_mislabel_a_later_manual_trash(self):
        draft = self.ctx.create_task_draft('Meeting', call_id='meeting'); self.save(draft)
        task = self.service.call('listTodos')[0]
        proposal = self.service.task_changes.propose(dict(action='cancel', taskId=task['id'],
            expectedRevision=1, reason='Cancelled'))
        self.service.call('task-changes:approve', dict(id=proposal['id'], expectedHash=proposal['hash']))
        old = self.ctx.read_draft(draft['id'])['currentTasks'][0]
        self.service.call('restoreBatch', old['task']['trashBatchId'])
        current = self.service.call('listTodos')[0]
        self.service.call('trashTodo', current['id'], current['revision'])
        self.reopen(); result = self.ctx.read_draft(draft['id'])['currentTasks'][0]
        self.assertNotEqual(result['task']['trashBatchId'], old['task']['trashBatchId'])
        self.assertEqual(result['state'], 'trashed')
        self.assertNotIn('cancellationChangeId', result)

    def test_fresh_projection_does_not_let_a_stale_change_overwrite_current_task(self):
        draft = self.ctx.create_task_draft('Discussion', call_id='meeting'); self.save(draft)
        original = self.ctx.read_draft(draft['id'])['currentTasks'][0]['task']
        proposal = self.service.task_changes.propose(dict(action='update', taskId=original['id'],
            expectedRevision=original['revision'], patch=dict(title='Stale title'), reason='A proposed edit'))
        self.service.call('updateTodo', original['id'], dict(title='Manually changed title'), original['revision'])
        self.reopen()
        current = self.ctx.read_draft(draft['id'])['currentTasks'][0]['task']
        self.assertEqual(current['revision'], 2)
        self.assertEqual(current['title'], 'Manually changed title')
        with self.assertRaisesRegex(ValueError, '变化'):
            self.service.call('task-changes:approve', dict(id=proposal['id'], expectedHash=proposal['hash']))
        self.assertEqual(self.service.call('listTodos')[0]['title'], 'Manually changed title')


if __name__ == '__main__': unittest.main()
