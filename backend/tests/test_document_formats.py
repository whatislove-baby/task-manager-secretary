"""Document capabilities: scanned pages, structured Word edits and real rendering."""
import base64
from io import BytesIO
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from uuid import uuid4
import zipfile

from task_backend.mail_files import MailFiles,make_docx,make_pdf,extract_document
from task_backend.document_formats import edit_docx_bytes,inspect_docx,check_docx
from task_backend.document_render import pdf_bytes,render_page,soffice_path


class DocumentFormatsTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.directory=Path(self.tmp.name)
        self.files=MailFiles(SimpleNamespace(directory=self.directory))
    def tearDown(self):self.files.close();self.tmp.cleanup()
    def word(self):
        from docx import Document
        from docx.shared import Inches
        from PIL import Image
        doc=Document(BytesIO(make_docx('课程进度报告','保留简介。')))
        doc.add_paragraph('截止日期是 2026年11月26日。')
        table=doc.add_table(rows=2,cols=2)
        for row,values in zip(table.rows,[['项目','状态'],['幻灯片','未开始']]):
            for cell,value in zip(row.cells,values):cell.text=value
        image=BytesIO();Image.new('RGB',(120,80),'blue').save(image,format='PNG');image.seek(0)
        doc.add_picture(image,width=Inches(1));doc.sections[0].header.paragraphs[0].text='保留课程页眉'
        out=BytesIO();doc.save(out);return out.getvalue()
    def test_scan_pdf_is_stored_and_rendered_without_text(self):
        from reportlab.pdfgen import canvas
        from reportlab.lib.utils import ImageReader
        from PIL import Image,ImageDraw
        img=Image.new('RGB',(300,150),'white');ImageDraw.Draw(img).rectangle((30,20,200,120),fill='red')
        stream=BytesIO();c=canvas.Canvas(stream,pagesize=(300,150));c.drawImage(ImageReader(img),0,0,width=300,height=150);c.showPage();c.save()
        original=stream.getvalue();record=self.files._save('scan.pdf',original,'local')
        self.assertEqual(self.files.read(record['id']),'');self.assertIn('视觉',str(record['warnings']))
        preview=self.files.preview(record['id']);self.assertEqual(preview['pageCount'],1)
        picture=Image.open(BytesIO(base64.b64decode(preview['dataUrl'].split(',',1)[1])))
        self.assertGreater(picture.width,300);self.assertLess(picture.getpixel((100,100))[1],30)
        self.assertEqual(self.files.bytes(record['id']),original)
        checked=self.files.validate(record['id']);self.assertTrue(checked['structuralPassed']);self.assertTrue(checked['rendered']);self.assertFalse(checked['visualReviewed'])
    def test_pdf_page_bounds_corruption_and_hash_checks(self):
        record=self.files._save('file.pdf',make_pdf('测试','可读取正文'),'generated')
        for page in (0,True,2,-1):
            with self.assertRaises(ValueError):self.files.preview(record['id'],page)
        with self.assertRaises(ValueError):render_page(b'broken',1)
        (self.files.root/record['id']).write_bytes(b'broken')
        with self.assertRaisesRegex(ValueError,'变化'):self.files.preview(record['id'])
    def test_word_structure_and_targeted_edit_preserve_image_header_source(self):
        data=self.word();original=self.files._save('report.docx',data,'local')
        info=self.files.inspect(original['id']);self.assertEqual(info['tables'][0]['rows'][1][1]['text'],'未开始');self.assertEqual(len(info['images']),1)
        target=next(p for p in info['paragraphs'] if p['text'].startswith('截止日期'))
        edits=[dict(target=target['id'],expected=target['text'],text='截止日期是 2026年11月28日。'),dict(target='t0.r1.c1',expected='未开始',text='初稿完成')]
        stable=str(uuid4());updated=self.files.edit_docx(original['id'],'课程修订.docx',edits,stable)
        self.assertEqual(updated,self.files.edit_docx(original['id'],'课程修订.docx',edits,stable))
        self.assertEqual(self.files.bytes(original['id']),data);self.assertEqual(updated['parentIds'],[original['id']])
        next_info=self.files.inspect(updated['id']);self.assertEqual(next_info['tables'][0]['rows'][1][1]['text'],'初稿完成');self.assertEqual(next_info['headers'][0][0],'保留课程页眉')
        with zipfile.ZipFile(BytesIO(data)) as a,zipfile.ZipFile(BytesIO(self.files.bytes(updated['id']))) as b:
            self.assertEqual({n:a.read(n) for n in a.namelist() if n.startswith('word/media/')},{n:b.read(n) for n in b.namelist() if n.startswith('word/media/')})
        self.assertIn('保留简介',self.files.read(updated['id']))
        from fontTools.ttLib import TTFont
        from uuid import UUID
        from xml.etree import ElementTree as ET
        with zipfile.ZipFile(BytesIO(self.files.bytes(updated['id']))) as z:
            ns='{http://schemas.openxmlformats.org/wordprocessingml/2006/main}'
            entry=ET.fromstring(z.read('word/fontTable.xml')).find('.//'+ns+'embedRegular')
            key=UUID(entry.get(ns+'fontKey')).bytes[::-1];font=bytearray(z.read('word/fonts/NotoSansSC.odttf'))
            for i in range(32):font[i]^=key[i%16]
            parsed=TTFont(BytesIO(font));self.assertTrue(all(ord(c) in parsed.getBestCmap() for c in '初稿完成'));parsed.close()
    def test_external_arial_template_embeds_cjk_without_changing_latin_header_or_image(self):
        from docx import Document
        from docx.oxml.ns import qn
        from docx.opc.constants import RELATIONSHIP_TYPE as RT
        from PIL import Image
        from fontTools.ttLib import TTFont
        from uuid import UUID
        doc=Document();doc.styles['Normal'].font.name='Arial'
        run=doc.add_paragraph().add_run('Student name: __________');run.font.name='Arial';run.bold=True
        doc.sections[0].header.paragraphs[0].text='Course office - retain header'
        picture=BytesIO();Image.new('RGB',(20,15),'blue').save(picture,format='PNG');picture.seek(0);doc.add_picture(picture)
        out=BytesIO();doc.save(out);original=out.getvalue()
        updated=edit_docx_bytes(original,[dict(target='p0',expected='Student name: __________',text='Student name: 林安宁')])
        edited=Document(BytesIO(updated));first=edited.paragraphs[0].runs[0]
        self.assertEqual(first.font.name,'Arial');self.assertTrue(first.bold)
        self.assertEqual(first._r.rPr.rFonts.get(qn('w:eastAsia')),'Noto Sans SC')
        settings_tags=[e.tag for e in edited.settings.element]
        self.assertLess(settings_tags.index(qn('w:embedTrueTypeFonts')),settings_tags.index(qn('w:saveSubsetFonts')))
        self.assertLess(settings_tags.index(qn('w:saveSubsetFonts')),settings_tags.index(qn('w:defaultTabStop')))
        table=edited.part.part_related_by(RT.FONT_TABLE)
        from lxml import etree
        entry=next(f for f in etree.fromstring(table.blob) if f.get(qn('w:name'))=='Noto Sans SC')
        embed=entry.find(qn('w:embedRegular'));part=table.rels[embed.get(qn('r:id'))].target_part
        font=bytearray(part.blob);key=UUID(embed.get(qn('w:fontKey'))).bytes[::-1]
        for i in range(32):font[i]^=key[i%16]
        parsed=TTFont(BytesIO(font));self.assertTrue(all(ord(c) in parsed.getBestCmap() for c in '林安宁'));parsed.close()
        with zipfile.ZipFile(BytesIO(original)) as a,zipfile.ZipFile(BytesIO(updated)) as b:
            for name in a.namelist():
                if name.startswith(('word/media/','word/header','word/footer')):self.assertEqual(a.read(name),b.read(name),name)
        # A second edit extends the same embedded font rather than accumulating parts.
        again=edit_docx_bytes(updated,[dict(target='p0',expected='Student name: 林安宁',text='Student name: 林安宁；湖景门诊')])
        table2=Document(BytesIO(again)).part.part_related_by(RT.FONT_TABLE)
        self.assertEqual(len([r for r in table2.rels.values() if r.reltype==RT.FONT]),1)
        font2=bytearray(table2.rels[embed.get(qn('r:id'))].target_part.blob)
        for i in range(32):font2[i]^=key[i%16]
        parsed=TTFont(BytesIO(font2));self.assertTrue(all(ord(c) in parsed.getBestCmap() for c in '林安宁湖景门诊'));parsed.close()
        try:soffice_path()
        except ValueError:return
        # PDF text extraction checks actual LibreOffice output, not just DOCX XML.
        rendered=pdf_bytes(updated,'docx');value=extract_document(rendered,'pdf')[0]
        self.assertIn('林安宁',value);self.assertIn('Student name',value);self.assertTrue(render_page(rendered)['rendered'])

    def test_new_cjk_font_does_not_reuse_an_existing_foreign_font_part_or_relationship(self):
        from docx import Document
        from docx.oxml import OxmlElement,parse_xml
        from docx.oxml.ns import qn
        from docx.opc.constants import RELATIONSHIP_TYPE as RT
        from docx.opc.packuri import PackURI
        from docx.opc.part import Part
        from lxml import etree
        doc=Document();doc.add_paragraph('Name');table=doc.part.part_related_by(RT.FONT_TABLE)
        foreign=Part(PackURI('/word/fonts/NotoSansSC.odttf'),'application/vnd.openxmlformats-officedocument.obfuscatedFont',b'foreign-font-must-remain',doc.part.package)
        rid=table.relate_to(foreign,RT.FONT)
        root=parse_xml(table.blob);entry=OxmlElement('w:font');entry.set(qn('w:name'),'Foreign Font')
        embed=OxmlElement('w:embedRegular');embed.set(qn('r:id'),rid);embed.set(qn('w:fontKey'),'{00000000-0000-0000-0000-000000000001}');entry.append(embed);root.append(entry)
        table._blob=etree.tostring(root);out=BytesIO();doc.save(out)
        updated=edit_docx_bytes(out.getvalue(),[dict(target='p0',expected='Name',text='姓名：林安宁')])
        newtable=Document(BytesIO(updated)).part.part_related_by(RT.FONT_TABLE)
        self.assertEqual(newtable.rels[rid].target_part.blob,b'foreign-font-must-remain')
        fonts=etree.fromstring(newtable.blob);noto=next(f for f in fonts if f.get(qn('w:name'))=='Noto Sans SC')
        newrid=noto.find(qn('w:embedRegular')).get(qn('r:id'))
        self.assertNotEqual(newrid,rid);self.assertNotEqual(newtable.rels[newrid].target_part.partname,foreign.partname)
        self.assertTrue(Document(BytesIO(updated)).settings.element.find(qn('w:embedTrueTypeFonts')) is not None)
    def test_word_expected_old_mismatch_is_atomic_and_cannot_reuse_id(self):
        original=self.files._save('report.docx',self.word(),'local');before=len(self.files.list_files())
        edits=[dict(target='t0.r1.c1',expected='未开始',text='完成'),dict(target='p0',expected='wrong old value',text='破坏标题')]
        with self.assertRaisesRegex(ValueError,'不匹配'):self.files.edit_docx(original['id'],'new',edits)
        self.assertEqual(len(self.files.list_files()),before)
        stable=str(uuid4());self.files.edit_docx(original['id'],'one',[edits[0]],stable)
        with self.assertRaisesRegex(ValueError,'不一致'):self.files.edit_docx(original['id'],'two',[edits[0]],stable)
    def test_word_duplicate_merged_cell_and_out_of_range_targets_fail(self):
        from docx import Document
        doc=Document();table=doc.add_table(rows=1,cols=2);table.cell(0,0).merge(table.cell(0,1)).text='合并内容';out=BytesIO();doc.save(out)
        edits=[dict(target='t0.r0.c0',expected='合并内容',text='一次'),dict(target='t0.r0.c1',expected='合并内容',text='两次')]
        with self.assertRaisesRegex(ValueError,'重复'):edit_docx_bytes(out.getvalue(),edits)
        with self.assertRaisesRegex(ValueError,'不存在'):edit_docx_bytes(out.getvalue(),[dict(target='p999',expected='',text='x')])
    def test_markdown_tables_generate_real_word_table_and_pdf(self):
        content='## 任务进度\n\n| 任务 | 状态 |\n|---|---|\n| 数据清洗 | 完成 |\n| 幻灯片 | 待复核 |\n\n最终核对后提交。'
        word=make_docx('课程任务报告',content);info=inspect_docx(word)
        self.assertEqual(sum(p['text']=='任务进度' for p in info['paragraphs']),1);self.assertEqual(next(p['style'] for p in info['paragraphs'] if p['text']=='任务进度'),'Heading2')
        self.assertEqual(len(info['tables']),1);self.assertEqual(info['tables'][0]['rows'][1][0]['text'],'数据清洗')
        self.assertIn('最终核对',extract_document(word,'docx')[0])
        pdf=make_pdf('课程任务报告',content);self.assertIn('待复核',extract_document(pdf,'pdf')[0]);self.assertTrue(render_page(pdf)['rendered'])
    def test_markdown_inline_word_uses_real_runs_and_preserves_literal_markers(self):
        from docx import Document
        content=r'**重点结论**与*需要复核*，***双重强调***；`a ** b`；\*literal\*；[课程链接](https://example.invalid/never-fetch)；&#x2605;。'+'\n\n| 项目 | 结果 |\n|---|---|\n| **合计** | *17.5* 与 `x_y` |'
        data=make_docx('行内格式测试',content);doc=Document(BytesIO(data))
        paragraph=doc.paragraphs[1];value=paragraph.text
        self.assertIn('重点结论与需要复核',value);self.assertNotIn('**重点结论**',value)
        self.assertIn('a ** b',value);self.assertIn('*literal*',value);self.assertIn('课程链接',value);self.assertIn('★',value)
        self.assertTrue(next(r for r in paragraph.runs if r.text=='重点结论').bold)
        self.assertTrue(next(r for r in paragraph.runs if r.text=='需要复核').italic)
        nested=next(r for r in paragraph.runs if r.text=='双重强调');self.assertTrue(nested.bold);self.assertTrue(nested.italic)
        code=next(r for r in paragraph.runs if r.text=='a ** b');self.assertEqual(code.font.name,'Courier New');self.assertFalse(code.bold)
        self.assertEqual(doc.tables[0].cell(1,0).text,'合计');self.assertTrue(doc.tables[0].cell(1,0).paragraphs[0].runs[0].bold)
        self.assertTrue(next(r for r in doc.tables[0].cell(1,1).paragraphs[0].runs if r.text=='17.5').italic)
        with zipfile.ZipFile(BytesIO(data)) as z:
            self.assertFalse(any(b'TargetMode="External"' in z.read(n) for n in z.namelist() if n.endswith('.rels')))
        check_docx(data,rendering=True)
    def test_markdown_inline_pdf_actual_render_has_no_format_markers(self):
        try:soffice_path()
        except ValueError:self.skipTest('No local or bundled LibreOffice configured')
        content=r'**重点结论**与*需要复核*；`a ** b`；\*literal\*；[课程链接](https://example.invalid/never-fetch)。'+'\n\n| 项目 | 结果 |\n|---|---|\n| **合计** | *17.5* |'
        data=make_pdf('PDF 行内格式测试',content);value=extract_document(data,'pdf')[0]
        self.assertIn('重点结论',value);self.assertNotIn('**重点结论**',value);self.assertNotIn('*需要复核*',value)
        self.assertIn('a ** b',value);self.assertIn('*literal*',value);self.assertIn('课程链接',value);self.assertNotIn('https://',value)
        self.assertIn('合计',value);self.assertNotIn('**合计**',value);self.assertIn('17.5',value);self.assertNotIn('*17.5*',value)
        self.assertTrue(render_page(data)['rendered'])
    def test_inline_pdf_conversion_failure_does_not_save_partial_output(self):
        with patch('task_backend.document_render.pdf_bytes',side_effect=ValueError('Word 页面转换未完成')):
            with self.assertRaisesRegex(ValueError,'转换未完成'):
                self.files.create_document('rich','pdf','Rich','**bold**',[])
            self.assertEqual(self.files.list_files(),[])
            # Plain documents retain the ReportLab path and its independence.
            plain=make_pdf('Plain','A regular sentence without inline styling.')
            self.assertTrue(plain.startswith(b'%PDF-'))

    def test_external_resources_and_dynamic_fields_not_automatically_rendered(self):
        from docx import Document
        from docx.oxml import OxmlElement
        doc=Document();p=doc.add_paragraph();r=p.add_run();field=OxmlElement('w:instrText');field.text='INCLUDETEXT "file:///etc/passwd"';r._r.append(field);out=BytesIO();doc.save(out)
        with self.assertRaisesRegex(ValueError,'字段'):edit_docx_bytes(out.getvalue(),[dict(target='p0',expected='',text='new text')])
        with self.assertRaisesRegex(ValueError,'外部字段'):check_docx(out.getvalue(),rendering=True)
        with patch('subprocess.run') as run:
            with self.assertRaisesRegex(ValueError,'外部字段'):pdf_bytes(out.getvalue(),'docx')
            run.assert_not_called()
    def test_validation_reports_missing_renderer_without_claiming_visual_success(self):
        record=self.files._save('word.docx',self.word(),'local')
        with patch('task_backend.document_render.soffice_path',side_effect=ValueError('需要 LibreOffice 渲染器')):
            result=self.files.validate(record['id'])
        self.assertTrue(result['structuralPassed']);self.assertFalse(result['rendered']);self.assertFalse(result['visualReviewed']);self.assertIn('LibreOffice',str(result['warnings']))
        self.assertEqual(self.files.get(record['id'])['validation'],result)
    def test_csv_storage_structure_and_reopen_validation(self):
        data='student,score\n001,85\n002,90\n'.encode()
        record=self.files._save('grades.csv',data,'local')
        self.assertEqual(self.files.bytes(record['id']),data)
        info=self.files.inspect(record['id']);self.assertEqual(info['rows'][0][0],'001')
        self.assertEqual(info['rowCount'],2)
        checked=self.files.validate(record['id']);self.assertTrue(checked['structuralPassed']);self.assertFalse(checked['rendered'])
    def test_real_word_renderer_when_configured(self):
        try:soffice_path()
        except ValueError:self.skipTest('No local or bundled LibreOffice configured')
        record=self.files._save('report.docx',self.word(),'local')
        checked=self.files.validate(record['id']);self.assertTrue(checked['rendered'],checked)
        result=self.files.preview(record['id']);self.assertTrue(result['dataUrl'].startswith('data:image/png;base64,'));self.assertGreater(result['width'],500)
        self.assertEqual(self.files.preview(record['id'])['dataUrl'],result['dataUrl'])
    def test_structural_pagination_does_not_claim_complete_document(self):
        record=self.files._save('word.docx',self.word(),'local')
        first=self.files.inspect(record['id'],0,1);self.assertEqual(first['nextOffset'],1);self.assertEqual(len(first['paragraphs']),1)
        second=self.files.inspect(record['id'],1,1);self.assertNotEqual(first['paragraphs'][0]['id'],second['paragraphs'][0]['id'])

if __name__=='__main__':unittest.main()
