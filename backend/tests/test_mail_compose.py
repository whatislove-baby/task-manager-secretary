"""Compose/SMTP handover tests: real persistence, synthetic model and SMTP only."""
import base64
from copy import deepcopy
from email import policy
from email.parser import BytesParser
import json
from pathlib import Path
import smtplib
import ssl
import tempfile
import threading
import unittest
from unittest.mock import patch

from cryptography.fernet import Fernet
from task_backend.mail_compose import MailCompose, connect_smtp
from task_backend.service import BackendService

CASES = json.loads((Path(__file__).resolve().parents[2] / 'examples/handover/compose_cases.json').read_text())


class FakeSMTP:
    def __init__(self, failure=None, entered=None, release=None):
        self.failure = failure
        self.entered = entered
        self.release = release
        self.payloads = []
        self.events = []

    def login(self, username, password):
        self.events.append('login')
        if self.failure == 'auth':
            raise smtplib.SMTPAuthenticationError(535, b'synthetic-secret-do-not-display')

    def mail(self, sender):
        self.events.append(('mail', sender))
        return 250, b'ok'

    def rcpt(self, recipient):
        self.events.append(('rcpt', recipient))
        return (550, b'no') if self.failure == 'recipient' else (250, b'ok')

    def data(self, raw):
        self.events.append('data')
        self.payloads.append(raw)
        if self.entered:
            self.entered.set()
            if not self.release.wait(10):
                raise AssertionError('test release timed out')
        if self.failure == 'uncertain':
            raise TimeoutError('synthetic-secret-do-not-display')
        if self.failure == 'reject':
            raise smtplib.SMTPDataError(554, b'synthetic-secret-do-not-display')
        return 250, b'queued'

    def close(self):
        self.events.append('close')
        if self.failure == 'close':
            raise OSError('quit lost after acceptance')


class MailComposeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        # Real encrypted account files, with an ephemeral test-only key; no OS Keychain.
        cipher = Fernet(Fernet.generate_key())
        for name, function in [('encrypt', lambda value:base64.b64encode(cipher.encrypt(value.encode())).decode()),
                               ('decrypt', lambda value:cipher.decrypt(base64.b64decode(value)).decode())]:
            patcher = patch('task_backend.vault.' + name, function)
            patcher.start(); self.addCleanup(patcher.stop)
        self.requests = []
        self.responses = []
        self.service = BackendService(self.directory, request=self.request, worker=True)
        self.addCleanup(self.service.close)
        self.config = dict(baseUrl='https://fixture.invalid/v1', model='fixture', apiKey='synthetic-ai-secret')
        self.service.settings.for_request = lambda:deepcopy(self.config)
        self.target = {k:self.config[k] for k in ('baseUrl', 'model')}
        self.binding = self.service.mail.manager.add('imap')['id']
        self.imap = dict(email='student@example.invalid', username='', host='imap.example.invalid', port=993, security='tls', password='synthetic-mail-secret')
        self.service.mail.manager.configure_imap(self.binding, self.imap)
        self.compose = self.service.compose
        self.compose.save_config(self.binding, dict(host='smtp.example.invalid', port=465, security='ssl'))
        self.smtp = FakeSMTP()
        self.connections = []
        self.compose.smtp_factory = self.connect

    def request(self, url, body, headers, limit, timeout):
        self.requests.append(json.loads(body))
        result, finish = self.responses.pop(0)
        return json.dumps(dict(choices=[dict(finish_reason=finish, message=dict(content=json.dumps(result, ensure_ascii=False)))])).encode()

    def connect(self, config):
        self.connections.append(deepcopy(config))
        return self.smtp

    def generation(self, name='new_meeting', **extra):
        case = CASES[name]
        self.responses.append((deepcopy(case['model_result']), case.get('finish_reason', 'stop')))
        return self.service.call('mail-compose:generate', {k:case[k] for k in ('instructions','subject','body')} | dict(target=self.target) | extra)

    def prepare(self, **changes):
        value = dict(bindingId=self.binding, to='teammate@example.com', subject='项目讨论', body='我们周日讨论，可以吗？')
        return self.service.call('mail-compose:prepare', value | changes)

    def source_mail(self, body_status='complete'):
        source = CASES['reply_reschedule']['source_mail']
        return self.service.mail.store.put(self.binding, 'synthetic-identity', self.imap['email'],
            dict(id='synthetic-remote', **source, preview='', unread=True, webUrl='', imported=False, bodyStatus=body_status))

    def test_attachment_snapshot_matches_preview_and_survives_source_change(self):
        original = self.directory / '选中的本地资料.txt'
        original.write_text('课程 Project 交接要求\n截止 2026-11-23', encoding='utf-8')
        original_bytes = original.read_bytes()
        record = self.service.files.import_local(str(original))
        receipt = self.prepare(attachmentIds=[record['id']])
        self.assertEqual(receipt['attachments'][0]['sha256'], record['sha256'])
        self.assertEqual(self.connections, [])
        original.write_text('之后的修改不能进入已确认的预览', encoding='utf-8')
        # The send uses its own durable byte snapshot, even if the file workspace
        # is now unavailable. File IO must not be repeated after confirmation.
        with patch.object(self.service.files, 'bytes', side_effect=AssertionError('no reread')):
            result = self.compose.send(receipt['id'])
        self.assertEqual(result['status'], 'sent')
        parsed = BytesParser(policy=policy.default).parsebytes(self.smtp.payloads[0])
        attached = list(parsed.iter_attachments())
        self.assertEqual(len(attached), 1)
        self.assertEqual(attached[0].get_filename(), '选中的本地资料.txt')
        # Attachments preserve the original bytes, including Windows CRLF.
        self.assertEqual(attached[0].get_payload(decode=True), original_bytes)
        self.compose.send(receipt['id'])
        self.assertEqual(len(self.smtp.payloads), 1)

    def test_corrupt_or_missing_attachment_snapshot_prevents_smtp_connection(self):
        original = self.directory / 'source.txt'; original.write_text('synthetic reference')
        record = self.service.files.import_local(str(original))
        for corruption in ('corrupt', 'missing'):
            receipt = self.prepare(attachmentIds=[record['id']])
            if corruption == 'corrupt':
                self.compose.db.execute('UPDATE compose_attachments SET payload=? WHERE receipt_id=?', (b'wrong',receipt['id']))
            else:
                self.compose.db.execute('DELETE FROM compose_attachments WHERE receipt_id=?',(receipt['id'],))
            self.assertEqual(self.compose.send(receipt['id'])['status'], 'failed')
        self.assertEqual(self.connections, [])

    def test_attachment_validation_rejects_duplicate_and_unknown_ids(self):
        original = self.directory / 'source.txt'; original.write_text('synthetic reference')
        record = self.service.files.import_local(str(original))
        for ids in ([record['id'],record['id']], ['../../secret'], [record['id']]*7):
            with self.assertRaises(ValueError):
                self.prepare(attachmentIds=ids)
        self.assertEqual(self.compose.list_recent(), [])

    def test_compose_reads_file_content_only_when_explicitly_selected(self):
        original = self.directory / 'source.txt'; original.write_text('SYNTHETIC-REFERENCE-ONLY-IF-SELECTED')
        record = self.service.files.import_local(str(original))
        self.generation()
        self.assertNotIn('SYNTHETIC-REFERENCE', json.dumps(self.requests[-1]))
        self.generation(fileIds=[record['id']])
        self.assertIn('SYNTHETIC-REFERENCE-ONLY-IF-SELECTED', json.dumps(self.requests[-1]))

    def test_new_meeting_requires_confirm_sends_utf8_mime_and_repeated_confirm_is_idempotent(self):
        draft = self.generation()
        self.assertEqual(draft, CASES['new_meeting']['model_result'])
        self.assertEqual(self.connections, [])
        receipt = self.prepare(**draft)
        self.assertEqual(receipt['status'], 'prepared')
        self.assertEqual(self.connections, [])
        result = self.service.call('mail-compose:send', receipt['id'])
        self.assertEqual(result['status'], 'sent')
        self.assertEqual(len(self.connections), 1)
        message = BytesParser(policy=policy.default).parsebytes(self.smtp.payloads[0])
        self.assertEqual(message['To'], 'teammate@example.com')
        self.assertEqual(message['From'], self.imap['email'])
        self.assertEqual(str(message['Subject']), draft['subject'])
        self.assertEqual(message['Message-ID'], receipt['messageId'])
        self.assertEqual(message.get_content().replace('\r\n', '\n').rstrip(), draft['body'].rstrip())
        self.assertNotIn(b'synthetic-mail-secret', self.smtp.payloads[0])
        again = self.service.call('mail-compose:send', receipt['id'])
        self.assertEqual(again, result)
        self.assertEqual(len(self.connections), 1)
        self.assertEqual(self.service.call('mail-compose:listRecent')[0], result)
        self.assertEqual(self.service.call('listTodos'), [])

    def test_reply_uses_untrusted_original_and_does_not_mutate_calendar_or_send(self):
        source = self.source_mail()
        result = self.generation('reply_reschedule', replyToLocalId=source['localId'])
        self.assertEqual(result, CASES['reply_reschedule']['model_result'])
        sent_context = json.loads(self.requests[-1]['messages'][1]['content'])
        self.assertEqual(sent_context['untrustedOriginalMail']['body'], CASES['reply_reschedule']['source_mail']['body'])
        self.assertIn('不可信', self.requests[-1]['messages'][0]['content'])
        self.assertEqual(self.connections, [])
        self.assertEqual(self.service.call('listTodos'), [])
        self.prepare(**result, replyToLocalId=source['localId'])
        self.assertEqual(self.connections, [])

    def test_truncated_json_is_rejected_even_if_json_is_parseable(self):
        with self.assertRaisesRegex(ValueError, '完整有效'):
            self.generation('truncated_response')
        self.assertEqual(self.compose.list_recent(), [])
        self.assertEqual(self.connections, [])

    def test_model_cannot_add_recipients_credentials_or_extra_fields(self):
        for field in ('to', 'password', 'apiKey'):
            self.responses.append((dict(subject='主题', body='正文', **{field:'attacker@example.invalid'}), 'stop'))
            with self.assertRaisesRegex(ValueError, '完整有效'):
                self.compose.generate(dict(instructions='写邮件', subject='', body='', target=self.target))
        self.assertEqual(self.connections, [])

    def test_address_and_header_injection_rejected_before_any_send(self):
        for address in ('a@example.com,b@example.com', 'Person <a@example.com>', 'a@example.com\r\nBcc: b@example.com', '\na@example.com', 'a..b@example.com', 'x@-bad.example'):
            with self.subTest(address=address), self.assertRaises(ValueError):
                self.prepare(to=address)
        for subject in ('hello\r\nBcc: x@example.com', '\nhello', '\x00hello'):
            with self.subTest(subject=subject), self.assertRaises(ValueError):
                self.prepare(subject=subject)
        self.assertEqual(self.compose.list_recent(), [])
        self.assertEqual(self.connections, [])

    def test_server_auth_and_explicit_data_rejection_are_failures_without_secret_leaks_or_retry(self):
        for failure in ('auth', 'recipient', 'reject'):
            with self.subTest(failure=failure):
                self.smtp = FakeSMTP(failure)
                receipt = self.prepare()
                result = self.compose.send(receipt['id'])
                self.assertEqual(result['status'], 'failed')
                self.assertNotIn('synthetic-secret', json.dumps(result))
                count = len(self.connections)
                self.assertEqual(self.compose.send(receipt['id']), result)
                self.assertEqual(len(self.connections), count)
        self.assertNotIn(b'synthetic-mail-secret', (self.directory / 'mail-compose.sqlite').read_bytes())

    def test_ambiguous_data_timeout_is_not_retried_and_survives_reopen(self):
        self.smtp = FakeSMTP('uncertain')
        receipt = self.prepare()
        result = self.compose.send(receipt['id'])
        self.assertEqual(result['status'], CASES['smtp_acceptance_uncertain']['expected']['status'])
        other = MailCompose(self.service, smtp_factory=self.connect)
        self.addCleanup(other.close)
        self.assertEqual(other.send(receipt['id']), result)
        self.assertEqual(len(self.smtp.payloads), 1)

    def test_lost_close_after_data_acceptance_remains_sent(self):
        self.smtp = FakeSMTP('close')
        self.assertEqual(self.compose.send(self.prepare()['id'])['status'], 'sent')

    def test_abandoned_sending_receipt_recovers_as_uncertain_on_reopen(self):
        receipt = self.prepare()
        receipt.update(status='sending', detail='simulated crash')
        self.compose.save_receipt(receipt)
        other = MailCompose(self.service, smtp_factory=self.connect)
        self.addCleanup(other.close)
        self.assertEqual(other.send(receipt['id'])['status'], 'uncertain')
        self.assertEqual(self.connections, [])

    def test_two_isolated_services_cannot_send_same_receipt_or_recover_active_send(self):
        receipt = self.prepare()
        entered = threading.Event(); release = threading.Event()
        smtp = FakeSMTP(entered=entered, release=release)
        errors = []; output = []
        def first_send():
            service = None
            try:
                service = BackendService(self.directory, worker=True)
                service.compose.smtp_factory = lambda config:smtp
                output.append(service.compose.send(receipt['id']))
            except BaseException as exc:
                errors.append(exc)
            finally:
                if service:service.close()
        thread = threading.Thread(target=first_send)
        thread.start()
        try:
            self.assertTrue(entered.wait(5), str(errors))
            other = BackendService(self.directory, worker=True)
            try:
                other.compose.smtp_factory = lambda config:self.fail('duplicate SMTP connection')
                self.assertEqual(other.compose.send(receipt['id'])['status'], 'sending')
                self.assertEqual(other.compose.list_recent()[0]['status'], 'sending')
            finally:
                other.close()
        finally:
            release.set(); thread.join(10)
        self.assertFalse(thread.is_alive())
        self.assertEqual(errors, [])
        self.assertEqual(output[0]['status'], 'sent')
        self.assertEqual(len(smtp.payloads), 1)

    def test_disconnect_and_changed_account_or_smtp_invalidate_preview(self):
        receipt = self.prepare()
        self.service.mail.manager.disconnect(self.binding)
        self.assertEqual(self.compose.send(receipt['id'])['status'], 'failed')
        self.service.mail.manager.configure_imap(self.binding, self.imap)
        receipt = self.prepare()
        self.service.mail.manager.configure_imap(self.binding, self.imap | dict(email='other@example.invalid', username='other@example.invalid'))
        self.assertFalse(self.compose.get_config(self.binding)['configured'])
        self.assertEqual(self.compose.send(receipt['id'])['status'], 'failed')
        self.service.mail.manager.configure_imap(self.binding, self.imap)
        receipt = self.prepare()
        self.compose.save_config(self.binding, dict(host='changed.example.invalid', port=587, security='starttls'))
        self.assertEqual(self.compose.send(receipt['id'])['status'], 'failed')
        self.assertEqual(self.connections, [])

    def test_microsoft_and_unconfigured_imap_do_not_offer_smtp(self):
        for method in ('microsoft', 'imap'):
            binding = self.service.mail.manager.add(method)['id']
            with self.assertRaises(ValueError):
                self.compose.get_config(binding)

    def test_partial_or_oversize_source_is_not_silently_used(self):
        source = self.source_mail('preview')
        with self.assertRaisesRegex(ValueError, '完整读取'):
            self.generation('reply_reschedule', replyToLocalId=source['localId'])
        with self.assertRaises(ValueError):
            self.compose.generate(dict(instructions='回复', subject='', body='x'*20001, target=self.target))
        self.assertEqual(self.requests, [])

    def test_smtp_security_rejects_plaintext_and_starttls_uses_verified_context(self):
        with self.assertRaises(ValueError):
            self.compose.save_config(self.binding, dict(host='smtp.example.invalid', port=25, security='plain'))
        events = []
        class Client:
            def ehlo(self):events.append('ehlo')
            def starttls(self, context):
                self_outer.assertEqual(context.verify_mode, ssl.CERT_REQUIRED)
                self_outer.assertTrue(context.check_hostname)
                events.append('starttls')
            def close(self):events.append('close')
        self_outer = self
        with patch('task_backend.mail_compose.smtplib.SMTP', return_value=Client()) as constructor:
            connect_smtp(dict(host='smtp.example.invalid', port=587, security='starttls'))
            self.assertEqual(constructor.call_args.kwargs['local_hostname'], '[127.0.0.1]')
            self.assertEqual(constructor.call_args.kwargs['timeout'], 20)
        self.assertEqual(events, ['ehlo', 'starttls', 'ehlo'])
        events.clear()
        with patch('task_backend.mail_compose.smtplib.SMTP_SSL', return_value=Client()) as constructor:
            connect_smtp(dict(host='smtp.example.invalid', port=465, security='ssl'))
            context = constructor.call_args.kwargs['context']
            self.assertEqual(context.verify_mode, ssl.CERT_REQUIRED)
            self.assertTrue(context.check_hostname)
            self.assertEqual(constructor.call_args.kwargs['local_hostname'], '[127.0.0.1]')
        self.assertEqual(events, ['ehlo'])


if __name__ == '__main__':
    unittest.main()
