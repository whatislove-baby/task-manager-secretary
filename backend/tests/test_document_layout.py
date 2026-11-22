"""Regression tests for real chart images, table sizing and local text formatting."""
import base64
from io import BytesIO
import json
from pathlib import Path
import tempfile
import unittest
import zipfile
from docx import Document
from docx.oxml.ns import qn
from task_backend.mail_files import make_docx, make_pdf, extract_document
from task_backend.document_formats import chart_spec, edit_docx_bytes, markdown_blocks
from task_backend.document_render import pdf_bytes, render_page


class DocumentLayoutTests(unittest.TestCase):
    def test_partial_rewrite_keeps_bold_label_and_normal_body(self):
        doc=Document();p=doc.add_paragraph();p.add_run('Summary: ').bold=True
        p.add_run('There are two pending tasks.').italic=False
        stream=BytesIO();doc.save(stream)
        updated=edit_docx_bytes(stream.getvalue(),[dict(target='p0',expected=p.text,text='Summary: There are three pending tasks, due Friday.')])
        new=Document(BytesIO(updated)).paragraphs[0]
        self.assertEqual(new.text,'Summary: There are three pending tasks, due Friday.')
        self.assertEqual(''.join(r.text for r in new.runs if r.bold),'Summary: ')
        self.assertTrue(all(not r.bold for r in new.runs if 'three' in r.text or 'Friday' in r.text))

    def test_insertion_after_bold_label_uses_body_format_and_tabs_survive(self):
        doc=Document();p=doc.add_paragraph();p.add_run('Status: ').bold=True;p.add_run('ready\tfor review')
        stream=BytesIO();doc.save(stream)
        result=Document(BytesIO(edit_docx_bytes(stream.getvalue(),[dict(target='p0',expected=p.text,text='Status: almost ready\tfor review')]))).paragraphs[0]
        self.assertEqual(result.text,'Status: almost ready\tfor review')
        self.assertEqual(''.join(r.text for r in result.runs if r.bold),'Status: ')

    def test_cell_rewrite_keeps_existing_mixed_runs(self):
        doc=Document();p=doc.add_table(rows=1,cols=1).cell(0,0).paragraphs[0]
        p.add_run('Total: ').bold=True;p.add_run('12 items')
        stream=BytesIO();doc.save(stream)
        result=Document(BytesIO(edit_docx_bytes(stream.getvalue(),[dict(target='t0.r0.c0',expected=p.text,text='Total: 13 items')]))).tables[0].cell(0,0).paragraphs[0]
        self.assertEqual(result.text,'Total: 13 items')
        self.assertEqual(''.join(r.text for r in result.runs if r.bold),'Total: ')

    def test_real_chart_image_and_searchable_values_survive_rendering(self):
        spec=dict(type='bar',title='模块进展',labels=['邮件整理','日历','资料管理','报告'],values=[75,58.3,50,40],unit='%',max=100)
        content='## 当前情况\n\n```chart\n'+json.dumps(spec,ensure_ascii=False)+'\n```\n\n来源：演示数据。'
        word=make_docx('进度报告',content);doc=Document(BytesIO(word))
        self.assertEqual(len(doc.inline_shapes),1)
        self.assertIn('邮件整理: 75%',extract_document(word,'docx')[0])
        self.assertNotIn('"type"',extract_document(word,'docx')[0])
        with zipfile.ZipFile(BytesIO(word)) as z:self.assertTrue(any(p.startswith('word/media/') for p in z.namelist()))
        pdf=pdf_bytes(word,'docx');self.assertEqual(render_page(pdf)['pageCount'],1)
        self.assertIn('58.3%',extract_document(pdf,'pdf')[0])
        self.assertTrue(make_pdf('进度报告',content).startswith(b'%PDF-'))

    def test_bad_chart_data_fails_explicitly_instead_of_being_printed_or_dropped(self):
        bad=[{},dict(type='line',labels=['x'],values=[1]),dict(type='bar',labels=['x'],values=[float('nan')]),dict(type='bar',labels=['x'],values=[1],max=.5),dict(type='bar',labels=['x'],values=[True]),dict(type='bar',labels=['x'],values=[1],python='do()')]
        for spec in bad:
            with self.subTest(spec=spec),self.assertRaises(ValueError):make_docx('Invalid','```chart\n'+json.dumps(spec)+'\n```')
        with self.assertRaises(ValueError):chart_spec('not json')

    def test_table_prioritizes_narrative_and_keeps_each_row_together(self):
        content='| 编号 | 说明 |\n|---|---|\n| 01 | 在邮件整理阶段先保留原始附件并验证处理后的结果，最后由用户检查。 |\n| 02 | 日历更新采用同一个事项编号，避免产生重复记录。 |'
        doc=Document(BytesIO(make_docx('状态说明',content)));table=doc.tables[0]
        self.assertGreater(table.columns[1].width,table.columns[0].width*2)
        for row in table.rows:self.assertIsNotNone(row._tr.trPr.find(qn('w:cantSplit')))
        self.assertIsNotNone(table.rows[0]._tr.trPr.find(qn('w:tblHeader')))
        self.assertFalse(table.autofit)
        self.assertEqual(table.cell(1,0).text,'01')
        page=pdf_bytes(make_docx('状态说明',content),'docx');self.assertEqual(render_page(page)['pageCount'],1)

if __name__=='__main__':unittest.main()
