"""Real import/classifier/persistence services; fixture transport only, no network."""
from network_guard import no_external_network
import base64
from copy import deepcopy
from datetime import datetime, timedelta
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from zoneinfo import ZoneInfo

from cryptography.fernet import Fernet
from task_backend.agent_runtime import AgentStopped
from task_backend.mail_automation import signature
from task_backend.mail_compose import receipt_lock
from task_backend.secretary import SecretaryService, content_version
from task_backend.service import BackendService


class PagedReader:
    def __init__(self):
        self.messages = []; self.reads = []; self.pages = []; self.fail = False; self.failed_ids = set(); self.hook = None

    def test_connection(self): pass

    def read_for_import(self, filters, excluded):
        self.pages.append(deepcopy(filters))
        if self.fail: raise OSError('fixture list failure')
        selected = [m for m in self.messages if filters['since'] <= m['receivedAt'][:10] <= filters['until']]
        pending = [m for m in selected if m['id'] not in excluded]
        return dict(messages=deepcopy(pending[:7]), limited=len(pending) > 7,
                    skippedRemoteIds=[m['id'] for m in selected if m['id'] in excluded])

    def get_message(self, ident):
        self.reads.append(ident)
        if self.hook: self.hook(ident)
        if ident in self.failed_ids: raise OSError('fixture body failure')
        return deepcopy(next(m for m in self.messages if m['id'] == ident))


class SecretaryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        cipher = Fernet(Fernet.generate_key())
        self.enterContext(patch('task_backend.vault.encrypt', lambda s: base64.b64encode(cipher.encrypt(s.encode())).decode()))
        self.enterContext(patch('task_backend.vault.decrypt', lambda s: cipher.decrypt(base64.b64decode(s)).decode()))
        self.enterContext(no_external_network())
        self.clock = datetime(2026, 11, 22, 10, tzinfo=ZoneInfo('Asia/Hong_Kong'))
        self.boxes = {}; self.calls = []; self.invalid = set(); self.model_hook = None
        self.open_service(); self.addCleanup(self.close_service)
        self.binding = self.account('owner')

    def open_service(self):
        self.service = BackendService(self.directory, request=self.request, worker=True)
        self.service.clock = lambda: self.clock
        self.service.settings.for_request = lambda: dict(baseUrl='https://fixture.invalid/v1', model='fixture', apiKey='synthetic-key')
        self.service.mail.manager.reader_factory = lambda manager, binding: self.boxes[binding]
        self.secretary = self.service.secretary

    def close_service(self):
        self.service.close()

    def reopen(self):
        self.close_service(); self.open_service()

    def account(self, name):
        binding = self.service.mail.manager.add('imap')['id']; self.boxes[binding] = PagedReader()
        self.service.mail.manager.configure_imap(binding, dict(email=name+'@example.invalid', host='imap.example.invalid', port=993, security='tls', password='fixture'))
        self.service.mail.manager.connect(binding)
        return binding

    def mail(self, subject, binding=None, status='complete', ago=0):
        reader = self.boxes[binding or self.binding]
        value = dict(id=json.dumps(['INBOX', 1, len(reader.messages)+1]), subject=subject,
                     sender='Same name <'+subject.split()[0].lower()+'@example.invalid>', receivedAt=(self.clock-timedelta(days=ago)).isoformat(),
                     preview='', body=subject+' actual original body', bodyStatus=status, unread=True, webUrl='', imported=False)
        reader.messages.append(value)
        return value

    def request(self, url, body, *args):
        payload = json.loads(body)
        self.assertTrue(payload['messages'][0]['content'].startswith('你是邮件分类助手'))
        mail = json.loads(payload['messages'][1]['content']); self.calls.append(mail['subject'])
        if self.model_hook:
            hook, self.model_hook = self.model_hook, None; hook()
        if mail['subject'] in self.invalid: return b'broken JSON'
        category = mail['subject'].split()[0].lower()
        if category not in ('action', 'important', 'normal', 'spam', 'review'): category = 'normal'
        result = dict(category=category, summary='真实传输夹具摘要 '+mail['subject'], reason='邮件明确内容')
        return json.dumps(dict(choices=[dict(finish_reason='stop', message=dict(content=json.dumps(result)))])).encode()

    def test_empty_and_disconnected_are_not_failed_refresh_disguised_as_empty(self):
        result = self.secretary.refresh('empty')
        self.assertEqual(result['status'], 'completed'); self.assertEqual(result['total'], 0); self.assertEqual(result['errors'], [])
        self.service.mail.manager.disconnect(self.binding)
        result = self.secretary.refresh('not-connected')
        self.assertEqual(result['status'], 'needs_setup'); self.assertEqual(result['errors'][0]['code'], 'NO_CONNECTED_ACCOUNT')
        self.assertEqual(self.calls, [])

    def test_paged_all_account_import_twenty_budget_remaining_visible_and_new_run(self):
        other = self.account('other')
        for n in range(22): self.mail(('action' if n==0 else 'spam' if n==1 else 'normal')+' '+str(n))
        self.mail('important second-account', other)
        self.mail('normal too-old', ago=10)
        result = self.secretary.refresh('r1')
        self.assertEqual(result['importReport']['added'], 23)
        self.assertEqual(result['attemptedClassifications'], 20)
        self.assertEqual(len(self.calls), 20); self.assertEqual(result['pendingClassification'], 3)
        self.assertEqual(result['total'], 23); self.assertEqual(result['status'], 'partial')
        self.assertGreater(len(self.boxes[self.binding].pages), 1)
        self.assertEqual({i['accountEmail'] for i in result['items']}, {'owner@example.invalid', 'other@example.invalid'})
        self.assertEqual(self.service.repo.call('listTodos'), [])
        self.assertEqual(self.service.mail.store.db.execute('SELECT count(*) FROM mail_analyses').fetchone()[0], 0)
        self.assertEqual(self.service.automation.get_config()['enabled'], False)
        self.assertEqual(len(self.service.operations.jobs()), 2)
        self.assertTrue(all(job['state']=='completed' for job in self.service.operations.jobs()))
        again = self.secretary.refresh('r1'); self.assertEqual(again, result); self.assertEqual(len(self.calls), 20)
        self.assertEqual(len(self.service.operations.jobs()), 2)
        self.reopen(); self.assertEqual(self.secretary.get_refresh('r1'), result)
        self.assertEqual(self.secretary.refresh('r1'), result); self.assertEqual(len(self.calls), 20)
        next_run = self.secretary.refresh('r2')
        self.assertEqual(next_run['importReport']['added'], 0); self.assertEqual(next_run['pendingClassification'], 0)
        self.assertEqual(len(self.calls), 23); self.assertEqual(next_run['total'], 23)
        self.assertEqual(next_run['collapsedCategories'], ['normal', 'spam'])
        self.assertEqual(len(self.boxes[self.binding].reads), 22)
        self.assertEqual(len(self.service.operations.jobs()), 4)

    def test_delivery_is_explicit_not_analysis_and_content_change_becomes_unbriefed(self):
        remote = self.mail('action report')
        first = self.secretary.refresh('first'); item = first['items'][0]
        self.assertIsNone(item['briefedAt'])
        self.assertEqual(self.secretary.get_briefing()['total'], 1)
        self.assertEqual(self.secretary.mark_briefed('first')['marked'], [item['localId']])
        self.assertEqual(self.secretary.get_briefing()['total'], 0)
        shown = self.secretary.get_briefing(True)['items'][0]
        self.assertTrue(shown['briefedAt']); self.assertTrue(shown['unread']); self.assertEqual(shown['category'], 'action')
        with self.assertRaises(ValueError): self.secretary.mark_briefed('first', [{**item, 'localId': 'f'*64}])
        self.reopen(); self.assertEqual(self.secretary.get_briefing()['total'], 0)
        mail = self.service.mail.store.get_local(item['localId'])
        self.service.mail.store.put_body(item['localId'], {**mail, 'body': 'Changed instructions '+remote['body']})
        self.assertEqual(self.secretary.mark_briefed('first')['marked'], [])
        new = self.secretary.refresh('changed')['items'][0]
        self.assertNotEqual(new['fingerprint'], item['fingerprint']); self.assertIsNone(new['briefedAt'])
        self.assertEqual(self.calls, ['action report', 'action report'])

    def test_failures_are_visible_and_separate_accounts_continue_then_next_id_recovers(self):
        other = self.account('working'); self.boxes[self.binding].fail = True
        self.mail('normal broken', other); self.invalid.add('normal broken')
        result = self.secretary.refresh('bad')
        self.assertEqual(result['status'], 'partial'); self.assertEqual(result['importReport']['failed'], 1)
        self.assertEqual(result['items'][0]['status'], 'failed'); self.assertGreaterEqual(len(result['errors']), 2)
        self.assertEqual(self.secretary.mark_briefed('bad')['marked'], [])
        self.invalid.clear(); self.boxes[self.binding].fail = False
        self.assertEqual(self.secretary.refresh('bad'), result)
        fixed = self.secretary.refresh('retry')
        self.assertEqual(fixed['status'], 'completed'); self.assertEqual(fixed['pendingClassification'], 0)
        self.assertEqual(fixed['items'][0]['status'], 'classified'); self.assertEqual(len(self.calls), 2)
        self.assertEqual(self.secretary.mark_briefed('bad')['marked'], [])
        self.assertEqual(self.secretary.get_briefing()['total'], 1)

    def test_same_fingerprint_automation_cache_reused_but_not_marked_briefed_or_done(self):
        self.mail('normal reuse')
        first = self.secretary.refresh('first'); item = first['items'][0]
        self.secretary.db.execute('DELETE FROM secretary_items')
        self.service.automation.save_item(dict(localId=item['localId'], fingerprint=item['fingerprint'], corrected=True,
            category='important', summary='已有人工分类', reason='用户规则', status='done', analysisId='some-saved-analysis'))
        result = self.secretary.refresh('reuse'); reused = result['items'][0]
        self.assertEqual(len(self.calls), 1); self.assertEqual(reused['category'], 'important')
        self.assertIsNone(reused['briefedAt']); self.assertEqual(reused['classificationSource'], 'automation')
        self.assertEqual(self.service.repo.call('listTodos'), [])

    def test_user_override_wins_and_old_delivery_cannot_swallow_correction(self):
        self.mail('spam not-an-ad')
        first = self.secretary.refresh('first'); item = first['items'][0]
        self.secretary.mark_briefed('first')
        fixed = self.secretary.correct_category(item['localId'], 'important', '这是老师的重要通知')
        self.assertEqual(fixed['originalClassification']['category'], 'spam')
        self.assertTrue(fixed['corrected']); self.assertIsNone(fixed['briefedAt'])
        self.assertEqual(self.secretary.mark_briefed('first')['marked'], [])
        later = self.secretary.refresh('next')['items'][0]
        self.assertEqual(later['category'], 'important'); self.assertEqual(len(self.calls), 1)
        self.secretary.mark_briefed('next'); self.assertEqual(self.secretary.get_briefing()['total'], 0)

    def test_partial_body_not_sent_to_model_and_retried_after_reading_full_body(self):
        remote = self.mail('action partial', status='partial')
        failed = self.secretary.refresh('partial')
        self.assertEqual(self.calls, []); self.assertEqual(failed['pendingClassification'], 1)
        self.assertEqual(failed['items'][0]['bodyStatus'], 'partial')
        remote['bodyStatus'] = 'complete'
        fixed = self.secretary.refresh('full')
        self.assertEqual(fixed['pendingClassification'], 0); self.assertEqual(self.calls, ['action partial'])

    def test_human_correction_wins_over_late_invalid_model_response(self):
        self.mail('spam late'); self.invalid.add('spam late')
        def correct():
            pending = self.secretary.get_briefing()['items'][0]
            self.secretary.correct_category(pending['localId'], 'important', '用户明确纠正')
        self.model_hook = correct
        result = self.secretary.refresh('late')
        self.assertEqual(result['status'], 'completed')
        self.assertEqual(result['items'][0]['category'], 'important')
        self.assertTrue(result['items'][0]['corrected']); self.assertIsNone(result['items'][0]['error'])

    def test_pause_resume_reuses_successful_classifications_and_fixed_budget(self):
        self.mail('normal first'); self.mail('normal second')
        def check():
            if len(self.calls) == 1 and any(i['status']=='classified' for i in self.secretary._current_items()):
                raise AgentStopped()
        with self.assertRaises(AgentStopped): self.secretary.refresh('paused', check)
        self.assertEqual(len(self.calls), 1)
        self.reopen(); result = self.secretary.refresh('paused')
        self.assertEqual(result['status'], 'completed'); self.assertEqual(len(self.calls), 2)
        self.assertEqual(result['attemptedClassifications'], 2)

    def test_shared_process_lock_prevents_parallel_refresh_and_retry_is_allowed(self):
        self.mail('normal one')
        with receipt_lock(self.directory/'secretary-refresh.lock') as acquired:
            self.assertTrue(acquired)
            result = self.secretary.refresh('concurrent')
            self.assertEqual(result['status'], 'busy'); self.assertEqual(self.calls, [])
        self.assertEqual(self.secretary.refresh('concurrent')['status'], 'completed')
        self.assertEqual(len(self.calls), 1)
        with receipt_lock(self.directory/'secretary-refresh.lock'):
            self.assertEqual(self.secretary.refresh('concurrent')['status'], 'completed')
            self.assertEqual(len(self.calls), 1)

    def test_large_pending_backlog_is_paged_and_does_not_hide_classified_summaries(self):
        for n in range(205): self.mail('normal '+str(n))
        result = self.secretary.refresh('many')
        self.assertEqual(result['total'], 205); self.assertEqual(len(result['items']), 100)
        self.assertEqual(result['pendingClassification'], 185); self.assertTrue(result['hasMoreClassification'])
        self.assertEqual(sum(i['status']=='classified' for i in result['items']), 20)
        second = self.secretary.get_briefing(limit=100, offset=result['nextOffset'])
        last = self.secretary.get_briefing(limit=100, offset=second['nextOffset'])
        self.assertEqual(len(last['items']), 5); self.assertIsNone(last['nextOffset'])
        self.assertEqual(len({i['localId'] for i in result['items']+second['items']+last['items']}), 205)

    def test_pause_during_import_does_not_become_a_transfer_failure(self):
        for n in range(9): self.mail('normal '+str(n))
        stop = False
        def on_read(_):
            nonlocal stop
            if len(self.boxes[self.binding].reads)==2: stop=True
        self.boxes[self.binding].hook = on_read
        def check():
            if stop: raise AgentStopped()
        with self.assertRaises(AgentStopped): self.secretary.refresh('paused-import', check)
        self.assertEqual(self.calls, [])
        jobs = self.service.operations.jobs(); self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0]['state'], 'paused'); self.assertEqual(jobs[0]['report']['failed'], 0)
        self.boxes[self.binding].hook = None
        self.reopen(); result = self.secretary.refresh('paused-import')
        self.assertEqual(result['status'], 'completed'); self.assertEqual(result['total'], 9)
        self.assertEqual(self.boxes[self.binding].reads.count(self.boxes[self.binding].messages[0]['id']), 1)
        self.assertEqual(len(self.service.operations.jobs()), 1)

    def test_long_gap_uses_saved_import_cursor_instead_of_losing_intervening_mail(self):
        self.mail('normal initial'); self.secretary.refresh('initial')
        self.clock += timedelta(days=15)
        self.mail('normal intervening', ago=10)
        result = self.secretary.refresh('later')
        self.assertEqual(result['importReport']['added'], 1)
        self.assertIn('normal intervening', [i['subject'] for i in result['items']])
        self.assertEqual(self.calls, ['normal initial', 'normal intervening'])

    def test_account_identity_change_during_import_never_classifies_under_wrong_account(self):
        self.mail('normal late')
        def disconnect(_):
            self.service.mail.manager.disconnect(self.binding)
            self.boxes[self.binding].hook = None
        self.boxes[self.binding].hook = disconnect
        result = self.secretary.refresh('identity-change')
        self.assertEqual(result['status'], 'partial'); self.assertEqual(self.calls, [])
        self.assertTrue(result['importReport']['mailboxErrors'])
        self.assertEqual(result['pendingClassification'], 1)


if __name__ == '__main__': unittest.main()
