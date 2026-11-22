"""Repeatable handover checks with synthetic mail and controlled model responses.

These exercise the real workspace, job processor, JSON validation and SQLite save
path. They deliberately do not test a live mailbox, OAuth or model quality.
"""
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest

from task_backend.service import BackendService


CASES=json.loads((Path(__file__).resolve().parents[2]/'examples/handover/mail_cases.json').read_text(encoding='utf-8'))


class MailHandoverTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.requests=[]
        self.results=[]
        self.service=BackendService(Path(self.temp.name),request=self.request,worker=True)
        config=dict(baseUrl='https://fixture.invalid/v1',model='fixture',apiKey='synthetic-only')
        self.service.settings.for_request=lambda:dict(config)
        self.target={k:config[k] for k in ('baseUrl','model')}
        self.addCleanup(self.temp.cleanup)
        self.addCleanup(self.service.close)

    def request(self,url,body,headers,limit,timeout):
        self.requests.append(json.loads(body))
        result,finish=self.results.pop(0)
        return json.dumps({'choices':[{'finish_reason':finish,'message':{'content':json.dumps(result)}}]}).encode()

    def respond(self,result,finish='stop'):
        self.results.append((deepcopy(result),finish))

    def mail(self,case,remote='remote-1'):
        return self.service.mail.store.put('binding','identity','student@example.invalid',dict(
            id=remote,subject=case['subject'],sender='teacher@example.invalid',receivedAt='2026-11-20T00:00:00Z',
            preview='',unread=True,webUrl='',imported=False,body=case['body']))

    def analyze(self,case):
        mail=self.mail(case)
        self.respond(case['model_result'],case.get('finish_reason','stop'))
        job=self.service.call('mail-operations:startAnalysis',dict(messageIds=[mail['localId']],instructions=case['instructions'],target=self.target))
        self.service.operations.run_next()
        return mail,self.service.call('mail-operations:getJob',job['id'])

    def follow_up(self,job,text):
        follow=self.service.call('mail-operations:continueConversation',job['conversationId'],text,self.target)
        self.service.operations.run_next()
        return self.service.call('mail-operations:getJob',follow['id'])

    def test_two_courses_same_title_and_date_survive_review_and_confirmation(self):
        case=CASES['distinct_tasks'];expected=case['expected']
        mail,job=self.analyze(case)
        self.assertEqual(job['state'],'completed',job.get('error'))
        repeated=self.mail(case)
        self.assertFalse(repeated['added']);self.assertEqual(mail['localId'],repeated['localId'])
        analysis=self.service.call('mail-workspace:getAnalysis',job['analysisIds'][0])
        self.assertEqual(len(analysis['drafts']),expected['draft_count'])
        self.assertIn('课程A',analysis['drafts'][0]['notes']);self.assertIn('课程B',analysis['drafts'][1]['notes'])
        self.assertEqual(len(self.service.call('listTodos')),expected['todos_before_confirmation'])
        for expected_count in (expected['first_confirmation_count'],expected['repeat_confirmation_count']):
            self.assertEqual(self.service.call('mail-workspace:saveAnalysis',analysis['id'],analysis['drafts'])['count'],expected_count)
        self.assertEqual(len(self.service.call('listTodos')),2)

    def test_follow_up_contains_original_mail_instructions_and_full_draft(self):
        case=CASES['follow_up'];mail,job=self.analyze(case)
        self.respond(case['follow_up_result'])
        follow=self.follow_up(job,case['question'])
        self.assertEqual(follow['state'],case['expected']['state'],follow.get('error'))
        sent=json.dumps(self.requests[-1],ensure_ascii=False)
        for expected in case['expected_request_contains']:self.assertIn(expected,sent)
        self.assertIn(r'\"due\"',self.requests[-1]['messages'][1]['content'])
        self.assertEqual(len(self.service.call('listTodos')),case['expected']['todos_before_confirmation'])
        conversation=self.service.call('mail-operations:getConversation',job['conversationId'])
        self.assertEqual([t['role'] for t in conversation['turns']],['user','assistant','user','assistant'])

    def test_old_conversation_recovers_initial_instruction_from_analysis(self):
        case=CASES['follow_up'];_,job=self.analyze(case)
        conversation=self.service.operations.conversation(job['conversationId'])
        conversation.pop('initialInstructions')
        conversation['turns']=conversation['turns'][1:]
        conversation['turns'][0]['content']='准备幻灯片'
        self.service.operations.save_conversation(conversation)
        self.respond(case['follow_up_result']);follow=self.follow_up(job,case['question'])
        self.assertEqual(follow['state'],'completed',follow.get('error'))
        sent=json.dumps(self.requests[-1],ensure_ascii=False)
        for expected in case['expected_request_contains']:self.assertIn(expected,sent)

    def test_truncated_valid_json_is_a_failure_without_saving_or_marking_analyzed(self):
        case=CASES['truncated_response'];expected=case['expected'];mail,job=self.analyze(case)
        self.assertEqual(job['state'],expected['state'])
        self.assertIn(expected['error_contains'],job['error'])
        self.assertEqual(len(self.service.call('listTodos')),expected['saved_todo_count'])
        self.assertEqual(self.service.call('mail-workspace:getLocal',mail['localId'])['status'],expected['mail_status'])
        self.assertEqual(job.get('analysisIds',[]),[])

    def test_same_response_identical_tasks_are_not_removed_but_segment_repeats_are(self):
        case=deepcopy(CASES['distinct_tasks']);case['body']='x'*16000
        first=case['model_result']['drafts'][0]
        second={**deepcopy(first),'tempId':'b'}
        case['model_result']['drafts']=[first,second]
        self.respond(case['model_result'])
        _,job=self.analyze(case)
        self.assertEqual(job['state'],'completed',job.get('error'))
        self.assertEqual(len(self.requests),2)
        analysis=self.service.call('mail-workspace:getAnalysis',job['analysisIds'][0])
        self.assertEqual(len(analysis['drafts']),2)

    def test_maximum_text_and_emoji_conversation_inputs_reach_model(self):
        for original in ('x'*20000,'😀'*10000):
            self.respond(dict(drafts=[],notes='已读取。'))
            job=self.service.call('mail-operations:startConversation',original,self.target)
            self.service.operations.run_next()
            done=self.service.call('mail-operations:getJob',job['id'])
            self.assertEqual(done['state'],'completed',done.get('error'))
            self.assertIn(original,self.requests[-1]['messages'][1]['content'])

    def test_large_mail_context_fails_explicitly_without_silently_omitting_original(self):
        case=deepcopy(CASES['follow_up']);case['body']='x'*25000
        self.respond(case['model_result']);_,job=self.analyze(case)
        self.assertEqual(job['state'],'completed',job.get('error'))
        before=len(self.requests);follow=self.follow_up(job,case['question'])
        self.assertEqual(follow['state'],'failed')
        self.assertIn('请减少所选邮件',follow['error'])
        self.assertEqual(len(self.requests),before)

    def test_direct_input_still_rejects_more_than_twenty_thousand_characters(self):
        with self.assertRaises(ValueError):self.service.call('extract','x'*20001)
        self.assertEqual(self.requests,[])
