"""Offline MIME fixtures must preserve identity, attachments and time isolation."""
from copy import deepcopy
import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from task_backend.service import BackendService

HELPER = Path(__file__).resolve().parents[2] / 'scripts/scenario_mail_fixtures.py'
spec = importlib.util.spec_from_file_location('scenario_mail_fixtures', HELPER)
fixtures = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fixtures)


class ScenarioMailFixturesTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        (self.root / 'attachments').mkdir()
        (self.root / 'attachments/details.md').write_text('# Course plan\nRead chapter 4.\n', encoding='utf-8')
        profile = self.root / 'profile'
        profile.mkdir()
        self.no_network = patch('socket.socket.connect', side_effect=AssertionError('No fixture network'))
        self.no_network.start()
        self.addCleanup(self.no_network.stop)
        self.service = BackendService(profile)
        self.addCleanup(self.service.close)
        base = dict(to=[dict(name='学生', address='student@example.invalid')], body='请看附件。\n陈明',
                    subject='课程安排', attachments=[dict(name='details.md', description='ORACLE SECRET')])
        messages = [dict(base, messageKey='m1', sentAt='2026-11-10T09:00:00+08:00',
                         **{'from': dict(name='陈明', address='professor@example.invalid')},
                         messageId='<m1@example.invalid>'),
                    dict(base, messageKey='m2', sentAt='2026-11-11T09:00:00+08:00',
                         **{'from': dict(name='陈明', address='club@example.invalid')},
                         messageId='<m2@example.invalid>', attachments=[],
                         body='Forwarded message:\nFrom: professor@example.invalid\nLast year: chapter 1.'),
                    dict(base, messageKey='m3', sentAt='2026-11-12T09:00:00+08:00',
                         **{'from': dict(name='Ming Chen', address='professor@example.invalid')},
                         messageId='<m3@example.invalid>', inReplyTo='<m1@example.invalid>',
                         references=['<m1@example.invalid>'], attachments=[])]
        self.scenario = dict(scenarioId='identity-course', metadata=dict(fictional=True), messages=messages,
                             oracle='ORACLE SECRET', checkpoints=[dict(checkpointId='early', throughMessageKey='m2')])

    def test_mime_roundtrip_retains_rfc_identity_threads_and_binary_attachment(self):
        raw = fixtures.make_eml(self.scenario['messages'][0], self.root)
        self.assertEqual(raw, fixtures.make_eml(self.scenario['messages'][0], self.root))
        self.assertNotIn(b'ORACLE SECRET', raw)
        item, payloads = fixtures.parse_eml(raw, 'remote')
        self.assertEqual(item['fromAddress'], 'professor@example.invalid')
        self.assertEqual(item['fromName'], '陈明')
        self.assertEqual(item['receivedAt'], '2026-11-10T01:00:00Z')
        self.assertEqual(payloads[0], (self.root / 'attachments/details.md').read_bytes())
        raw = fixtures.make_eml(self.scenario['messages'][2], self.root)
        reply, _ = fixtures.parse_eml(raw, 'reply')
        self.assertEqual(reply['inReplyTo'], '<m1@example.invalid>')
        self.assertEqual(reply['references'], ['<m1@example.invalid>'])

    def test_real_store_prefix_isolation_and_incremental_replay(self):
        early = fixtures.seed(self.service, self.scenario, self.root, checkpoint='early')
        self.assertEqual(early['visibleMessageKeys'], ['m1', 'm2'])
        self.assertEqual(early['addedMessageKeys'], ['m1', 'm2'])
        self.assertEqual(self.service.mail.store.search_agent_mail(sender='professor@example.invalid')['total'], 1)
        forward = self.service.mail.store.get_local(early['mailIds']['m2'])
        self.assertEqual(forward['fromAddress'], 'club@example.invalid')
        record = early['files']['details.md']
        self.assertEqual(record['sourceMailId'], early['mailIds']['m1'])
        again = fixtures.seed(self.service, self.scenario, self.root, checkpoint='early')
        self.assertEqual(again['addedMessageKeys'], [])
        self.assertEqual(again['files']['details.md']['id'], record['id'])
        full = fixtures.seed(self.service, self.scenario, self.root)
        self.assertEqual(full['addedMessageKeys'], ['m3'])
        with self.assertRaisesRegex(ValueError, 'Future messages'):
            fixtures.seed(self.service, self.scenario, self.root, checkpoint='early')
        with self.assertRaisesRegex(ValueError, 'Future messages'):
            fixtures.seed(self.service, self.scenario, self.root, message_keys=['m1', 'm3'])

    def test_lazy_download_real_import_and_real_disconnected_binding(self):
        seeded = fixtures.seed(self.service, self.scenario, self.root, cutoff='m1',
                               register_binding=True, import_attachments=False)
        self.assertEqual(seeded['files'], {})
        self.assertEqual(self.service.mail.manager.entry(seeded['bindingId'])['method'], 'imap')
        self.assertIsNone(self.service.mail.manager.get(seeded['bindingId'])['state']['account'])
        original_reader = self.service.mail.manager.reader
        with fixtures.fixture_transport(self.service, seeded):
            imported = self.service.files.import_attachment(seeded['mailIds']['m1'], 0)
            self.assertEqual(self.service.files.bytes(imported['id']), (self.root / 'attachments/details.md').read_bytes())
            self.assertEqual(imported['source'], 'mail')
            with self.assertRaisesRegex(ValueError, 'identity'):
                self.service.mail.manager.check_identity(seeded['bindingId'], 'different')
        self.assertEqual(self.service.mail.manager.reader, original_reader)
        replay = fixtures.seed(self.service, self.scenario, self.root, cutoff='m1',
                               register_binding=True, import_attachments=False)
        self.assertEqual(replay['bindingId'], seeded['bindingId'])

    def test_fixture_cannot_send_real_identities_or_read_outside_its_root(self):
        message = deepcopy(self.scenario['messages'][0])
        message['from']['address'] = 'teacher@example.com'
        with self.assertRaisesRegex(ValueError, 'reserved'):
            fixtures.make_eml(message, self.root)
        message['from']['address'] = 'teacher@example.invalid'
        message['attachments'][0]['fixturePath'] = '../outside.md'
        with self.assertRaisesRegex(ValueError, 'missing|leaves'):
            fixtures.make_eml(message, self.root)
        message['attachments'][0]['fixturePath'] = 'attachments/details.md'
        message['attachments'][0]['name'] = '../details.md'
        with self.assertRaisesRegex(ValueError, 'plain filename'):
            fixtures.make_eml(message, self.root)


if __name__ == '__main__':
    unittest.main()
