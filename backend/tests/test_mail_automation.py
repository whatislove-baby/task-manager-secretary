"""Automatic triage integration: isolated SQLite, fixture IMAP/model, no network."""
import base64
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from cryptography.fernet import Fernet
from task_backend.mail_automation import MailAutomation, DEFAULT_CONFIG
from task_backend.mail_compose import receipt_lock
from task_backend.mail_types import DEFAULT_FILTER, matches_filter
from task_backend.service import BackendService


class Reader:
    def __init__(self, messages):
        self.messages = messages
        self.filters = []
        self.fail = False

    def test_connection(self):
        pass

    def read_for_import(self, filters, excluded):
        self.filters.append(deepcopy(filters))
        if self.fail:
            raise ValueError('fixture unavailable')
        selected = [deepcopy(v) for v in self.messages if matches_filter(v, filters)
                    and filters['since'] <= v['receivedAt'][:10] <= filters['until']]
        skipped = sum(v['id'] in excluded for v in selected)
        return dict(messages=[v for v in selected if v['id'] not in excluded][:200], limited=len(selected)-skipped > 200, skippedKnown=skipped)

    def get_message(self, remote):
        return deepcopy(next(v for v in self.messages if v['id'] == remote))


class MailAutomationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        cipher = Fernet(Fernet.generate_key())
        for name, value in [('encrypt', lambda s: base64.b64encode(cipher.encrypt(s.encode())).decode()),
                            ('decrypt', lambda s: cipher.decrypt(base64.b64decode(s)).decode())]:
            p = patch('task_backend.vault.' + name, value)
            p.start(); self.addCleanup(p.stop)
        p = patch('socket.create_connection', side_effect=AssertionError('real network forbidden'))
        p.start(); self.addCleanup(p.stop)
        self.clock = datetime(2026, 11, 20, 0, 0, tzinfo=timezone.utc)  # 08:00 Hong Kong
        self.calls = []
        self.model_hook = None
        self.invalid = set()
        self.extract_failures = 0
        self.boxes = {}
        self.service = BackendService(Path(self.temp.name), request=self.request, worker=True)
        self.service.clock = lambda: self.clock
        self.addCleanup(self.service.close)
        self.service.mail.manager.reader_factory = lambda manager, binding: self.boxes[binding]
        self.service.call('saveSettings', dict(baseUrl='https://fixture.invalid/v1', model='fixture', apiKey='synthetic-secret'))
        self.engine = MailAutomation(self.service)
        self.addCleanup(lambda: self.engine.close())
        self.binding = self.add_account()

    def add_account(self, name='owner'):
        binding = self.service.call('mail-workspace:addAccount', 'imap')['id']
        self.boxes[binding] = Reader([])
        self.service.call('mail-workspace:configureImap', binding, dict(
            email=name+'@example.invalid', host='imap.example.invalid', port=993, security='tls', password='fixture-code'))
        self.service.call('mail-workspace:connect', binding)
        return binding

    def mail(self, marker, sender='client@example.invalid', body_status='complete', binding=None):
        box = self.boxes[binding or self.binding]
        value = dict(id=json.dumps(['INBOX', 1, len(box.messages)+1]), subject=marker,
            sender=sender, receivedAt=self.clock.isoformat(), preview=marker, body=marker,
            unread=True, webUrl='', imported=False, bodyStatus=body_status)
        box.messages.append(value)
        return value

    def configure(self, **changes):
        value = {**deepcopy(DEFAULT_CONFIG), 'enabled': True, 'bindingIds': [self.binding], **changes}
        self.engine.call('saveConfig', value)
        return value

    def request(self, url, raw, headers, limit, timeout):
        body = json.loads(raw)
        classification = body['messages'][0]['content'].startswith('你是邮件分类助手')
        content = body['messages'][1]['content']
        marker = next((v for v in ('ACTION', 'BAD', 'PARTIAL', 'IMPORTANT', 'NORMAL', 'FIRST', 'SECOND', 'THIRD') if v in content), 'NORMAL')
        self.calls.append((classification, marker))
        if self.model_hook:
            hook = self.model_hook
            self.model_hook = None
            hook()
        if marker in self.invalid:
            return b'{invalid'
        if not classification and self.extract_failures:
            self.extract_failures -= 1
            return b'{invalid'
        if classification:
            value = dict(category='action' if marker == 'ACTION' else 'normal', summary='合成摘要', reason='合成分类依据')
        else:
            value = dict(drafts=[dict(tempId='1', parentTempId=None, title='处理合成任务', notes='核对原文',
                source=marker, due=dict(mode='none'))] if marker == 'ACTION' else [], notes='合成提取')
        return json.dumps(dict(choices=[dict(finish_reason='stop', message=dict(content=json.dumps(value, ensure_ascii=False)))])).encode()

    def manual(self):
        self.engine.call('runNow')
        return self.engine.tick()

    def results(self):
        return self.engine.call('listResults', {})

    def test_configuration_schedule_enable_cutoff_missed_slots_and_no_repeat(self):
        defaults = self.engine.call('getConfig')
        self.assertFalse(defaults['enabled'])
        self.assertIsNone(defaults['nextRunAt'])
        config = self.configure()
        self.assertEqual(self.engine.call('getConfig')['nextRunAt'], '2026-11-20T01:00:00+00:00')
        self.assertIsNone(self.engine.tick())
        self.clock += timedelta(days=2, hours=11)  # Missed several slots, now 19:00 HK
        run = self.engine.tick()
        self.assertEqual(run['trigger'], 'schedule')
        self.assertEqual(run['state'], 'completed')
        self.assertEqual(len(self.engine.records()), 1)
        self.assertIsNone(self.engine.tick())
        config['enabled'] = False
        self.engine.call('saveConfig', config)
        self.clock += timedelta(days=1)
        config['enabled'] = True
        self.engine.call('saveConfig', config)
        self.assertIsNone(self.engine.tick())  # No pre-enable catch-up.
        for bad in ({**config, 'times': ['25:00']}, {**config, 'maxMessagesPerRun': True}, {**config, 'extra': 1}):
            with self.assertRaises(ValueError):
                self.engine.call('saveConfig', bad)

    def test_rules_action_review_and_no_smtp_or_committed_tasks(self):
        self.mail('NORMAL', 'Muted <mute@example.invalid>')
        self.mail('IMPORTANT', 'VIP <vip@example.invalid>')
        self.mail('ACTION')
        self.mail('PARTIAL', body_status='partial')
        self.configure(importantSenders=['vip@example.invalid'], mutedSenders=['mute@example.invalid'])
        result = self.manual()
        self.assertEqual((result['state'], result['classified'], result['failed']), ('completed', 4, 0))
        items = {v['subject']: v for v in self.results()['items']}
        self.assertEqual([items[v]['category'] for v in ('NORMAL', 'IMPORTANT', 'ACTION', 'PARTIAL')], ['spam', 'important', 'action', 'review'])
        analysis = self.service.call('mail-workspace:getAnalysis', items['ACTION']['analysisId'])
        self.assertEqual(len(analysis['drafts']), 1)
        self.assertEqual(self.service.call('listTodos'), [])
        self.assertEqual(self.service.call('mail-compose:listRecent'), [])
        self.assertEqual(len(self.calls), 4)  # VIP and ACTION each classification + extraction.
        self.assertEqual(self.boxes[self.binding].filters[0]['folderIds'], ['INBOX'])
        self.assertEqual(self.boxes[self.binding].filters[0]['subject'], '')

    def test_budget_is_per_run_not_per_minute_and_oldest_backlog_advances(self):
        for marker in ('FIRST', 'SECOND', 'THIRD'):
            self.mail(marker)
            self.clock += timedelta(seconds=1)
        self.configure(maxMessagesPerRun=1)
        first = self.manual()
        self.assertEqual(first['imported'], 3)
        self.assertEqual(first['classified'], 1)
        self.assertEqual(self.calls, [(True, 'FIRST')])
        self.clock += timedelta(minutes=1)
        self.assertIsNone(self.engine.tick())
        self.assertEqual(len(self.calls), 1)
        self.manual()
        self.assertEqual(self.calls[-1], (True, 'SECOND'))
        self.assertEqual(self.results()['total'], 2)

    def test_fingerprint_partial_completion_and_human_correction_survive_reruns(self):
        self.mail('PARTIAL', body_status='partial')
        self.configure()
        self.manual()
        item = self.results()['items'][0]
        self.assertEqual(self.calls, [])
        local = self.service.mail.store.get_local(item['localId'])
        self.service.mail.store.put_body(local['localId'], {**local, 'body': 'ACTION', 'bodyStatus': 'complete'})
        self.manual()
        self.assertEqual(self.results()['items'][0]['category'], 'action')
        analysis_id = self.results()['items'][0]['analysisId']
        self.manual()
        self.assertEqual(len(self.calls), 2)
        corrected = self.engine.call('correctCategory', item['localId'], 'normal')
        self.assertTrue(corrected['corrected'])
        self.service.mail.store.put_body(local['localId'], {**local, 'body': 'ACTION changed same day', 'bodyStatus': 'complete'})
        self.manual()
        self.assertEqual(self.results()['items'][0]['category'], 'normal')
        self.assertEqual(self.results()['items'][0]['analysisId'], analysis_id)
        self.assertEqual(len(self.calls), 2)

    def test_failures_backoff_three_attempts_and_successful_items_not_repeated(self):
        self.mail('NORMAL')
        self.clock += timedelta(seconds=1)
        self.mail('BAD')
        self.invalid.add('BAD')
        self.configure()
        first = self.manual()
        self.assertEqual((first['state'], first['classified'], first['failed']), ('failed', 1, 1))
        self.assertIsNone(self.engine.tick())
        self.clock += timedelta(minutes=5)
        second = self.engine.tick()
        self.assertEqual(second['trigger'], 'retry')
        self.clock += timedelta(minutes=30)
        third = self.engine.tick()
        self.assertEqual(third['state'], 'failed')
        self.clock += timedelta(minutes=1)
        self.assertIsNone(self.engine.tick())
        self.assertEqual(self.calls.count((True, 'NORMAL')), 1)
        self.assertEqual(self.calls.count((True, 'BAD')), 3)
        self.assertIn('error', next(v for v in self.results()['items'] if v['subject'] == 'BAD'))

    def test_extraction_retry_reuses_classification_and_stable_analysis_id(self):
        self.mail('ACTION')
        self.extract_failures = 1
        self.configure()
        self.assertEqual(self.manual()['state'], 'failed')
        self.engine.close()
        self.engine = MailAutomation(self.service)
        self.clock += timedelta(minutes=5)
        self.assertEqual(self.engine.tick()['state'], 'completed')
        self.assertEqual(self.calls, [(True, 'ACTION'), (False, 'ACTION'), (False, 'ACTION')])
        item = self.results()['items'][0]
        self.assertEqual(item['category'], 'action')
        self.manual()
        self.assertEqual(self.results()['items'][0]['analysisId'], item['analysisId'])
        self.assertEqual(len(self.calls), 3)

    def test_disabled_during_model_call_stops_before_extraction(self):
        self.mail('ACTION')
        config = self.configure()
        self.model_hook = lambda: self.engine.call('saveConfig', {**config, 'enabled': False})
        run = self.manual()
        self.assertEqual(run['state'], 'failed')
        self.assertIn('停止', run['error'])
        self.assertEqual(self.calls, [(True, 'ACTION')])
        self.clock += timedelta(hours=1)
        self.assertIsNone(self.engine.tick())
        self.assertEqual(self.service.call('listTodos'), [])

    def test_human_correction_during_request_wins_and_prevents_extraction(self):
        self.mail('ACTION')
        self.configure()
        self.model_hook = lambda: self.engine.call('correctCategory', self.results()['items'][0]['localId'], 'normal')
        self.manual()
        self.assertEqual(self.results()['items'][0]['category'], 'normal')
        self.assertTrue(self.results()['items'][0]['corrected'])
        self.assertEqual(self.calls, [(True, 'ACTION')])

    def test_account_failure_isolated_and_identity_change_stops_affected_account(self):
        other = self.add_account('other')
        self.mail('NORMAL', binding=other)
        self.configure(bindingIds=[self.binding, other])
        self.boxes[self.binding].fail = True
        run = self.manual()
        self.assertEqual(run['state'], 'failed')
        self.assertEqual(run['classified'], 1)
        self.assertEqual(self.results()['items'][0]['bindingId'], other)
        self.service.call('mail-workspace:configureImap', self.binding, dict(
            email='different@example.invalid', host='imap.example.invalid', port=993, security='tls', password='fixture-code'))
        self.service.call('mail-workspace:connect', self.binding)
        with self.assertRaisesRegex(ValueError, '身份'):
            self.engine.call('runNow')

    def test_run_now_only_queues_and_lock_prevents_overlap(self):
        self.mail('NORMAL')
        self.configure()
        self.assertEqual(self.engine.call('runNow'), {'queued': True})
        self.assertEqual(self.engine.call('getStatus')['state'], 'idle')
        self.assertEqual(self.calls, [])
        with receipt_lock(Path(self.temp.name) / 'mail-automation-run.lock') as acquired:
            self.assertTrue(acquired)
            self.assertEqual(self.engine.call('getStatus')['state'], 'running')
            self.assertIsNone(self.engine.tick())
        self.assertEqual(self.engine.tick()['classified'], 1)

    def test_failed_host_start_leaves_manual_request_actionable_without_extra_budget(self):
        self.mail('FIRST')
        self.mail('SECOND')
        self.configure(enabled=False, maxMessagesPerRun=1)
        self.engine.call('runNow')
        requested = self.engine.settings()['manualRequested']
        # Simulate startOnce failing: no worker acquires the lock or calls tick.
        status = self.engine.call('getStatus')
        self.assertEqual(status['state'], 'idle')
        self.assertIsNone(status['lastRun'])
        self.assertEqual(self.calls, [])
        self.clock += timedelta(minutes=10)
        self.engine.call('runNow')
        self.assertEqual(self.engine.settings()['manualRequested'], requested)
        self.assertEqual(self.engine.tick()['classified'], 1)
        self.assertIsNone(self.engine.tick())
        self.assertEqual(len(self.engine.records()), 1)
        self.assertEqual(len(self.calls), 1)

    def test_stale_manual_request_survives_reopen_and_runs_only_one_budget(self):
        self.mail('FIRST')
        self.mail('SECOND')
        self.configure(enabled=False, maxMessagesPerRun=1)
        self.engine.call('runNow')
        requested = self.engine.settings()['manualRequested']
        self.engine.close()
        self.engine = MailAutomation(self.service)
        self.clock += timedelta(days=1)
        self.assertEqual(self.engine.call('getStatus')['state'], 'idle')
        self.assertEqual(self.engine.settings()['manualRequested'], requested)
        self.engine.call('runNow')
        self.assertEqual(self.engine.settings()['manualRequested'], requested)
        self.assertEqual(self.engine.tick()['classified'], 1)
        self.assertIsNone(self.engine.tick())
        self.assertEqual(len(self.engine.records()), 1)
        self.assertEqual(len(self.calls), 1)

    def test_long_message_goes_review_without_silent_truncation_or_ai(self):
        value = self.mail('NORMAL')
        value['body'] = '长' * 15000
        self.configure()
        self.manual()
        self.assertEqual(self.results()['items'][0]['category'], 'review')
        self.assertEqual(self.calls, [])
        with self.assertRaises(ValueError):
            self.engine.call('correctCategory', self.results()['items'][0]['localId'], 'action')

    def test_run_now_during_active_run_does_not_queue_another_budget(self):
        self.mail('NORMAL')
        self.configure()
        observed = []
        self.model_hook = lambda: observed.append(self.engine.call('runNow'))
        run = self.manual()
        self.assertEqual(run['classified'], 1)
        self.assertEqual(observed, [{'queued': True}])
        self.assertIsNone(self.engine.settings()['manualRequested'])
        self.assertIsNone(self.engine.tick())
        self.assertEqual(len(self.engine.records()), 1)

    def test_manual_run_with_disabled_schedule_retains_all_bounded_retries(self):
        self.mail('BAD')
        self.invalid.add('BAD')
        self.configure(enabled=False)
        self.assertEqual(self.manual()['state'], 'failed')
        for minutes in (5, 30):
            self.clock += timedelta(minutes=minutes)
            self.assertEqual(self.engine.tick()['state'], 'failed')
        self.assertEqual(self.calls.count((True, 'BAD')), 3)
        self.assertEqual(len(self.engine.records()), 1)
        self.clock += timedelta(hours=1)
        self.assertIsNone(self.engine.tick())

    def test_existing_complete_confirmed_analysis_is_reused_without_new_drafts(self):
        self.mail('ACTION')
        self.service.mail.import_mail(dict(bindingIds=[self.binding], onlyNew=True,
            filter={**deepcopy(DEFAULT_FILTER),
                    'dateMode': 'range', 'since': '2026-11-01', 'until': '2026-11-30'}))
        local = self.service.mail.store.list_local(dict(bindingIds=[self.binding]))['messages'][0]
        previous = self.service.mail.analyze(dict(messageIds=[local['localId']], instructions='提取合成任务',
            target=dict(baseUrl='https://fixture.invalid/v1', model='fixture')))
        self.assertEqual(self.service.mail.save_analysis(previous['id'], previous['drafts'])['count'], 1)
        saved = self.service.mail.store.get_analysis(previous['id'])
        self.calls.clear()
        self.configure()
        self.manual()
        item = self.results()['items'][0]
        self.assertEqual(item['analysisId'], previous['id'])
        self.assertEqual(self.calls, [(True, 'ACTION')])
        self.assertEqual(self.service.mail.store.get_analysis(previous['id']), saved)
        self.assertEqual(self.service.mail.save_analysis(previous['id'], previous['drafts'])['count'], 0)
        self.assertEqual(len(self.service.call('listTodos')), 1)

    def test_deleted_local_messages_are_hidden_and_counts_match(self):
        self.mail('NORMAL')
        self.configure()
        self.manual()
        local_id = self.results()['items'][0]['localId']
        self.service.call('mail-workspace:deleteLocal', [local_id])
        result = self.results()
        self.assertEqual(result['items'], [])
        self.assertEqual(result['total'], 0)
        self.assertEqual(sum(result['counts'].values()), 0)

    def test_ai_configuration_change_stops_old_run_before_any_extraction(self):
        self.mail('ACTION')
        self.configure()
        self.model_hook = lambda: self.service.call('saveSettings', dict(
            baseUrl='https://fixture.invalid/v1', model='changed-model', apiKey=''))
        run = self.manual()
        self.assertEqual(run['state'], 'failed')
        self.assertIn('AI 配置', run['error'])
        self.assertEqual(self.calls, [(True, 'ACTION')])
        self.assertEqual(self.service.call('listTodos'), [])

    def test_source_change_during_ai_cannot_attach_stale_analysis(self):
        self.mail('ACTION')
        self.configure()
        def change_body():
            local_id = self.results()['items'][0]['localId']
            local = self.service.mail.store.get_local(local_id)
            self.service.mail.store.put_body(local_id, {**local, 'body': 'Changed facts'})
        self.model_hook = change_body
        self.assertEqual(self.manual()['state'], 'failed')
        item = self.results()['items'][0]
        self.assertIsNone(item['analysisId'])
        self.assertIsNone(self.service.mail.store.get_local(item['localId'])['analysisId'])
        self.assertEqual(self.calls, [(True, 'ACTION')])

    def test_crashed_run_at_final_attempt_is_not_stuck_running_forever(self):
        self.mail('BAD')
        self.invalid.add('BAD')
        self.configure()
        self.manual()
        run = self.engine.records()[0]
        run['state'] = 'running'
        run['work']['attempt'] = 3
        self.engine.save_run(run)
        self.assertIsNone(self.engine.tick())
        self.assertEqual(self.engine.call('getStatus')['state'], 'failed')
        self.assertIn('中断', self.engine.call('getStatus')['lastRun']['error'])

    def test_disable_commits_stopped_state_without_requiring_another_tick(self):
        self.mail('BAD')
        self.invalid.add('BAD')
        config = self.configure()
        self.manual()
        run = self.engine.records()[0]
        run['state'] = 'running'
        self.engine.save_run(run)
        # Simulate the host saving disabled then terminating the old process.
        self.engine.call('saveConfig', {**config, 'enabled': False})
        status = self.engine.call('getStatus')
        self.assertEqual(status['state'], 'failed')
        self.assertIn('停', status['lastRun']['error'])
        self.assertIsNone(self.engine.records()[0]['work']['retryAt'])
        self.assertEqual(self.engine.call('runNow'), {'queued': True})
        self.assertEqual(self.engine.call('getStatus')['state'], 'idle')

    def test_status_detects_dead_worker_but_keeps_live_locked_worker_running(self):
        self.mail('BAD')
        self.invalid.add('BAD')
        self.configure()
        self.manual()
        run = self.engine.records()[0]
        run['state'] = 'running'
        self.engine.save_run(run)
        with receipt_lock(Path(self.temp.name) / 'mail-automation-run.lock') as acquired:
            self.assertTrue(acquired)
            self.assertEqual(self.engine.call('getStatus')['state'], 'running')
        status = self.engine.call('getStatus')
        self.assertEqual(status['state'], 'failed')
        self.assertIn('中断', status['lastRun']['error'])
        self.engine.call('runNow')
        self.assertIsNone(self.engine.settings()['manualRequested'])
        self.assertIsNotNone(self.engine.records()[0]['work']['retryAt'])


if __name__ == '__main__':
    unittest.main()
