"""Native Word export, managed lineage and actual draft attachment contracts."""
from io import BytesIO
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from docx import Document
from docx.shared import Cm
from PIL import Image
from pypdf import PdfReader, PdfWriter

from task_backend.agent_tools import AgentTools, build_tools
from task_backend.service import BackendService


def rich_word():
    document=Document()
    document.sections[0].header.paragraphs[0].text='Company template'
    document.sections[0].footer.paragraphs[0].text='Reference Q-126'
    document.add_paragraph('Project status',style='Title')
    p=document.add_paragraph();p.add_run('Decision: ').bold=True;p.add_run('Keep the accepted layout.').italic=True
    table=document.add_table(rows=3,cols=2);table.style='Table Grid';table.autofit=False
    for col,width in zip(table.columns,[Cm(4),Cm(11)]):col.width=width
    for row,values in zip(table.rows,[['Area','Description'],['Orders','The accepted table has deliberately uneven columns.'],['Shipping','No text reconstruction is permitted.']]):
        for cell,width,value in zip(row.cells,[Cm(4),Cm(11)],values):cell.width=width;cell.text=value
    image=BytesIO();Image.new('RGB',(140,60),(20,70,150)).save(image,format='PNG');image.seek(0)
    document.add_picture(image,width=Cm(4))
    document.add_page_break();document.add_paragraph('Second page',style='Heading 1');document.add_paragraph('Explicit pagination is part of this template.')
    output=BytesIO();document.save(output);return output.getvalue()


def small_pdf():
    writer=PdfWriter();writer.add_blank_page(width=400,height=600)
    output=BytesIO();writer.write(output);return output.getvalue()


class WordPdfConversionTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        self.service=BackendService(self.root,worker=True)
        self.ctx=AgentTools(self.service,'conversion-run','conversion-chat',lambda event:None)
        self.addCleanup(self.temp.cleanup);self.addCleanup(self.service.close);self.addCleanup(self.ctx.close)
        self.enterContext(patch('socket.socket.connect',side_effect=AssertionError('No network')))
        self.source=self.service.files._save('company-template.docx',rich_word(),'local')

    def test_convert_records_exact_source_and_keeps_word_active(self):
        with patch('task_backend.document_render.pdf_bytes',side_effect=lambda data,extension,cache=None:small_pdf() if extension=='docx' else data) as converter:
            result=self.ctx.execute('convert_to_pdf',dict(file_id=self.source['id'],name='deliverable.pdf'),'native-export')
            self.assertNotIn('error',result)
            record=self.service.files.get(result['id'])
            self.assertEqual(record['parentIds'],[self.source['id']])
            self.assertEqual(record['conversion']['sourceSha256'],self.source['sha256'])
            self.assertEqual(record['conversion']['method'],'LibreOffice writer_pdf_Export')
            self.assertEqual(record['lifecycleStatus'],'active');self.assertEqual(record['revision'],1)
            self.assertTrue(record['validation']['rendered']);self.assertFalse(record['validation']['visualReviewed'])
            self.assertEqual(converter.call_args_list[0].args[:2],(self.service.files.bytes(self.source['id']),'docx'))
            draft=self.ctx.create_mail_draft('Review','Please review.',to='review@example.invalid',attachment_ids=[self.source['id'],record['id']],call_id='reply')
            self.assertEqual(draft['attachmentIds'],[self.source['id'],record['id']])
            self.assertEqual(self.service.files.get(self.source['id'])['sha256'],self.source['sha256'])
            self.assertEqual(self.service.compose.list_recent(),[])

    def test_replay_and_commit_before_checkpoint_do_not_reconvert(self):
        args=dict(file_id=self.source['id'],name='deliverable',call_id='replay')
        with patch('task_backend.document_render.pdf_bytes',side_effect=lambda data,extension,cache=None:small_pdf() if extension=='docx' else data):
            result=self.ctx.convert_to_pdf(**args)
        with patch('task_backend.document_render.pdf_bytes',side_effect=lambda data,extension,cache=None: (_ for _ in ()).throw(AssertionError('Converted twice')) if extension=='docx' else data):
            self.assertEqual(self.ctx.convert_to_pdf(**args),result)
            self.ctx.db.execute('UPDATE agent_tool_results SET result=NULL WHERE id=?',(result['id'],))
            recovered=self.ctx.convert_to_pdf(**args)
            self.assertEqual(recovered['id'],result['id']);self.assertEqual(recovered['file']['sha256'],result['file']['sha256'])
        self.assertEqual(len(self.service.files.list_files()),2)

    def test_converter_failure_keeps_input_and_creates_no_partial_artifact(self):
        before=self.service.files.bytes(self.source['id'])
        with patch('task_backend.document_render.pdf_bytes',side_effect=ValueError('Word 页面转换超时')):
            result=self.ctx.execute('convert_to_pdf',dict(file_id=self.source['id'],name='failed'),'fail')
        self.assertEqual(result['error'],'tool_failed');self.assertEqual(len(self.service.files.list_files()),1)
        self.assertEqual(self.service.files.bytes(self.source['id']),before)
        self.assertFalse(self.ctx.list_mail_drafts())

    def test_wrong_type_and_stale_source_cannot_be_exported(self):
        file=self.service.files._save('data.csv',b'name,value\na,1\n','local')
        with self.assertRaisesRegex(ValueError,'DOCX'):self.ctx.convert_to_pdf(file['id'],'bad',call_id='csv')
        first=self.ctx.edit_word(self.source['id'],'first',[dict(target='p0',expected='Project status',text='First')],call_id='edit1')
        current=self.ctx.edit_word(first['id'],'current',[dict(target='p0',expected='First',text='Current')],call_id='edit2')
        with patch('task_backend.document_render.pdf_bytes',side_effect=AssertionError('Stale input rendered')):
            with self.assertRaisesRegex(ValueError,'替代'):self.ctx.convert_to_pdf(first['id'],'stale',call_id='stale')
        self.assertEqual(self.service.files.get(current['id'])['lifecycleStatus'],'active')

    def test_tool_surface_exposes_conversion_without_content_or_code(self):
        tools={tool.name:tool for tool in build_tools(self.ctx)}
        schema=tools['convert_to_pdf'].tool_call_schema.model_json_schema()
        self.assertEqual(set(schema['properties']),{'file_id','name'})
        self.assertIn('convert_to_pdf',tools['create_document'].description)

    @unittest.skipUnless((Path(__file__).resolve().parents[2]/'.libreoffice-runtime-mac/LibreOffice.app/Contents/MacOS/soffice').is_file(),'Requires product-bundled LibreOffice')
    def test_native_export_matches_word_render_for_every_page(self):
        executable=Path(__file__).resolve().parents[2]/'.libreoffice-runtime-mac/LibreOffice.app/Contents/MacOS/soffice'
        original=self.service.files.bytes(self.source['id'])
        with patch.dict('os.environ',{'TM_SOFFICE':str(executable)}):
            # Use actual production render path, no converter mocks. The PDF
            # must be identical to the accepted Word preview, not just text-equal.
            self.service.files.preview(self.source['id'],1)
            result=self.ctx.convert_to_pdf(self.source['id'],'native-layout',call_id='real')
            record=self.service.files.get(result['id'])
            output=self.service.files.bytes(result['id']);pdf=PdfReader(BytesIO(output))
            self.assertEqual(len(pdf.pages),2)
            self.assertGreaterEqual(sum(len(page.images) for page in pdf.pages),1)
            text='\n'.join(page.extract_text() or '' for page in pdf.pages)
            for expected in ('Company template','Second page','No text reconstruction is permitted.','Reference Q-126'):self.assertIn(expected,text)
            for page in range(1,3):
                source_preview=self.service.files.preview(self.source['id'],page)
                pdf_preview=self.service.files.preview(result['id'],page)
                self.assertEqual(source_preview['dataUrl'],pdf_preview['dataUrl'],f'Page {page} layout differs')
            self.assertEqual(record['validation']['pageCount'],2)
            self.assertEqual(self.service.files.bytes(self.source['id']),original)
            self.assertEqual(self.service.files.get(self.source['id']).get('lifecycleStatus','active'),'active')


if __name__=='__main__':unittest.main()
