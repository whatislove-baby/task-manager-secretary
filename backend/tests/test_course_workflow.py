"""Course-project workflow against real Python services and temporary SQLite.

Only external boundaries are replaced: an IMAP protocol client, model HTTP
responses, and an ephemeral credential cipher. No real network or Keychain is
used. This does not establish live-provider/model accuracy, automatic deadline
updates from another email, or awareness of task edits in later AI requests.
"""
import base64
from copy import deepcopy
from datetime import datetime, timezone
from email.message import EmailMessage
from email.policy import SMTP
import json
from pathlib import Path
import re
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from cryptography.fernet import Fernet

from task_backend.mail_readers import ImapReader
from task_backend.mail_types import DEFAULT_FILTER
from task_backend.service import BackendService


SUBJECT = '5051 Project 2：提交与演示要求（合成测试）'
COURSE_BODY = '''这是一封合成课程通知，不是真实课程安排。
Project 2 由 5–6 人合作，提交 project2.zip，截止日期为 2026 年 11 月 23 日。
子任务一：完成 README 与示例输入；README 不超过 500 字，与项目共同提交。
子任务二：完成 6 分钟演示幻灯片，截止日期为 2026 年 11 月 22 日。
课堂展示安排在 2026 年 11 月 24 日，展示后有 2 分钟提问。
请安排一位队友独立运行项目，并记录一次交接问题。'''
INSTRUCTIONS = '按原邮件生成一个 Project 2 父任务及两个明确子任务，保留日期精度和原文依据；交接要求写入父任务备注。'
MODEL_RESULT = {
    'notes': '项目提交与幻灯片有不同截止日期；安排真人队友运行并记录交接问题。',
    'drafts': [
        dict(tempId='project', parentTempId=None, title='Project 2：课程项目',
             notes='5–6 人合作；提交 project2.zip；安排真人队友独立运行并记录交接问题。',
             due={'mode':'value', 'value':{'kind':'date', 'date':'2026-11-23'}},
             source='提交 project2.zip，截止日期为 2026 年 11 月 23 日。'),
        dict(tempId='readme', parentTempId='project', title='完成 README 与示例输入',
             notes='README 不超过 500 字。', due={'mode':'inherit'},
             source='子任务一：完成 README 与示例输入；README 不超过 500 字，与项目共同提交。'),
        dict(tempId='slides', parentTempId='project', title='完成 6 分钟演示幻灯片',
             notes='课堂展示为 6 分钟，随后 2 分钟提问。',
             due={'mode':'value', 'value':{'kind':'date', 'date':'2026-11-22'}},
             source='子任务二：完成 6 分钟演示幻灯片，截止日期为 2026 年 11 月 22 日。'),
    ],
}


class SyntheticImapClient:
    """Protocol-shaped data only; production ImapReader still parses/imports it."""
    def __init__(self):
        message = EmailMessage(policy=SMTP)
        message['From'] = 'teacher@example.invalid'
        message['To'] = 'student@example.invalid'
        message['Subject'] = SUBJECT
        message.set_content(COURSE_BODY, charset='utf-8')
        self.raw = message.as_bytes()
        self.fetches = []
        self.readonly_selections = []

    def login(self, username, password):
        if (username, password) != ('student@example.invalid', 'fixture-mail-secret'):
            raise AssertionError('Only synthetic credentials are expected')

    def logout(self):
        pass

    def list_folders(self):
        return [((), b'/', 'INBOX')]

    def select_folder(self, folder, readonly):
        if folder != 'INBOX' or readonly is not True:
            raise AssertionError('This import must remain read-only')
        self.readonly_selections.append(folder)
        return {b'UIDVALIDITY':71}

    def search(self, criteria):
        return [101]

    def fetch(self, uids, fields):
        self.fetches.append(tuple(fields))
        if uids != [101]:
            raise AssertionError('Unexpected fixture UID')
        if fields == ['UID']:
            return {101:{b'UID':101}}
        segment = re.fullmatch(r'BODY\.PEEK\[\]<([0-9]+)\.([0-9]+)>', fields[0])
        if segment:
            start, take = map(int, segment.groups())
            return {101:{f'BODY[]<{start}>'.encode():self.raw[start:start+take]}}
        return {101:{
            b'ENVELOPE':SimpleNamespace(subject=SUBJECT.encode(), from_=[
                SimpleNamespace(name=None, mailbox=b'teacher', host=b'example.invalid')]),
            b'INTERNALDATE':datetime(2026, 11, 20, 0, 0, tzinfo=timezone.utc),
            b'FLAGS':(), b'RFC822.SIZE':len(self.raw),
            b'BODYSTRUCTURE':(b'TEXT', b'PLAIN', (b'CHARSET', b'utf-8'), None, None, b'8BIT', len(self.raw)),
        }}


class CourseWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        cipher = Fernet(Fernet.generate_key())
        # Exercise encrypted account persistence without any OS Keychain access.
        for name, function in (
            ('encrypt', lambda value:base64.b64encode(cipher.encrypt(value.encode())).decode()),
            ('decrypt', lambda value:cipher.decrypt(base64.b64decode(value)).decode()),
        ):
            patcher = patch('task_backend.vault.' + name, function)
            patcher.start()
            self.addCleanup(patcher.stop)
        network_guard = patch('socket.create_connection', side_effect=AssertionError('Unexpected real network access'))
        network_guard.start()
        self.addCleanup(network_guard.stop)
        self.transport = SyntheticImapClient()
        self.requests = []
        self.responses = []
        self.config = dict(baseUrl='https://fixture.invalid/v1', model='fixture', apiKey='fixture-ai-secret')
        self.target = {key:self.config[key] for key in ('baseUrl', 'model')}
        self.service = None
        self.open_service()
        self.addCleanup(lambda:self.service.close() if self.service else None)

    def request(self, url, body, headers, limit, timeout):
        self.assertEqual(url, self.config['baseUrl'] + '/chat/completions')
        self.requests.append(json.loads(body))
        self.assertTrue(self.responses, 'Unexpected model request')
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return json.dumps({'choices':[{'finish_reason':'stop', 'message':{
            'content':json.dumps(response, ensure_ascii=False)}}]}).encode()

    def open_service(self):
        self.service = BackendService(self.root, request=self.request, worker=True)
        self.service.settings.for_request = lambda:dict(self.config)
        self.service.mail.manager.reader_factory = lambda manager, binding:ImapReader(
            manager, binding, client_factory=lambda *args, **kwargs:self.transport)

    def restart(self):
        self.service.close()
        self.service = None
        self.open_service()

    def run_job(self, method, *args):
        job = self.service.call('mail-operations:' + method, *args)
        self.assertTrue(self.service.operations.run_next())
        return self.service.call('mail-operations:getJob', job['id'])

    def test_course_project_survives_review_completion_restart_followup_and_network_failure(self):
        binding = self.service.call('mail-workspace:addAccount', 'imap')['id']
        self.service.call('mail-workspace:configureImap', binding, dict(
            email='student@example.invalid', username='', host='imap.example.invalid',
            port=993, security='tls', password='fixture-mail-secret'))
        self.service.call('mail-workspace:connect', binding)
        filters = dict(DEFAULT_FILTER, dateMode='range', since='2026-11-20', until='2026-11-20',
                       folderIds=['INBOX'], subject='5051 Project 2')
        imported = self.run_job('startImport', dict(bindingIds=[binding], filter=filters, onlyNew=True))
        self.assertEqual(imported['state'], 'completed', imported.get('error'))
        for field, expected in {'added':1, 'failed':0, 'bodyRead':1}.items():
            self.assertEqual(imported['report'][field], expected, field)
        self.assertTrue(self.transport.readonly_selections)
        self.assertTrue(any(fields[0].startswith('BODY.PEEK[]') for fields in self.transport.fetches))
        local = self.service.call('mail-workspace:listLocal', {})
        self.assertEqual(local['total'], 1)
        mail_id = local['messages'][0]['localId']
        original = self.service.call('mail-workspace:getLocal', mail_id)
        self.assertEqual(original['bodyStatus'], 'complete')
        decoded_body = original['body']
        self.assertEqual(decoded_body.replace('\r\n', '\n').rstrip('\n'), COURSE_BODY)

        self.responses.append(deepcopy(MODEL_RESULT))
        analyzed = self.run_job('startAnalysis', dict(
            messageIds=[mail_id], instructions=INSTRUCTIONS, target=self.target))
        self.assertEqual(analyzed['state'], 'completed', analyzed.get('error'))
        self.assertEqual(self.service.call('listTodos'), [])
        analysis_id = analyzed['analysisIds'][0]
        analysis = self.service.call('mail-workspace:getAnalysis', analysis_id)
        self.assertEqual(len(analysis['drafts']), 3)
        for draft in analysis['drafts']:
            self.assertEqual(draft['sourceMailIds'], [mail_id])
            self.assertIn(draft['source'], COURSE_BODY)
        self.assertEqual(self.service.call('mail-workspace:saveAnalysis', analysis_id, analysis['drafts']), {'count':3})
        tasks = {task['title']:task for task in self.service.call('listTodos')}
        parent, readme, slides = (tasks[draft['title']] for draft in MODEL_RESULT['drafts'])
        self.assertIsNone(parent['parentId'])
        self.assertEqual(readme['parentId'], parent['id'])
        self.assertEqual(slides['parentId'], parent['id'])
        self.assertEqual(parent['due'], MODEL_RESULT['drafts'][0]['due'])
        self.assertEqual(readme['due'], {'mode':'inherit'})
        self.assertEqual(slides['due'], MODEL_RESULT['drafts'][2]['due'])
        self.service.call('setCompleted', readme['id'], True, readme['revision'])
        saved_tasks = self.service.call('listTodos')
        by_id = {task['id']:task for task in saved_tasks}
        self.assertTrue(by_id[readme['id']]['completed'])
        self.assertFalse(by_id[slides['id']]['completed'])
        self.assertFalse(by_id[parent['id']]['completed'])
        self.assertIsNotNone(by_id[readme['id']]['completedAt'])
        saved_analysis = self.service.call('mail-workspace:getAnalysis', analysis_id)
        conversation_id = analyzed['conversationId']
        saved_conversation = self.service.call('mail-operations:getConversation', conversation_id)

        self.restart()
        self.assertEqual(self.service.call('listTodos'), saved_tasks)
        self.assertEqual(self.service.call('mail-workspace:getAnalysis', analysis_id), saved_analysis)
        self.assertEqual(self.service.call('mail-operations:getConversation', conversation_id), saved_conversation)
        self.assertEqual(self.service.call('mail-workspace:getLocal', mail_id)['body'], decoded_body)
        self.assertEqual(len(self.requests), 1, 'Restart must not repeat model analysis')
        self.assertEqual(self.service.call('mail-workspace:saveAnalysis', analysis_id, analysis['drafts']), {'count':0})
        self.assertEqual(self.service.call('listTodos'), saved_tasks)

        # The user explicitly states completion; the test does not pretend the
        # model automatically receives current checkbox state from the task DB.
        question = '我已手动完成 README 与示例输入。请按原通知复核幻灯片截止日期和展示时长，只说明，不新增待办。'
        answer = '按原通知，幻灯片截止日期为 2026 年 11 月 22 日；课堂展示 6 分钟，随后 2 分钟提问。'
        self.responses.append(dict(drafts=[], notes=answer))
        followed = self.run_job('continueConversation', conversation_id, question, self.target)
        self.assertEqual(followed['state'], 'completed', followed.get('error'))
        sent = self.requests[-1]['messages'][1]['content']
        self.assertIn(json.dumps(decoded_body, ensure_ascii=False), sent)
        self.assertIn(INSTRUCTIONS, sent)
        self.assertIn(question, sent)
        history = json.loads(sent.split('历史轮次：', 1)[1].split('\n当前用户要求：', 1)[0])
        self.assertEqual([turn['role'] for turn in history], ['user', 'assistant'])
        previous_drafts = json.loads(history[1]['content'].split('待办草稿：\n', 1)[1])
        self.assertEqual(previous_drafts, analysis['drafts'])
        follow_analysis_id = followed['analysisIds'][0]
        follow_analysis = self.service.call('mail-workspace:getAnalysis', follow_analysis_id)
        self.assertEqual(follow_analysis['drafts'], [])
        self.assertEqual(follow_analysis['notes'], answer)
        conversation_before_failure = self.service.call('mail-operations:getConversation', conversation_id)
        self.assertEqual([turn['role'] for turn in conversation_before_failure['turns']],
                         ['user', 'assistant', 'user', 'assistant'])
        self.assertEqual(self.service.call('listTodos'), saved_tasks)

        self.responses.extend(ValueError('网络通信失败：合成测试连接不可用') for _ in range(3))
        before_requests = len(self.requests)
        failed = self.run_job('continueConversation', conversation_id, '再确认一下交接需要记录什么。', self.target)
        self.assertEqual(failed['state'], 'failed')
        self.assertIn('网络通信失败', failed['error'])
        self.assertEqual(len(self.requests) - before_requests, 3)
        self.assertEqual(failed.get('analysisIds', []), [])
        self.assertEqual(self.service.call('listTodos'), saved_tasks)
        self.assertEqual(self.service.call('mail-workspace:getAnalysis', analysis_id), saved_analysis)
        self.assertEqual(self.service.call('mail-workspace:getAnalysis', follow_analysis_id), follow_analysis)
        after_failure = self.service.call('mail-operations:getConversation', conversation_id)
        self.assertEqual(after_failure['turns'][:-1], conversation_before_failure['turns'])
        self.assertEqual(after_failure['turns'][-1]['role'], 'user')
        self.assertEqual(after_failure['turns'][-1]['jobId'], failed['id'])
        self.assertEqual(self.service.call('mail-workspace:saveAnalysis', analysis_id, analysis['drafts']), {'count':0})
        self.assertEqual(self.service.call('listTodos'), saved_tasks)

        self.restart()
        self.assertEqual(self.service.call('mail-operations:getJob', failed['id'])['state'], 'failed')
        self.assertEqual(self.service.call('mail-operations:getConversation', conversation_id), after_failure)
        self.assertEqual(self.service.call('listTodos'), saved_tasks)
        self.assertEqual(len(self.requests), 5, 'Failure must not silently retry after restart')
        self.assertEqual(self.responses, [])
