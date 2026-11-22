import time
from pathlib import Path
import tempfile
import unittest
from task_backend.service import BackendService


class OperationTests(unittest.TestCase):
    def test_job_checkpoint_survives_restart_and_requires_explicit_resume(self):
        with tempfile.TemporaryDirectory() as folder:
            service=BackendService(Path(folder),worker=True)
            job=service.call('mail-operations:startImport',dict(bindingIds=['missing'],onlyNew=True))
            self.assertEqual(job['state'],'queued')
            service.call('mail-operations:pauseJob',job['id'])
            self.assertEqual(service.call('mail-operations:getJob',job['id'])['state'],'paused')
            service.close()
            service=BackendService(Path(folder),worker=True)
            try:
                self.assertEqual(service.call('mail-operations:getJob',job['id'])['state'],'paused')
                service.call('mail-operations:cancelJob',job['id'])
                self.assertEqual(service.call('mail-operations:getJob',job['id'])['state'],'canceled')
                service.call('mail-operations:acknowledgeJob',job['id'])
                self.assertTrue(service.call('mail-operations:getJob',job['id'])['notificationAcknowledged'])
            finally: service.close()

    def test_background_import_failure_report_is_persisted(self):
        with tempfile.TemporaryDirectory() as folder:
            service=BackendService(Path(folder))
            try:
                job=service.call('mail-operations:startImport',dict(bindingIds=['missing'],onlyNew=True))
                end=time.monotonic()+15
                while time.monotonic()<end:
                    current=service.call('mail-operations:getJob',job['id'])
                    if current['state'] in ('completed','failed'): break
                    time.sleep(.1)
                self.assertEqual(current['state'],'completed')
                self.assertEqual(current['report']['failed'],1)
                self.assertEqual(current['report']['mailboxErrors'][0]['bindingId'],'missing')
            finally: service.close()

class AnalysisCheckpointTests(unittest.TestCase):
    def test_invalid_deadline_stops_without_retry_and_resumes_remaining_mail_after_restart(self):
        import json
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);service=BackendService(root,worker=True)
            config=dict(baseUrl='https://fixture.invalid/v1',model='fixture',apiKey='synthetic')
            service.settings.for_request=lambda:dict(config)
            target={k:config[k] for k in ('baseUrl','model')}
            calls=[]
            dues={
                'C':{'mode':'none'},
                'B':{'mode':'value','value':{'kind':'date','date':'2026-11-22'}},
                'A':{'mode':'value','value':{'kind':'minute','at':'2026-11-21T18:00:00+08:00','timeZone':'Asia/Hong_Kong'}},
            }
            broken=True
            def request(url,body,*args):
                original=json.loads(body)['messages'][1]['content']
                label=next(label for label in dues if '主题：Test '+label+'\n' in original)
                calls.append(label)
                due={'mode':'value','kind':'date','date':'2026-11-22'} if broken and label=='B' else dues[label]
                result=dict(drafts=[dict(tempId='1',parentTempId=None,title='Task '+label,due=due)],notes='')
                return json.dumps({'choices':[{'finish_reason':'stop','message':{'content':json.dumps(result)}}]}).encode()
            service.request=request
            try:
                ids=[service.mail.store.put('binding','identity','student@example.invalid',dict(
                    id='remote-'+label,subject='Test '+label,sender='student@example.invalid',
                    receivedAt='2026-11-20T00:00:00Z',preview='',unread=True,webUrl='',imported=False,
                    body='Synthetic test task '+label))['localId'] for label in dues]
                job=service.call('mail-operations:startAnalysis',dict(messageIds=ids,instructions='',target=target))
                service.operations.run_next()
                failed=service.call('mail-operations:getJob',job['id'])
                self.assertEqual(failed['state'],'failed')
                self.assertEqual(failed['done'],1)
                self.assertEqual(failed['total'],3)
                self.assertIn('JSON可解析，但草稿字段不符合约定',failed['error'])
                self.assertEqual(calls,['C','B'])
                self.assertEqual(service.call('listTodos'),[])
                self.assertEqual([service.call('mail-workspace:getLocal',id)['status'] for id in ids],
                                 ['analyzed','unanalyzed','unanalyzed'])
                service.call('mail-operations:acknowledgeJob',job['id'])
                self.assertTrue(service.call('mail-operations:getJob',job['id'])['notificationAcknowledged'])
                service.close();service=BackendService(root,worker=True)
                service.settings.for_request=lambda:dict(config)
                service.request=request;broken=False
                self.assertTrue(service.call('mail-operations:getJob',job['id'])['notificationAcknowledged'])
                resumed=service.call('mail-operations:resumeJob',job['id'])
                self.assertEqual(resumed['state'],'queued')
                self.assertFalse(resumed['notificationAcknowledged'])
                self.assertFalse(service.call('mail-operations:getJob',job['id'])['notificationAcknowledged'])
                service.operations.run_next()
                done=service.call('mail-operations:getJob',job['id'])
                self.assertEqual(done['state'],'completed',done.get('error'))
                self.assertFalse(done['notificationAcknowledged'])
                self.assertEqual(done['done'],3)
                self.assertEqual(calls,['C','B','B','A'])
                self.assertEqual(done['analysisIds'][:1],failed['analysisIds'])
                self.assertEqual(len(set(done['analysisIds'])),3)
                self.assertEqual(service.call('listTodos'),[])
                for id in done['analysisIds']:
                    analysis=service.call('mail-workspace:getAnalysis',id)
                    for _ in range(2):service.call('mail-workspace:saveAnalysis',id,analysis['drafts'])
                todos={v['title']:v['due'] for v in service.call('listTodos')}
                self.assertEqual(len(todos),3)
                self.assertEqual(todos['Task C'],dues['C'])
                self.assertEqual(todos['Task B'],dues['B'])
                self.assertEqual(todos['Task A'],{'mode':'value','value':{
                    'kind':'minute','at':'2026-11-21T10:00:00.000Z','timeZone':'Asia/Hong_Kong'}})
            finally:service.close()

    def test_successful_segments_are_not_requested_again_after_pause_and_restart(self):
        import json
        from task_backend.operations import JobStop
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);service=BackendService(root,worker=True)
            service.call('saveSettings',dict(baseUrl='https://fixture.invalid/v1',model='fixture',apiKey='synthetic'))
            id=service.mail.store.put('binding','identity','user',dict(id='remote',subject='Long',sender='sender',receivedAt='2026-11-12T00:00:00Z',preview='',unread=True,webUrl='',imported=False,body='x'*21000))['localId']
            calls=[]
            answer=json.dumps({'choices':[{'message':{'content':json.dumps({'drafts':[dict(tempId='a',parentTempId=None,title='One task')],'notes':'summary'})}}]}).encode()
            def first(*args):
                calls.append(args)
                if len(calls)==2:raise JobStop('paused')
                return answer
            service.request=first
            job=service.call('mail-operations:startAnalysis',dict(messageIds=[id],instructions='',target=dict(baseUrl='https://fixture.invalid/v1',model='fixture')))
            service.operations.run_next()
            self.assertEqual(service.call('mail-operations:getJob',job['id'])['state'],'paused')
            self.assertEqual(service.call('mail-workspace:getLocal',id)['status'],'unanalyzed')
            service.close();service=BackendService(root,worker=True)
            resumed=[]
            service.request=lambda *args:(resumed.append(args) or answer)
            try:
                service.call('mail-operations:resumeJob',job['id']);service.operations.run_next()
                done=service.call('mail-operations:getJob',job['id'])
                self.assertEqual(done['state'],'completed');self.assertEqual(len(resumed),1)
                analysis=service.call('mail-workspace:getAnalysis',done['analysisIds'][0])
                self.assertEqual(len(analysis['drafts']),1)
                self.assertEqual(service.call('mail-workspace:getLocal',id)['status'],'analyzed')
            finally:service.close()
