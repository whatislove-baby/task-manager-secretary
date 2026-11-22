"""Layout corrections preserve source packages and use the current file family."""
from io import BytesIO
from pathlib import Path
import tempfile
import unittest
import zipfile
from unittest.mock import patch

from docx import Document
from docx.shared import Inches
from task_backend.document_formats import format_docx_bytes, inspect_docx
from task_backend.agent_tools import AgentTools
from task_backend.service import BackendService


def fixture():
    doc=Document();doc.add_paragraph('Weekly progress');doc.add_paragraph('Source facts remain unchanged.')
    table=doc.add_table(rows=2,cols=2)
    for row,values in zip(table.rows,[['Workstream','Progress'],['Orders','3/5']]):
        for cell,value in zip(row.cells,values):cell.text=value
    table.rows[1].cells[0].merge(table.rows[1].cells[1]).text='One merged note'
    doc.sections[0].header.paragraphs[0].text='Keep company header'
    doc.sections[0].footer.paragraphs[0].text='Keep source footer'
    from PIL import Image
    im=BytesIO();Image.new('RGB',(50,30),'blue').save(im,format='PNG');im.seek(0);doc.add_picture(im,width=Inches(1))
    out=BytesIO();doc.save(out)
    # OPC unknown parts must survive: python-docx serialisation alone drops them.
    output=BytesIO()
    with zipfile.ZipFile(BytesIO(out.getvalue())) as source,zipfile.ZipFile(output,'w') as target:
        for item in source.infolist():target.writestr(item,source.read(item.filename))
        target.writestr('customXml/template-owner.bin',b'original-template-opaque-part')
    return output.getvalue()


class WordFormatPatchTests(unittest.TestCase):
    def test_local_format_patch_keeps_all_other_package_parts_and_text(self):
        original=fixture()
        changes=[dict(target='p0',expected='Weekly progress',style='Heading 1',keep_with_next=True),
                 dict(target='p1',expected='Source facts remain unchanged.',page_break_before=True,space_after_pt=8),
                 dict(target='t0',expected_headers=['Workstream','Progress'],column_widths_cm=[8,6],repeat_header=True)]
        updated= format_docx_bytes(original,changes)
        with zipfile.ZipFile(BytesIO(original)) as old,zipfile.ZipFile(BytesIO(updated)) as new:
            self.assertEqual(old.namelist(),new.namelist())
            for path in old.namelist():
                if path!='word/document.xml':self.assertEqual(old.read(path),new.read(path),path)
        doc=Document(BytesIO(updated))
        self.assertEqual(doc.paragraphs[0].style.name,'Heading 1')
        self.assertTrue(doc.paragraphs[0].paragraph_format.keep_with_next)
        self.assertTrue(doc.paragraphs[1].paragraph_format.page_break_before)
        self.assertEqual(doc.paragraphs[1].text,'Source facts remain unchanged.')
        self.assertAlmostEqual(doc.tables[0].columns[0].width.cm,8,places=2)
        self.assertAlmostEqual(doc.tables[0].cell(1,0).width.cm,14,places=2)
        self.assertEqual(doc.tables[0].cell(1,0).text,'One merged note')
        self.assertEqual(len(doc.inline_shapes),1)
        info=inspect_docx(updated)
        self.assertEqual(info['paragraphs'][1]['format']['pageBreakBefore'],True)
        self.assertAlmostEqual(info['tables'][0]['columnWidthsCm'][0],8,places=2)

    def test_invalid_or_stale_layout_changes_do_not_write_partial_package(self):
        data=fixture()
        bad=[dict(target='t0',expected_headers=['Wrong','Progress'],column_widths_cm=[8,6]),
             dict(target='t0',expected_headers=['Workstream','Progress'],column_widths_cm=[80,6]),
             dict(target='t0',expected_headers=['Workstream','Progress'],column_widths_cm=[8]),
             dict(target='p1',expected='stale',page_break_before=True),
             dict(target='p1',expected='Source facts remain unchanged.',style='Made up style'),
             dict(target='p1',expected='Source facts remain unchanged.',page_break_before='false'),
             dict(target='p1',expected='Source facts remain unchanged.',text='This changes content'),
             dict(target='p1',expected='Source facts remain unchanged.',space_after_pt=float('nan'))]
        for change in bad:
            with self.subTest(change=change),self.assertRaises(ValueError):format_docx_bytes(data,[change])
        self.assertEqual(Document(BytesIO(data)).paragraphs[1].text,'Source facts remain unchanged.')

    def test_versioned_formatting_and_replay_keep_original_and_reject_old_generated(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);service=BackendService(root,worker=True);ctx=AgentTools(service,'format-run','chat',lambda event:None)
            try:
                with patch('socket.socket.connect',side_effect=AssertionError('No network')):
                    original=service.files._save('template.docx',fixture(),'local')
                    changes=[dict(target='p0',expected='Weekly progress',style='Heading 1')]
                    first=ctx.format_word(original['id'],'formatted',changes,call_id='format-1')
                    self.assertEqual(first,ctx.format_word(original['id'],'formatted',changes,call_id='format-1'))
                    second=ctx.format_word(first['id'],'formatted-final',[dict(target='p1',expected='Source facts remain unchanged.',keep_together=True)],expected_revision=1,call_id='format-2')
                    self.assertEqual(service.files.get(first['id'])['lifecycleStatus'],'superseded')
                    self.assertEqual(service.files.get(second['id'])['parentIds'],[first['id']])
                    self.assertEqual(service.files.get(original['id'])['sha256'],original['sha256'])
                    with self.assertRaises(ValueError):ctx.format_word(first['id'],'bad',changes,call_id='stale')
                    before=service.files.db.execute('SELECT count(*) FROM files').fetchone()[0]
                    with self.assertRaises(ValueError):ctx.format_word(second['id'],'bad',changes,expected_revision=999,call_id='bad-revision')
                    self.assertEqual(service.files.db.execute('SELECT count(*) FROM files').fetchone()[0],before)
            finally:ctx.close();service.close()

if __name__=='__main__':unittest.main()
