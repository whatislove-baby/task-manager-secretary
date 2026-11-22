"""Real pypdf page selection, visual equivalence and managed delivery checks."""
from io import BytesIO
import base64
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from PIL import Image
from pypdf import PdfReader,PdfWriter
from pypdf.generic import RectangleObject
from reportlab.pdfgen import canvas
from reportlab.lib.utils import ImageReader
from task_backend.service import BackendService
from task_backend.agent_tools import AgentTools,build_tools
from task_backend.mail_files import selected_pdf_pages
from task_backend.document_render import render_page


def fixture_pdf():
    stream=BytesIO();c=canvas.Canvas(stream,pagesize=(300,400))
    image=Image.new('RGB',(60,40),'navy')
    for n in range(1,4):
        c.drawString(30,360,'Page '+str(n));c.drawImage(ImageReader(image),40,240,120,80)
        if n==2:c.acroForm.textfield(name='student_name',value='Test Student',x=30,y=160,width=200,height=25)
        c.showPage()
    c.save();image.close()
    reader=PdfReader(BytesIO(stream.getvalue()));writer=PdfWriter();writer.append(reader)
    writer.pages[2].rotate(90);writer.pages[2].cropbox=RectangleObject([20,20,280,380])
    output=BytesIO();writer.write(output);writer.close();reader.close();return output.getvalue()


class PdfPageSelectionTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.s=BackendService(Path(self.tmp.name),worker=True);self.addCleanup(self.s.close)
        self.ctx=AgentTools(self.s,'run','chat',lambda event:None);self.addCleanup(self.ctx.close)
        self.raw=fixture_pdf();self.file=self.s.files._save('original.pdf',self.raw,'local')
        self.enterContext(patch('socket.socket.connect',side_effect=AssertionError('No network')))

    def test_reordered_actual_pages_preserve_images_rotation_crop_and_pixels(self):
        result=self.ctx.execute('extract_pdf_pages',dict(file_id=self.file['id'],name='selected.pdf',pages=[3,1]),'selection')
        self.assertNotIn('error',result)
        output=self.s.files.bytes(result['id']);reader=PdfReader(BytesIO(output))
        self.assertEqual([p.extract_text().strip() for p in reader.pages],['Page 3','Page 1'])
        self.assertEqual(reader.pages[0].rotation,90)
        self.assertEqual(list(reader.pages[0].cropbox),[20,20,280,380])
        for old,new in [(3,1),(1,2)]:
            source=render_page(self.raw,old);selected=render_page(output,new)
            self.assertEqual((source['width'],source['height']),(selected['width'],selected['height']))
            self.assertEqual(source['dataUrl'],selected['dataUrl'])
        self.assertEqual(self.s.files.bytes(self.file['id']),self.raw)
        meta=result['file']['pageSelection']
        self.assertEqual(meta['pages'],[3,1]);self.assertEqual(meta['sourcePageCount'],3)
        self.assertEqual(meta['sourceSha256'],self.file['sha256'])
        self.assertEqual(result['file']['parentIds'],[self.file['id']])
        self.assertEqual(result['file']['validation']['pageCount'],2)
        self.assertTrue(result['file']['validation']['rendered'])

    def test_selected_form_fields_remain_available_and_can_be_attached(self):
        result=self.ctx.extract_pdf_pages(self.file['id'],'form-only',[2],call_id='form')
        fields=PdfReader(BytesIO(self.s.files.bytes(result['id']))).get_fields()
        self.assertEqual(fields['student_name']['/V'],'Test Student')
        draft=self.ctx.create_mail_draft('Requested form','Here is the selected page.',to='recipient@example.invalid',attachment_ids=[result['id']],call_id='reply')
        check=self.ctx.validate_mail_draft(draft['id'],[result['id']])
        self.assertTrue(check['passed']);self.assertEqual(check['actualAttachmentIds'],[result['id']])
        self.assertEqual(self.s.compose.db.execute('SELECT count(*) FROM compose_receipts').fetchone()[0],0)
        self.assertEqual(self.s.files.get(self.file['id']).get('lifecycleStatus','active'),'active')

    def test_replay_and_result_journal_recovery_do_not_duplicate(self):
        args=dict(file_id=self.file['id'],name='selected',pages=[1,3],call_id='stable')
        result=self.ctx.extract_pdf_pages(**args)
        self.assertEqual(result,self.ctx.extract_pdf_pages(**args))
        self.ctx.db.execute('UPDATE agent_tool_results SET result=NULL WHERE id=?',(result['id'],))
        replay=self.ctx.extract_pdf_pages(**args)
        self.assertEqual(replay['id'],result['id'])
        self.assertEqual(len(self.s.files.list_files()),2)

    def test_invalid_selections_fail_without_saved_output(self):
        for pages in [[],[0],[4],[1,1],[True],[1.0],['1'],list(range(1,82)),None]:
            with self.subTest(pages=pages):
                with self.assertRaises(ValueError):self.ctx.extract_pdf_pages(self.file['id'],'bad',pages,call_id='bad'+str(pages))
        self.assertEqual(len(self.s.files.list_files()),1)
        self.assertEqual(self.s.files.bytes(self.file['id']),self.raw)

    def test_encryption_corruption_and_wrong_format_rejected(self):
        writer=PdfWriter();writer.add_blank_page(120,120);writer.encrypt('secret');stream=BytesIO();writer.write(stream);writer.close()
        with self.assertRaisesRegex(ValueError,'加密'):selected_pdf_pages(stream.getvalue(),[1])
        with self.assertRaises(ValueError):selected_pdf_pages(b'%PDF-broken',[1])
        source=self.s.files._save('text.md',b'Text source','local')
        with self.assertRaisesRegex(ValueError,'只支持PDF'):self.ctx.extract_pdf_pages(source['id'],'bad',[1],call_id='wrong')
        self.assertEqual(len(self.s.files.list_files()),2)

    def test_tool_schema_is_explicit_page_numbers_no_host_paths(self):
        selected=next(t for t in build_tools(self.ctx) if t.name=='extract_pdf_pages')
        schema=selected.tool_call_schema.model_json_schema()
        self.assertEqual(set(schema['properties']),{'file_id','name','pages'})
        self.assertEqual(schema['properties']['pages']['items']['type'],'integer')

if __name__=='__main__':unittest.main()
