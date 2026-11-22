"""Real import/SQLite regressions; only IMAP transport and credential OS boundary are synthetic."""
import base64
import importlib.util
import json
import ssl
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from cryptography.fernet import Fernet
from task_backend.mail_readers import ImapReader
from task_backend.service import BackendService

spec = importlib.util.spec_from_file_location('qa_mail_import_fixture', Path(__file__).resolve().parents[2] / 'scripts/qa-mail-import.py')
fixture = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fixture)


class Mailbox(fixture.ProtocolMailbox):
    def __init__(self, messages):
        super().__init__(messages)
        self.validity = 71
        self.body_reads = []
        self.on_body = None
        self.folders = ['INBOX']

    def list_folders(self):
        return [((), b'/', folder) for folder in self.folders]

    def select_folder(self, folder, readonly=True):
        if folder not in self.folders or readonly is not True:
            raise AssertionError('unexpected folder or writable import')
        return {b'UIDVALIDITY':self.validity}

    def fetch(self, uids, fields):
        result = super().fetch(uids, fields)
        if fields[0].startswith('BODY.PEEK[]<0.'):
            self.body_reads.extend(uids)
            if self.on_body:
                callback, self.on_body = self.on_body, None
                callback()
        return result


class ImapImportContinuationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        cipher = Fernet(Fernet.generate_key())
        for name, function in (
            ('encrypt', lambda value:base64.b64encode(cipher.encrypt(value.encode())).decode()),
            ('decrypt', lambda value:cipher.decrypt(base64.b64decode(value)).decode()),
        ):
            patcher = patch('task_backend.vault.' + name, function)
            patcher.start();self.addCleanup(patcher.stop)
        guard = patch('socket.create_connection', side_effect=AssertionError('No real network allowed'))
        guard.start();self.addCleanup(guard.stop)
        # Real TLS policy, reused only inside this synthetic transport test.
        # Loading the Windows certificate store 800+ times obscures the paging
        # assertions without exercising any additional TLS connection.
        self.tls_context=ssl.create_default_context()
        self.assertEqual(self.tls_context.verify_mode,ssl.CERT_REQUIRED)
        self.assertTrue(self.tls_context.check_hostname)
        self.enterContext(patch('task_backend.mail_readers.ssl.create_default_context',return_value=self.tls_context))
        self.mailboxes = {}
        self.legacy_readers = set()
        self.service = None
        self.open_service()
        self.addCleanup(lambda:self.service.close() if self.service else None)

    def open_service(self):
        self.service = BackendService(self.directory, request=lambda *args:self.fail('No HTTP or model calls expected'), worker=True)
        def factory(manager, binding):
            def client(*args,**kwargs):
                self.assertIs(kwargs['ssl_context'],self.tls_context)
                self.assertTrue(kwargs['ssl'])
                return self.mailboxes[binding]
            reader = ImapReader(manager, binding, client_factory=client)
            if binding in self.legacy_readers:
                return SimpleNamespace(read=reader.read, get_message=reader.get_message, test_connection=reader.test_connection)
            return reader
        self.service.mail.manager.reader_factory = factory

    def restart(self):
        self.service.close();self.service = None;self.open_service()

    def add_mailbox(self, count, label='student'):
        binding = self.service.call('mail-workspace:addAccount', 'imap')['id']
        messages = [fixture.wire_message(uid, f'Course {uid}', f'Synthetic course message {uid}.') for uid in range(1, count+1)]
        self.mailboxes[binding] = Mailbox(messages)
        self.configure(binding, label)
        return binding

    def configure(self, binding, label):
        self.service.call('mail-workspace:configureImap', binding, dict(email=label+'@example.invalid', username='',
                          host='imap.example.invalid', port=993, security='tls', password='fixture-only'))
        self.service.call('mail-workspace:connect', binding)

    def run_import(self, binding, background=True, only_new=True, filters=None):
        value = dict(bindingIds=[binding], filter=filters or fixture.FILTER, onlyNew=only_new)
        if not background:
            return self.service.call('mail-workspace:importMail', value)
        job = self.service.call('mail-operations:startImport', value)
        self.assertTrue(self.service.operations.run_next())
        job = self.service.call('mail-operations:getJob', job['id'])
        self.assertEqual(job['state'], 'completed', job.get('error'))
        self.assertEqual(job['done'], job['total'])
        return job['report']

    def local_mail(self, binding):
        return self.service.call('mail-workspace:listLocal', dict(bindingIds=[binding], limit=1000))

    def test_both_import_paths_continue_401_messages_across_restart_without_body_duplicates(self):
        for background in (False, True):
            with self.subTest(background=background):
                binding = self.add_mailbox(401, 'background' if background else 'sync')
                for index, (added, skipped, limited) in enumerate(((200, 0, True), (200, 200, True), (1, 400, False), (0, 401, False))):
                    report = self.run_import(binding, background)
                    self.assertEqual((report['added'], report['skipped'], report['bodyRead'], report['failed'], report['limited']),
                                     (added, skipped, added, 0, limited))
                    if index == 0:
                        self.restart()
                self.assertEqual(self.local_mail(binding)['total'], 401)
                self.assertEqual(len(self.mailboxes[binding].body_reads), 401)
                self.assertEqual(set(self.mailboxes[binding].body_reads), set(range(1, 402)))

    def test_partial_and_pending_are_retried_complete_unavailable_deleted_are_skipped(self):
        for background in (False, True):
            with self.subTest(background=background):
                binding = self.add_mailbox(5, 'states-bg' if background else 'states-sync')
                self.run_import(binding, background)
                messages = {json.loads(mail['id'])[2]:mail for mail in self.local_mail(binding)['messages']}
                for uid, status in ((4, 'unavailable'), (3, 'partial'), (2, 'pending')):
                    mail = self.service.call('mail-workspace:getLocal', messages[uid]['localId'])
                    self.service.mail.store.put_body(mail['localId'], {**mail, 'bodyStatus':status})
                self.service.call('mail-workspace:deleteLocal', [messages[1]['localId']])
                mailbox = self.mailboxes[binding];mailbox.body_reads.clear()
                report = self.run_import(binding, background)
                self.assertEqual((report['bodyRead'], report['failed']), (2, 0))
                # The synchronous legacy report also counts refreshed existing
                # rows as skipped; actual fetch IDs verify which bodies are read.
                self.assertGreaterEqual(report['skipped'], 3)
                self.assertEqual(set(mailbox.body_reads), {2, 3})
                self.assertEqual(self.local_mail(binding)['total'], 4)
                self.assertEqual(self.service.call('mail-workspace:getLocal', messages[4]['localId'])['bodyStatus'], 'unavailable')
                mailbox.body_reads.clear()
                explicit = self.run_import(binding, background, only_new=False)
                self.assertEqual((explicit['bodyRead'], explicit['failed']), (4, 0))
                self.assertEqual(set(mailbox.body_reads), {2, 3, 4, 5})
                self.assertEqual(self.local_mail(binding)['total'], 4, 'deleted mail must not be resurrected')

    def test_exclusions_are_scoped_to_uidvalidity_account_identity_and_binding(self):
        binding = self.add_mailbox(1)
        self.assertEqual(self.run_import(binding)['added'], 1)
        self.mailboxes[binding].validity = 72
        self.assertEqual(self.run_import(binding)['added'], 1)
        self.configure(binding, 'changed-account')
        self.assertEqual(self.run_import(binding)['added'], 1)
        self.assertEqual(self.local_mail(binding)['total'], 3)
        second = self.add_mailbox(1, 'changed-account')
        self.mailboxes[second].validity = 72
        self.assertEqual(self.run_import(second)['added'], 1)
        self.assertEqual(self.local_mail(second)['total'], 1)

    def test_identical_uids_in_different_folders_do_not_collide(self):
        binding = self.add_mailbox(1)
        self.mailboxes[binding].folders = ['INBOX', 'Archive']
        self.assertEqual(self.run_import(binding)['added'], 1)
        report = self.run_import(binding, filters=dict(fixture.FILTER, folderIds=['INBOX', 'Archive']))
        self.assertEqual((report['added'], report['skipped']), (1, 1))
        self.assertEqual(self.local_mail(binding)['total'], 2)

    def test_legacy_reader_signature_and_filtered_skip_counts_remain_compatible(self):
        binding = self.add_mailbox(3)
        self.legacy_readers.add(binding)
        self.assertEqual(self.run_import(binding)['added'], 3)
        self.assertEqual(self.run_import(binding, background=False)['skipped'], 3)
        self.legacy_readers.clear()
        before, found = self.service.mail.read_import_candidates(binding, dict(fixture.FILTER, subject='Course 2'))
        self.assertEqual(found['messages'], [])
        self.assertEqual(found['skippedKnown'], 1, 'do not count stored messages outside the active filter')
        self.assertEqual(before['identity'], self.service.mail.manager.identity(binding)['identity'])

    def test_pause_restart_continuation_does_not_count_exclusions_twice(self):
        binding = self.add_mailbox(201)
        self.assertEqual(self.run_import(binding, background=False)['added'], 200)
        job = self.service.call('mail-operations:startImport', dict(bindingIds=[binding], filter=fixture.FILTER, onlyNew=True))
        self.mailboxes[binding].on_body = lambda:self.service.call('mail-operations:pauseJob', job['id'])
        self.service.operations.run_next()
        paused = self.service.call('mail-operations:getJob', job['id'])
        self.assertEqual(paused['state'], 'paused')
        self.assertEqual((paused['done'], paused['total'], paused['report']['skipped']), (200, 201, 200))
        self.restart()
        self.service.call('mail-operations:resumeJob', job['id'])
        self.service.operations.run_next()
        resumed = self.service.call('mail-operations:getJob', job['id'])
        self.assertEqual(resumed['state'], 'completed', resumed.get('error'))
        self.assertEqual((resumed['done'], resumed['total']), (201, 201))
        self.assertEqual((resumed['report']['added'], resumed['report']['skipped'], resumed['report']['bodyRead']), (1, 200, 1))
        self.assertEqual(self.local_mail(binding)['total'], 201)
