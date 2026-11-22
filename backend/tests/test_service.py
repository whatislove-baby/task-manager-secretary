import json
from pathlib import Path
import tempfile
import unittest
from task_backend.service import BackendService


class ServiceTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.root=Path(self.temp.name)
        self.service=BackendService(self.root)

    def tearDown(self):
        self.service.close();self.temp.cleanup()

    def settings(self,**extra):
        return dict(baseUrl='https://example.com/v1',model='fixture',apiKey='test-private-marker',**extra)

    def test_settings_encrypts_key_and_clearing_preserves_endpoint(self):
        saved=self.service.call('saveSettings',self.settings())
        self.assertTrue(saved['hasApiKey'])
        self.assertNotIn('test-private-marker',(self.root/'settings.json').read_text())
        self.assertEqual(self.service.settings.for_request()['apiKey'],'test-private-marker')
        self.service.call('clearKey')
        self.assertFalse(self.service.call('getSettings')['hasApiKey'])
        self.assertEqual(self.service.call('getSettings')['model'],'fixture')

    def test_endpoint_change_requires_new_key_and_http_rejected(self):
        self.service.call('saveSettings',self.settings())
        for value in (dict(baseUrl='https://other.example/v1',model='fixture',apiKey=''),dict(baseUrl='http://example.com',model='fixture',apiKey='key')):
            with self.assertRaises(ValueError): self.service.call('saveSettings',value)
        self.assertEqual(self.service.call('getSettings')['baseUrl'],'https://example.com/v1')

    def test_ai_extract_validates_output_without_saving_and_confirmation_writes(self):
        requests=[]
        def request(url,body,headers,limit,timeout):
            requests.append((url,json.loads(body),headers))
            return json.dumps({'choices':[{'message':{'content':json.dumps({'drafts':[{'tempId':'a','parentTempId':None,'title':'行动'}],'notes':'summary'})}}]}).encode()
        self.service.request=request
        self.service.call('saveSettings',self.settings())
        result=self.service.call('extract','untrusted original')
        self.assertEqual(self.service.call('listTodos'),[])
        self.assertEqual(result['drafts'][0]['title'],'行动')
        self.assertEqual(requests[0][0],'https://example.com/v1/chat/completions')
        self.assertEqual(requests[0][1]['messages'][1]['content'],'untrusted original')
        self.assertEqual(self.service.call('saveDrafts',result['drafts'],result['confirmationToken'])['value'],1)

    def test_malformed_ai_output_never_saves(self):
        self.service.request=lambda *args: b'{"choices":[{"message":{"content":"not JSON"}}]}'
        self.service.call('saveSettings',self.settings())
        with self.assertRaisesRegex(ValueError,'不是有效的JSON文本'): self.service.call('extract','text')
        self.assertEqual(self.service.call('listTodos'),[])

    def test_deadline_contract_examples_validate_and_preserve_precision(self):
        from task_backend.tree import due_value
        requests=[]
        self.service.settings.for_request=lambda:self.settings()
        def request(url,body,*args):
            system=json.loads(body)['messages'][0]['content'];requests.append(system)
            # Validate the exact examples sent to the model against the real schema.
            examples=[json.loads(line) for line in system.splitlines() if line.startswith('{"mode":')]
            self.assertEqual(len(examples),4)
            for example in examples:due_value(example)
            drafts=[dict(tempId=str(i),parentTempId='0' if due['mode']=='inherit' else None,
                         title='Task '+str(i),due=due) for i,due in enumerate(examples)]
            return json.dumps({'choices':[{'finish_reason':'stop','message':{'content':json.dumps({'drafts':drafts,'notes':''})}}]}).encode()
        self.service.request=request
        result=self.service.call('extract','合成测试原文')
        dues=[v['due'] for v in result['drafts']]
        self.assertIn({'mode':'none'},dues)
        self.assertIn({'mode':'inherit'},dues)
        self.assertIn({'mode':'value','value':{'kind':'date','date':'2026-01-02'}},dues)
        self.assertIn({'mode':'value','value':{'kind':'minute','at':'2026-01-02T10:00:00.000Z','timeZone':'Asia/Hong_Kong'}},dues)
        self.assertEqual(len(requests),1)
        self.assertEqual(self.service.call('listTodos'),[])

    def test_parseable_but_invalid_draft_json_is_rejected_without_leaking_content(self):
        self.service.settings.for_request=lambda:self.settings()
        marker='private-model-content-must-not-appear'
        draft=dict(tempId='1',parentTempId=None,title=marker)
        invalid_dues=[
            {'mode':'date','date':'2026-11-22'},
            {'mode':'value','kind':'date','date':'2026-11-22'},
            {'mode':'none','value':{'kind':'date','date':'2026-11-22'}},
            {'mode':'value','value':{'kind':'date','date':'2026-02-30'}},
            {'mode':'value','value':{'kind':'minute','at':'2026-11-21T18:00:01+08:00','timeZone':'Asia/Hong_Kong'}},
            {'mode':'value','value':{'kind':'minute','at':'2026-11-21T18:00:00+08:00','timeZone':'invalid-zone'}},
        ]
        invalid_drafts=[{**draft,'due':due} for due in invalid_dues]+[
            {'tempId':'1','title':marker},
            {**draft,'tempId':1},
            {**draft,'unexpected':marker},
        ]
        for invalid in invalid_drafts:
            with self.subTest(draft=invalid):
                raw=json.dumps({'choices':[{'message':{'content':json.dumps({'drafts':[invalid],'notes':marker})}}]}).encode()
                self.service.request=lambda *args:raw
                with self.assertRaisesRegex(ValueError,'JSON可解析，但草稿字段不符合约定') as caught:
                    self.service.call('extract','合成测试原文')
                self.assertNotIn(marker,str(caught.exception))
                self.assertEqual(self.service.call('listTodos'),[])

    def test_invalid_response_envelope_has_distinct_safe_error(self):
        self.service.settings.for_request=lambda:self.settings()
        self.service.request=lambda *args:b'{"private-model-content":"secret"}'
        with self.assertRaisesRegex(ValueError,'响应JSON结构无效') as caught:
            self.service.call('extract','合成测试原文')
        self.assertNotIn('secret',str(caught.exception))
        self.assertEqual(self.service.call('listTodos'),[])

    def test_text_and_html_mail_import_are_plain_text(self):
        file=self.root/'input.txt';file.write_text('\ufeff中文',encoding='utf-8')
        self.assertEqual(self.service.call('importTextFile',str(file))['text'],'中文')
        file=self.root/'input.eml';file.write_bytes(b'Subject: fixture\r\nContent-Type: text/html; charset=utf-8\r\n\r\n<p>hello</p><script>bad()</script>')
        parsed=self.service.call('importTextFile',str(file))
        self.assertIn('hello',parsed['text']);self.assertNotIn('bad()',parsed['text'])

    def test_backup_file_roundtrip_and_malformed_file_does_not_save(self):
        file=self.root/'backup.json'
        self.assertTrue(self.service.call('exportBackupFile',str(file)))
        self.assertTrue(self.service.call('importBackupFile',str(file)))
        file.write_text('bad')
        with self.assertRaises(ValueError): self.service.call('importBackupFile',str(file))
