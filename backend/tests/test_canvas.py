from datetime import datetime,timezone
from pathlib import Path
import tempfile
import unittest
import base64
import json
from unittest.mock import patch
from task_backend.canvas import parse_feed
from task_backend.service import BackendService

NOW=datetime(2026,11,12,12,tzinfo=timezone.utc)

def event(*lines): return '\r\n'.join(['BEGIN:VEVENT',*lines,'END:VEVENT'])
def feed(*events): return '\r\n'.join(['BEGIN:VCALENDAR','VERSION:2.0',*events,'END:VCALENDAR'])


class CanvasTests(unittest.TestCase):
    def test_precision_plain_text_and_stable_identity(self):
        raw=feed(event('UID:a','DTSTART:20261113T143000Z','SUMMARY:Essay','DESCRIPTION:<p>Read &amp; write</p><script>bad()</script>'),event('UID:b','DTSTART;VALUE=DATE:20261114','SUMMARY:All day'))
        result=parse_feed(raw,NOW)
        self.assertEqual(result['issues'],[])
        self.assertEqual(result['items'][0]['due']['value'],dict(kind='minute',at='2026-11-13T14:30:00.000Z',timeZone='UTC'))
        self.assertEqual(result['items'][0]['description'],'Read & write')
        self.assertEqual(result['items'][1]['due']['value'],dict(kind='date',date='2026-11-14'))
        self.assertEqual(result['items'][0]['key'],'["a"]')

    def test_recurring_exclusion_override_and_cancellation(self):
        raw=feed(event('UID:x','DTSTART:20261111T100000Z','RRULE:FREQ=DAILY;COUNT=5','EXDATE:20261112T100000Z','SUMMARY:Daily'),event('UID:x','RECURRENCE-ID:20261113T100000Z','DTSTART:20261113T120000Z','SUMMARY:Moved'),event('UID:x','RECURRENCE-ID:20261114T100000Z','STATUS:CANCELLED'))
        result=parse_feed(raw,NOW)
        self.assertEqual(len(result['items']),3)
        moved=next(v for v in result['items'] if v['title']=='Moved')
        self.assertIn('2026-11-13T10:00:00.000Z',moved['key'])
        self.assertEqual(moved['due']['value']['at'],'2026-11-13T12:00:00.000Z')

    def test_bad_event_isolated_and_dst_gap_rejected(self):
        raw=feed(event('UID:bad','DTSTART;TZID=America/New_York:20260308T023000','SUMMARY:Gap'),event('UID:good','DTSTART;VALUE=DATE:20261113','SUMMARY:Good'))
        result=parse_feed(raw,NOW)
        self.assertEqual(len(result['issues']),1)
        self.assertEqual(result['items'][0]['title'],'Good')
        with self.assertRaises(ValueError): parse_feed('<html>login</html>',NOW)

    def test_unknown_explicit_timezone_does_not_become_local_time(self):
        for line in ('DTSTART;TZID=Mars/Unknown:20261113T120000',
                     'DTSTART:20261113T120000Z\r\nRDATE;TZID=Mars/Unknown:20261114T120000',
                     'DTSTART:20261113T120000Z\r\nEXDATE;TZID=Mars/Unknown:20261114T120000',
                     'DTSTART:20261113T120000Z\r\nRECURRENCE-ID;TZID=Mars/Unknown:20261114T120000'):
            with self.subTest(line=line):
                raw=feed(event('UID:bad',line,'SUMMARY:Invalid timezone'),event('UID:good','DTSTART;VALUE=DATE:20261113','SUMMARY:Good'))
                result=parse_feed(raw,NOW)
                self.assertEqual([v['title'] for v in result['items']],['Good'])
                self.assertEqual(len(result['issues']),1)
                self.assertIn('无法识别的时区',result['issues'][0]['reason'])

    def test_embedded_custom_timezone_and_iana_recurrence_remain_supported(self):
        custom='\r\n'.join(['BEGIN:VTIMEZONE','TZID:Course/Fixed-Plus8',
            'BEGIN:STANDARD','DTSTART:19700101T000000','TZOFFSETFROM:+0800',
            'TZOFFSETTO:+0800','END:STANDARD','END:VTIMEZONE'])
        raw=feed(custom,event('UID:custom','DTSTART;TZID=Course/Fixed-Plus8:20261113T120000',
            'RRULE:FREQ=DAILY;COUNT=2','SUMMARY:Custom'),
            event('UID:iana','DTSTART;TZID=Asia/Hong_Kong:20261113T120000',
            'RRULE:FREQ=DAILY;COUNT=2','EXDATE;TZID=Asia/Hong_Kong:20261114T120000','SUMMARY:IANA'))
        result=parse_feed(raw,NOW)
        self.assertEqual(result['issues'],[])
        self.assertEqual(len(result['items']),3)
        self.assertEqual(result['items'][0]['due']['value']['at'],'2026-11-13T04:00:00.000Z')
        self.assertEqual(result['items'][-1]['due']['value']['timeZone'],'Asia/Hong_Kong')

    def test_source_isolation_after_clear_and_same_host_restore(self):
        # This regression tests source identity, not platform credential storage.
        with tempfile.TemporaryDirectory() as folder, \
                patch('task_backend.canvas.vault.encrypt',lambda v:base64.b64encode(v.encode()).decode()), \
                patch('task_backend.canvas.vault.decrypt',lambda v:base64.b64decode(v).decode()):
            service=BackendService(Path(folder));service.clock=lambda:NOW
            raw=feed(event('UID:assignment-123','DTSTART;VALUE=DATE:20261113','SUMMARY:School A'))
            service.request=lambda *args:raw.encode()
            try:
                service.call('saveCanvasFeed','https://a.example/private-token.ics')
                self.assertEqual(service.call('syncCanvas')['added'],1)
                original_id=service.canvas.read()['sourceId']
                service.call('clearCanvasFeed')
                service.call('saveCanvasFeed','https://b.example/private-token.ics')
                raw=raw.replace('SUMMARY:School A','SUMMARY:School B')
                self.assertEqual(service.call('syncCanvas')['added'],1)
                self.assertNotEqual(service.canvas.read()['sourceId'],original_id)
                self.assertEqual({v['title'] for v in service.call('listTodos')},{'School A','School B'})
                service.call('clearCanvasFeed')
                service.call('saveCanvasFeed','https://a.example/rotated-token.ics')
                self.assertEqual(service.canvas.read()['sourceId'],original_id)
                raw=raw.replace('SUMMARY:School B','SUMMARY:School A updated')
                self.assertEqual(service.call('syncCanvas')['updated'],1)
                self.assertEqual(len(service.call('listTodos')),2)
                self.assertNotIn('token.ics',service.canvas.sources_file.read_text())
            finally: service.close()

    def test_unidentified_legacy_database_source_is_not_reused(self):
        with tempfile.TemporaryDirectory() as folder, \
                patch('task_backend.canvas.vault.encrypt',lambda v:base64.b64encode(v.encode()).decode()), \
                patch('task_backend.canvas.vault.decrypt',lambda v:base64.b64decode(v).decode()):
            service=BackendService(Path(folder));service.clock=lambda:NOW
            raw=feed(event('UID:a','DTSTART;VALUE=DATE:20261113','SUMMARY:Imported task'))
            service.request=lambda *args:raw.encode()
            try:
                service.call('saveCanvasFeed','https://a.example/private.ics');service.call('syncCanvas')
                original_id=service.canvas.read()['sourceId']
                # Old versions/backups may retain task mappings but no source host.
                service.canvas.file.unlink();service.canvas.sources_file.unlink()
                service.call('saveCanvasFeed','https://b.example/private.ics')
                self.assertNotEqual(service.canvas.read()['sourceId'],original_id)
                self.assertEqual(service.call('syncCanvas')['added'],1)
            finally: service.close()

    def test_handover_fixtures_import_update_and_failure_keep_local_work(self):
        fixtures=Path(__file__).resolve().parents[2]/'examples'/'handover'
        expected=json.loads((fixtures/'canvas-expected.json').read_text())
        with tempfile.TemporaryDirectory() as folder, \
                patch('task_backend.canvas.vault.encrypt',lambda v:base64.b64encode(v.encode()).decode()), \
                patch('task_backend.canvas.vault.decrypt',lambda v:base64.b64decode(v).decode()):
            service=BackendService(Path(folder))
            service.clock=lambda:datetime.fromisoformat(expected['clock'].replace('Z','+00:00'))
            original_id=None
            try:
                service.call('saveCanvasFeed','https://school.example.invalid/synthetic-calendar.ics')
                for step in expected['steps']:
                    with self.subTest(input=step['input']):
                        raw=(fixtures/step['input']).read_bytes()
                        service.request=lambda *args:raw
                        result=service.call('syncCanvas')
                        self.assertEqual(result['added'],step['added'])
                        self.assertEqual(result['updated'],step['updated'])
                        self.assertEqual(len(result['issues']),step['issues'])
                        tasks=service.call('listTodos');self.assertEqual(len(tasks),1)
                        task=tasks[0]
                        self.assertEqual(task['due']['value']['at'],step['dueAt'])
                        if original_id is None:
                            original_id=task['id']
                            service.call('editTodo',task['id'],{'notes':'My handover note'},True,task['revision'])
                        else:
                            self.assertEqual(task['id'],original_id)
                            self.assertEqual(task['notes'],'My handover note')
                            self.assertTrue(task['completed'])
            finally: service.close()

    def test_sync_is_idempotent_preserves_local_state_and_skips_deleted(self):
        with tempfile.TemporaryDirectory() as folder:
            service=BackendService(Path(folder));service.clock=lambda:NOW
            raw=feed(event('UID:a','DTSTART;VALUE=DATE:20261113','SUMMARY:Course','DESCRIPTION:full description'))
            service.request=lambda *args:raw.encode()
            try:
                service.call('saveCanvasFeed','https://school.example/private.ics')
                self.assertNotIn('private.ics',(Path(folder)/'canvas-feed.json').read_text())
                self.assertEqual(service.call('syncCanvas')['added'],1)
                self.assertEqual(service.call('syncCanvas')['unchanged'],1)
                root=service.call('listTodos')[0]
                service.call('editTodo',root['id'],{'notes':'personal'},True,root['revision'])
                raw=raw.replace('SUMMARY:Course','SUMMARY:Updated')
                self.assertEqual(service.call('syncCanvas')['updated'],1)
                changed=service.call('listTodos')[0]
                self.assertTrue(changed['completed']);self.assertEqual(changed['notes'],'personal')
                service.call('trashTodo',changed['id'],changed['revision'])
                self.assertEqual(service.call('syncCanvas')['skipped'],1)
                self.assertEqual(service.call('listTodos'),[])
            finally: service.close()
