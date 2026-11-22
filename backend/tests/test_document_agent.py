"""Real file stores and transformations; only the vision provider is synthetic."""
import asyncio
from io import BytesIO
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from types import SimpleNamespace

from task_backend.agent_tools import AgentTools, build_tools
from task_backend.service import BackendService
from task_backend.mail_files import make_pdf, make_docx


class DocumentAgentTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
        self.service=BackendService(self.root,worker=True)
        self.events=[];self.ctx=AgentTools(self.service,'document-tests','test-chat',self.events.append)
        self.addCleanup(self.tmp.cleanup);self.addCleanup(self.service.close);self.addCleanup(self.ctx.close)
        self.enterContext(patch('socket.socket.connect',side_effect=AssertionError('No network')))
        self.config=dict(apiKey='synthetic-key',baseUrl='https://example.invalid/v1',model='test/vision')
        self.service.settings.for_request=lambda:dict(self.config)

    def file(self,name,data):
        p=self.root/name;p.write_bytes(data);return self.service.files.import_local(p)

    def test_csv_transform_replay_validation_and_draft_exact_versions(self):
        source=self.file('hours.csv',b'id,group,hours\n001,A,1.1\n001,A,1.1\n002,A,2.2\n')
        clean=self.ctx.transform_csv(source['id'],'clean',[{'op':'drop_duplicates'}],call_id='clean')
        summary=self.ctx.transform_csv(clean['id'],'summary',[
            {'op':'groupby','by':['group'],'aggregations':[{'column':'hours','op':'sum','output':'total'}]}],call_id='sum')
        self.assertEqual(self.ctx.inspect_file(clean['id'])['structure']['rows'][0][0],'001')
        self.assertEqual(self.ctx.inspect_file(summary['id'])['structure']['rows'],[['A','3.3']])
        self.assertTrue(self.ctx.validate_file(summary['id'])['structuralPassed'])
        repeated=self.ctx.transform_csv(clean['id'],'summary',[
            {'op':'groupby','by':['group'],'aggregations':[{'column':'hours','op':'sum','output':'total'}]}],call_id='sum')
        self.assertEqual(summary,repeated);self.assertEqual(len(self.service.files.list_files()),3)
        self.ctx.db.execute('UPDATE agent_tool_results SET result=NULL WHERE id=?',(summary['id'],))
        self.assertEqual(self.ctx.transform_csv(clean['id'],'summary',[
            {'op':'groupby','by':['group'],'aggregations':[{'column':'hours','op':'sum','output':'total'}]}],call_id='sum')['id'],summary['id'])
        draft=self.ctx.create_mail_draft('Synthetic review','Please review.',to='training@example.invalid',
            attachment_ids=[clean['id'],summary['id']],call_id='draft')
        self.assertEqual(draft['attachmentIds'],[clean['id'],summary['id']])
        self.assertTrue(all(f['validation']['structuralPassed'] for f in draft['attachments']))
        self.assertEqual(self.service.compose.list_recent(),[])
        self.assertEqual(self.service.files.get(source['id'])['sha256'],source['sha256'])

    def test_word_targeted_edit_conflict_and_checkpoint(self):
        original=self.file('report.docx',make_docx('Report','Status pending'))
        structure=self.ctx.inspect_file(original['id'])['structure']
        target=next(p for p in structure['paragraphs'] if p['text']=='Status pending')
        with self.assertRaises(ValueError):
            self.ctx.edit_word(original['id'],'bad',[dict(target=target['id'],expected='stale',text='Complete')],call_id='bad')
        self.assertEqual(len(self.service.files.list_files()),1)
        args=dict(file_id=original['id'],name='reviewed',edits=[dict(target=target['id'],expected=target['text'],text='Complete')])
        new=self.ctx.edit_word(**args,call_id='edit');self.assertIn('Complete',self.service.files.read(new['id']))
        self.assertEqual(new,self.ctx.edit_word(**args,call_id='edit'))
        self.assertEqual(self.service.files.get(new['id'])['parentIds'],[original['id']])
        self.assertEqual(self.service.files.bytes(original['id']),(self.root/'report.docx').read_bytes())

    def test_vision_receives_actual_page_and_caches_observation_without_images_in_ui(self):
        file=self.file('scan.pdf',make_pdf('Visual test','Chart data only matters on the page'))
        calls=[]
        class Vision:
            async def ainvoke(_,messages):
                calls.append(messages)
                return SimpleNamespace(content='第 1 页显示 Visual test。',response_metadata={'finish_reason':'stop'})
        self.ctx.vision_model_factory=lambda config:Vision()
        first=asyncio.run(self.ctx.view_file(file['id'],[1],'Read title',call_id='view-1'))
        second=asyncio.run(self.ctx.view_file(file['id'],[1],'Read title',call_id='view-2'))
        self.assertEqual(first,second);self.assertEqual(len(calls),1)
        images=[p for p in calls[0][1].content if p['type']=='image_url']
        self.assertEqual(len(images),1);self.assertTrue(images[0]['image_url']['url'].startswith('data:image/png;base64,'))
        self.assertTrue(first['vision']);self.assertEqual(first['pages'],[1])
        self.assertNotIn('base64',str(self.events));self.assertNotIn('synthetic-key',str(self.events))
        audit=[event for event in self.events if event.get('type')=='model_call' and event.get('kind')=='vision']
        self.assertEqual([event['status'] for event in audit],['started','completed'])
        self.assertGreaterEqual(audit[-1]['elapsedSeconds'],0)
        self.assertNotIn('base64',str(self.ctx.db.execute("SELECT result FROM agent_tool_results WHERE operation='view_file'").fetchone()[0]))

    def test_vision_failure_does_not_fall_back_to_extracted_text_or_leak_provider_body(self):
        file=self.file('notes.pdf',make_pdf('Notes','Text extraction is possible'))
        class Broken:
            async def ainvoke(_,messages):raise ValueError('private response with synthetic-key')
        self.ctx.vision_model_factory=lambda config:Broken()
        result=asyncio.run(self.ctx.view_file(file['id'],[1],'Read',call_id='fail'))
        self.assertEqual(result['error'],'vision_failed');self.assertNotIn('synthetic-key',str(result)+str(self.events))
        self.assertNotIn('observation',result)
        self.assertEqual([e['status'] for e in self.events if e.get('type')=='model_call'],['started','failed'])
        self.assertEqual(self.ctx.db.execute("SELECT count(*) FROM agent_tool_results WHERE operation='view_file'").fetchone()[0],0)

    def test_wrong_visual_tool_arguments_return_recoverable_errors_before_model_call(self):
        csv=self.file('table.csv',b'id,hours\n001,3\n')
        self.ctx.vision_model_factory=lambda config:self.fail('Bad input must not reach the model')
        for file_id,pages in [(csv['id'],[0]),(csv['id'],[1]),('00000000-0000-4000-8000-000000000000',[1])]:
            with self.subTest(file_id=file_id,pages=pages):
                result=asyncio.run(self.ctx.view_file(file_id,pages,'Read',call_id='invalid'))
                self.assertEqual(result['error'],'vision_failed')
                self.assertNotIn('observation',result)
        self.assertTrue(all(e['status']=='failed' for e in self.events if e.get('tool')=='view_file'))
        # The same task can recover by choosing the appropriate CSV tool.
        self.assertEqual(self.ctx.inspect_file(csv['id'])['structure']['rows'],[['001','3']])

    def test_corrupt_output_cannot_be_attached_and_tool_surface_has_no_send_or_eval(self):
        created=self.ctx.create_csv('table',['id'],[['001']],call_id='new')
        (self.service.files.root/created['id']).write_bytes(b'corrupt')
        with self.assertRaises(ValueError):
            self.ctx.create_mail_draft('x','y',attachment_ids=[created['id']],call_id='must-not-save')
        names={t.name for t in build_tools(self.ctx)}
        self.assertTrue({'inspect_file','view_file','validate_file','edit_word','create_csv','transform_csv'}<=names)
        self.assertFalse({'send','send_mail','execute_python','shell'} & names)

    def test_unrenderable_generated_document_is_preserved_but_not_added_to_draft(self):
        document=self.ctx.create_document('report','pdf','Report','Needs checking',[],call_id='pdf')
        with patch.object(self.service.files,'validate',return_value=dict(structuralPassed=True,rendered=False)):
            with self.assertRaisesRegex(ValueError,'渲染'):
                self.ctx.create_mail_draft('Report','Review',attachment_ids=[document['id']],call_id='blocked')
        self.assertTrue(self.service.files.bytes(document['id']))
        self.assertEqual(self.ctx.list_mail_drafts(),[])

    def test_mail_attachment_import_supports_csv_word_and_scanned_pdf_with_retry(self):
        from pypdf import PdfWriter
        writer=PdfWriter();writer.add_blank_page(120,120);stream=BytesIO();writer.write(stream)
        inputs=[('hours.csv',b'id,hours\n001,2.5\n'),('brief.docx',make_docx('Brief','Keep this text')),('scan.pdf',stream.getvalue())]
        attachments=[dict(name=name,type='application/octet-stream',size=len(data),inline=False) for name,data in inputs]
        mid=self.service.mail.store.put('synthetic-binding','synthetic-identity','student@example.invalid',
            dict(id='three-attachments',subject='Synthetic attachment request',sender='training@example.invalid',
                 receivedAt='2026-11-21T01:00:00Z',body='Please process these three attachments.',preview='',
                 unread=True,webUrl='',attachments=attachments))['localId']
        calls=[]
        reader=SimpleNamespace(get_attachment=lambda remote,index,*args:(calls.append(index) or inputs[index][1]))
        with patch.object(self.service.mail.manager,'check_identity',return_value=None),patch.object(self.service.mail.manager,'reader',return_value=reader):
            for index in range(3):
                record=self.ctx.import_attachment(mid,index,call_id='attachment-'+str(index))['file']
                self.assertEqual(record['sourceMailId'],mid)
                self.assertEqual(self.service.files.bytes(record['id']),inputs[index][1])
                again=self.ctx.import_attachment(mid,index,call_id='attachment-'+str(index))['file']
                self.assertEqual(again['id'],record['id'])
        self.assertEqual(calls,[0,1,2])


if __name__=='__main__':unittest.main()
