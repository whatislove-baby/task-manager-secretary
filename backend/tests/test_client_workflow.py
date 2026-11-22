"""Supported client workflow through production services and temporary SQLite.

IMAP reads, model responses and SMTP are controlled boundary fixtures. This is
not a live account, UI interaction, model-quality test or student handover.
It does not assert missing cross-email revision/deduplication as correct behavior.
"""
import base64
from copy import deepcopy
from email import policy
from email.parser import BytesParser
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from cryptography.fernet import Fernet
from task_backend.mail_types import matches_filter
from task_backend.service import BackendService


CLIENT_REQUEST = {
    'id': 'synthetic-client-request-1',
    'subject': '【合成客户】项目北斗方案需求',
    'sender': 'client@example.invalid',
    'receivedAt': '2026-11-20T02:00:00Z',
    'preview': '请准备一页 PDF 方案，并回复确认已收到需求。',
    'body': '这是一封合成客户需求，不是真实业务。\n'
            '请为项目北斗准备一页 PDF 方案，截止时间为2026年11月23日16:00（香港时间）。\n'
            '请回复确认已收到需求；预算尚未确定，不要承诺报价。',
    'unread': True,
    'webUrl': '',
    'imported': False,
    'bodyStatus': 'complete',
    'attachments': [],
}
TASK_RESPONSE = {
    'drafts': [{
        'tempId': 'proposal', 'parentTempId': None,
        'title': '【合成客户】准备项目北斗方案',
        'notes': '整理为一页 PDF；预算尚未确定，不承诺报价。',
        'source': '请为项目北斗准备一页 PDF 方案，截止时间为2026年11月23日16:00（香港时间）。',
        'due': {'mode': 'value', 'value': {
            'kind': 'minute', 'at': '2026-11-23T08:00:00.000Z',
            'timeZone': 'Asia/Hong_Kong',
        }},
    }],
    'notes': '提取方案准备事项，回复内容另外由用户核对。',
}
REPLY_RESPONSE = {
    'subject': 'Re: 【合成客户】项目北斗方案需求',
    'body': '您好，已收到项目北斗的一页 PDF 方案需求。\n'
            '已记下2026年11月23日16:00（香港时间）的截止要求。预算仍待确认。谢谢！',
}


class SyntheticMailbox:
    def __init__(self):
        self.connection_tests = 0
        self.body_reads = []

    def test_connection(self):
        self.connection_tests += 1

    def read(self, filters):
        messages = [deepcopy(CLIENT_REQUEST)] if matches_filter(CLIENT_REQUEST, filters) else []
        for message in messages:
            message.pop('body')
        return {'messages': messages, 'limited': False}

    def get_message(self, remote_id):
        if remote_id != CLIENT_REQUEST['id']:
            raise AssertionError('Unexpected synthetic message requested')
        self.body_reads.append(remote_id)
        return deepcopy(CLIENT_REQUEST)


class SyntheticSMTP:
    def __init__(self):
        self.envelopes = []
        self.payloads = []

    def login(self, username, password):
        # Validate the seam without retaining credentials in test diagnostics.
        if username != 'intern@example.invalid' or password != 'synthetic-mail-secret':
            raise AssertionError('SMTP did not use the configured fixture account')

    def mail(self, sender):
        self.envelopes.append({'from': sender})
        return 250, b'ok'

    def rcpt(self, recipient):
        self.envelopes[-1]['to'] = recipient
        return 250, b'ok'

    def data(self, payload):
        self.payloads.append(payload)
        return 250, b'queued'

    def close(self):
        pass


class ClientWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        # Exercise encrypted configuration persistence with an ephemeral fixture
        # key; never access the user's OS Keychain, data or configured service.
        cipher = Fernet(Fernet.generate_key())
        replacements = {
            'encrypt': lambda value: base64.b64encode(cipher.encrypt(value.encode())).decode(),
            'decrypt': lambda value: cipher.decrypt(base64.b64decode(value)).decode(),
        }
        for name, function in replacements.items():
            patcher = patch('task_backend.vault.' + name, function)
            patcher.start()
            self.addCleanup(patcher.stop)
        network_guard = patch('socket.create_connection', side_effect=AssertionError('Real network forbidden in client fixture'))
        network_guard.start()
        self.addCleanup(network_guard.stop)
        self.requests = []
        self.responses = []
        self.mailbox = SyntheticMailbox()
        self.smtp = SyntheticSMTP()
        self.smtp_connections = 0
        self.service = None
        self.addCleanup(self.close_service)
        self.open_service()
        self.target = {'baseUrl': 'https://fixture.invalid/v1', 'model': 'controlled-client-fixture'}
        self.service.call('saveSettings', {**self.target, 'apiKey': 'synthetic-ai-secret'})
        self.binding = self.service.call('mail-workspace:addAccount', 'imap')['id']
        self.service.call('mail-workspace:configureImap', self.binding, {
            'email': 'intern@example.invalid', 'username': '', 'host': 'imap.example.invalid',
            'port': 993, 'security': 'tls', 'password': 'synthetic-mail-secret',
        })
        self.service.call('mail-workspace:connect', self.binding)
        self.service.call('mail-compose:saveConfig', self.binding, {
            'host': 'smtp.example.invalid', 'port': 465, 'security': 'ssl',
        })

    def open_service(self):
        self.service = BackendService(self.directory, request=self.request, worker=True)
        self.service.mail.manager.reader_factory = lambda manager, binding: self.mailbox
        self.service.compose.smtp_factory = self.connect_smtp

    def close_service(self):
        if self.service is not None:
            self.service.close()
            self.service = None

    def request(self, url, body, headers, limit, timeout):
        self.assertEqual(url, 'https://fixture.invalid/v1/chat/completions')
        self.assertTrue(self.responses, 'Unexpected extra model request')
        self.requests.append(json.loads(body))
        result = self.responses.pop(0)
        return json.dumps({'choices': [{
            'finish_reason': 'stop', 'message': {'content': json.dumps(result, ensure_ascii=False)},
        }]}).encode()

    def connect_smtp(self, config):
        self.assertEqual(config, {'host': 'smtp.example.invalid', 'port': 465, 'security': 'ssl'})
        self.smtp_connections += 1
        return self.smtp

    def finish_job(self, job):
        self.service.operations.run_next()
        completed = self.service.call('mail-operations:getJob', job['id'])
        self.assertEqual(completed['state'], 'completed', completed.get('error'))
        return completed

    def test_client_request_to_reviewed_reply_is_frozen_single_send_and_survives_restart(self):
        imported = self.finish_job(self.service.call('mail-operations:startImport', {
            'bindingIds': [self.binding], 'onlyNew': True,
        }))
        self.assertEqual({k: imported['report'][k] for k in ('added', 'failed', 'bodyRead')},
                         {'added': 1, 'failed': 0, 'bodyRead': 1})
        summary = self.service.call('mail-workspace:listLocal', {'bindingIds': [self.binding]})['messages'][0]
        local = self.service.call('mail-workspace:getLocal', summary['localId'])
        self.assertEqual(local['body'], CLIENT_REQUEST['body'])
        self.assertEqual(local['bodyStatus'], 'complete')
        self.assertEqual(self.requests, [])
        self.assertEqual(self.service.call('listTodos'), [])

        self.responses.append(deepcopy(TASK_RESPONSE))
        analyzed = self.finish_job(self.service.call('mail-operations:startAnalysis', {
            'messageIds': [local['localId']], 'instructions': '提取客户要求的方案准备事项，保留时区和预算未定的限制。',
            'target': self.target,
        }))
        analysis = self.service.call('mail-workspace:getAnalysis', analyzed['analysisIds'][0])
        self.assertEqual(len(analysis['drafts']), 1)
        self.assertEqual(self.service.call('listTodos'), [])  # Analysis alone cannot commit a task.
        self.assertEqual(self.service.call('mail-workspace:saveAnalysis', analysis['id'], analysis['drafts'])['count'], 1)
        tasks = deepcopy(self.service.call('listTodos'))
        self.assertEqual(len(tasks), 1)
        self.assertEqual(tasks[0]['due'], TASK_RESPONSE['drafts'][0]['due'])
        self.assertIn(local['localId'], tasks[0]['notes'])

        self.responses.append(deepcopy(REPLY_RESPONSE))
        draft = self.service.call('mail-compose:generate', {
            'instructions': '礼貌确认收到项目北斗需求，提及原截止时间，不承诺预算；只起草，不发送。',
            'subject': '', 'body': '', 'replyToLocalId': local['localId'], 'target': self.target,
        })
        context = json.loads(self.requests[-1]['messages'][1]['content'])
        self.assertEqual(context['untrustedOriginalMail']['body'], CLIENT_REQUEST['body'])
        self.assertEqual(context['untrustedOriginalMail']['sender'], CLIENT_REQUEST['sender'])
        self.assertIn('不可信', self.requests[-1]['messages'][0]['content'])
        self.assertEqual(draft, REPLY_RESPONSE)
        self.assertEqual(self.service.call('listTodos'), tasks)
        self.assertEqual(self.smtp_connections, 0)

        # Simulate the user's editable-review boundary. Sender/recipient are
        # supplied by the caller, never extracted from the model's response.
        edited_body = draft['body'] + '\n\n请另外提供品牌素材；收到后我会先核对资料完整性。'
        editable = {'bindingId': self.binding, 'to': 'client@example.invalid',
                    'subject': 'Re: 项目北斗需求与待补充资料', 'body': edited_body,
                    'replyToLocalId': local['localId']}
        preview = self.service.call('mail-compose:prepare', editable)
        self.assertEqual(preview['status'], 'prepared')
        self.assertEqual(preview['body'], edited_body)
        self.assertEqual(self.smtp_connections, 0)
        # A later renderer mutation must not alter the already-reviewed snapshot.
        editable.update(to='unreviewed@example.invalid', body='Unreviewed later editor changes')
        sent = self.service.call('mail-compose:send', preview['id'])
        self.assertEqual(sent['status'], 'sent')
        self.assertEqual(self.smtp_connections, 1)
        self.assertEqual(self.smtp.envelopes, [{'from': 'intern@example.invalid', 'to': 'client@example.invalid'}])
        message = BytesParser(policy=policy.default).parsebytes(self.smtp.payloads[0])
        self.assertEqual(message['To'], 'client@example.invalid')
        self.assertEqual(str(message['Subject']), preview['subject'])
        self.assertEqual(message.get_content().replace('\r\n', '\n').rstrip(), edited_body.rstrip())
        self.assertEqual(message['Message-ID'], preview['messageId'])
        self.assertNotIn(b'synthetic-mail-secret', self.smtp.payloads[0])
        self.assertEqual(self.service.call('mail-compose:send', preview['id']), sent)
        self.assertEqual(len(self.smtp.payloads), 1)
        self.assertEqual(self.service.call('listTodos'), tasks)

        self.close_service()
        self.open_service()
        self.assertEqual(self.service.call('listTodos'), tasks)
        self.assertEqual(self.service.call('mail-compose:listRecent'), [sent])
        self.assertEqual(self.service.call('mail-workspace:getLocal', local['localId'])['body'], CLIENT_REQUEST['body'])
        self.assertEqual(self.service.call('mail-compose:send', preview['id']), sent)
        self.assertEqual(self.smtp_connections, 1)
        self.assertEqual(len(self.smtp.payloads), 1)
        self.assertEqual(self.service.call('mail-workspace:saveAnalysis', analysis['id'], analysis['drafts'])['count'], 0)
        self.assertEqual(self.service.call('listTodos'), tasks)
        self.assertEqual(len(self.requests), 2)
        self.assertEqual(self.responses, [])


if __name__ == '__main__':
    unittest.main()
