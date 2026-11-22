"""Real workbook ingestion is independent of literal table transformations."""
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch
import unittest
import zipfile

from openpyxl import Workbook, load_workbook
from task_backend.mail_files import MailFiles
from task_backend.spreadsheet_files import inspect_xlsx, read_table
from task_backend.skill_library import compare_output, output_difference




def workbook(build):
    book = Workbook(); build(book); out = BytesIO(); book.save(out); book.close()
    return out.getvalue()


def rewrite(data, name, transform):
    out = BytesIO()
    with zipfile.ZipFile(BytesIO(data)) as source, zipfile.ZipFile(out, 'w', zipfile.ZIP_DEFLATED) as target:
        for item in source.infolist():
            content = source.read(item.filename)
            target.writestr(item.filename, transform(content) if item.filename == name else content)
    return out.getvalue()


class WorkbookFiles(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.files = MailFiles(SimpleNamespace(directory=Path(self.tmp.name)))
        self.addCleanup(self.files.close)

    def test_merged_title_late_header_and_multiple_sheets_are_preserved(self):
        def build(book):
            s = book.active; s.title = '订单'; s.merge_cells('A1:D1'); s['A1'] = '采购单'
            s.append([]); s.append(['料号', '数量']); s.append(['0007', 12])
            book.create_sheet('说明')['B3'] = '请保留前导零'
        data = workbook(build)
        record = self.files._save('采购单.xlsx', data, 'local')
        self.assertEqual(self.files.bytes(record['id']), data)
        self.assertEqual(record['parseStatus'], 'parsed')
        preview = self.files.inspect(record['id'], sheet_name='订单', cell_range='A3:B4', limit=1)
        self.assertEqual(preview['sheetNames'], ['订单', '说明'])
        self.assertEqual(preview['mergedRanges'], ['A1:D1'])
        self.assertEqual(preview['nextOffset'], 1)
        self.assertEqual([c['coordinate'] for c in preview['cells']], ['A3', 'B3'])
        page = self.files.inspect(record['id'], offset=1, sheet_name='订单', cell_range='A3:B4')
        self.assertEqual([(c['coordinate'], c['value']) for c in page['cells']], [('A4', '0007'), ('B4', 12)])
        self.assertIn('请保留前导零', self.files.read(record['id']))
        self.assertTrue(self.files.validate(record['id'])['structuralPassed'])
        with self.assertRaises(ValueError):read_table(data, 'xlsx')

    def test_formula_and_error_are_readable_without_evaluating_or_trusting_cache(self):
        def build(book):
            s=book.active;s.append(['Quantity', 'Calculated']);s.append([3, '=A2*2']);s['C2']='#DIV/0!'
        data=workbook(build)
        record=self.files._save('formula.xlsx',data,'local')
        info=self.files.inspect(record['id'], sheet_name='Sheet', cell_range='A2:C2')
        formula=next(c for c in info['cells'] if c['coordinate']=='B2')
        self.assertEqual(formula['formula'],'=A2*2')
        self.assertIsNone(formula['cachedValue']);self.assertEqual(formula['cacheStatus'],'missing')
        self.assertFalse(info['formulasEvaluated'])
        self.assertEqual(next(c for c in info['cells'] if c['coordinate']=='C2')['dataType'],'e')
        cached=rewrite(data,'xl/worksheets/sheet1.xml',lambda raw:raw.replace(b'<f>A2*2</f><v></v>',b'<f>A2*2</f><v>6</v>'))
        cell=next(c for c in inspect_xlsx(cached,sheet_name='Sheet',cell_range='B2:B2')['cells'] if c['coordinate']=='B2')
        self.assertEqual(cell['cachedValue'],6);self.assertEqual(cell['cacheStatus'],'unverified')
        with self.assertRaisesRegex(ValueError,'公式'):read_table(cached,'xlsx')


    def test_corrupt_macro_external_and_oversized_merge_still_rejected(self):
        data=workbook(lambda b:b.active.append(['title']))
        bad=[b'not an xlsx']
        stream=BytesIO(data)
        with zipfile.ZipFile(stream,'a') as z:z.writestr('xl/vbaProject.bin',b'not code')
        bad.append(stream.getvalue())
        bad.append(rewrite(data,'xl/worksheets/sheet1.xml',lambda raw:raw.replace(b'</worksheet>',b'<mergeCells count="1"><mergeCell ref="A1:XFD1048576"/></mergeCells></worksheet>')))
        bad.append(rewrite(data,'xl/worksheets/sheet1.xml',lambda raw:b'<!DOCTYPE x [<!ENTITY z "bad">]>'+raw))
        bad.append(rewrite(data,'xl/worksheets/sheet1.xml',lambda raw:raw.replace(b'ref="A1:A1"',b'ref="A1:XFD1048576"')))
        def external(book):
            book.active['A1']='website';book.active['A1'].hyperlink='https://example.invalid'
        bad.append(workbook(external))
        for content in bad:
            with self.subTest(length=len(content)), self.assertRaises(ValueError):self.files._save('unsafe.xlsx',content,'local')
        self.assertEqual(self.files.list_files(),[])

    def test_archive_entry_duplicates_and_cell_budget_remain_bounded(self):
        data=workbook(lambda b:b.active.append(['first','second']))
        stream=BytesIO(data)
        with self.assertWarns(UserWarning),zipfile.ZipFile(stream,'a') as z:z.writestr('xl/workbook.xml',b'<workbook/>')
        with self.assertRaisesRegex(ValueError,'重复'):self.files._save('duplicate.xlsx',stream.getvalue(),'local')
        with patch('task_backend.spreadsheet_files.MAX_CELLS',1),self.assertRaisesRegex(ValueError,'总范围'):
            self.files._save('over-budget.xlsx',data,'local')
        implicit=rewrite(data,'xl/worksheets/sheet1.xml',lambda raw:raw.replace(b'<sheetData>',b'<sheetData><row r="2">'+b'<c><v>1</v></c>'*101+b'</row>'))
        with self.assertRaisesRegex(ValueError,'列坐标'):self.files._save('implicit.xlsx',implicit,'local')

    def test_truncated_index_keeps_original_and_reports_it(self):
        data=workbook(lambda b:[b.active.append([str(i)+'x'*20000]) for i in range(8)])
        item=self.files._save('long.xlsx',data,'local')
        self.assertTrue(item['indexTruncated']);self.assertEqual(self.files.bytes(item['id']),data)
        info=self.files.inspect(item['id'],sheet_name='Sheet',cell_range='A8:A8')
        self.assertTrue(info['cellValuesTruncated']);self.assertTrue(info['cells'][0]['valueTruncated'])


    def test_complex_output_can_pass_examples_and_run_without_relaxing_review(self):
        from task_backend.skill_library import SkillLibraryService
        service=self.files.service;service.files=self.files
        calls=[]
        def result(amount):
            def build(book):
                sheet=book.active;sheet.merge_cells('A1:B1');sheet['A1']='Confirmed'
                sheet.append(['Amount','Formula']);sheet.append([amount,'=A3*2'])
                book.create_sheet('Notes')['A1']='reference'
            return workbook(build)
        def runner(code,inputs):
            calls.append(inputs)
            value=inputs['input.txt'].decode()
            if value=='invalid':return dict(ok=False,exitCode=2,outputs={},stdout='',stderr='Missing amount')
            return dict(ok=True,exitCode=0,outputs={'result.xlsx':result(int(value)*2)},stdout='',stderr='')
        service.skills=SkillLibraryService(service,runner);self.addCleanup(service.skills.close)
        inputs=[self.files._save(f'in{i}.txt',str(v).encode(),'local')['id'] for i,v in enumerate((2,4,'invalid'))]
        expected=[self.files._save(f'expected{i}.xlsx',result(v),'local')['id'] for i,v in enumerate((4,8))]
        value=dict(title='Workbook conversion',kind='script',scope=dict(kind='personal',label='Local tests',emails=[]),
            rationale='Verify file plumbing, not script business accuracy',instruction='Preserve the supplied workbook layout.',
            code='mocked runner only',inputs=[dict(name='input.txt',description='Amount')],
            outputs=[dict(name='result.xlsx',description='Workbook',comparison='workbook')],
            examples=[dict(name='first',kind='success',inputFiles={'input.txt':inputs[0]},expectedFiles={'result.xlsx':expected[0]})])
        first=service.skills.propose(value)
        self.assertFalse(first['canApprove']);self.assertEqual(calls,[])
        with self.assertRaises(ValueError):service.skills.approve(dict(id=first['id'],expectedHash=first['versionHash']))
        value['parentId']=first['id'];value['examples']+=[
            dict(name='second',kind='success',inputFiles={'input.txt':inputs[1]},expectedFiles={'result.xlsx':expected[1]}),
            dict(name='missing',kind='failure',inputFiles={'input.txt':inputs[2]},expectedError='Missing amount')]
        checked=service.skills.propose(value);self.assertTrue(checked['canApprove']);self.assertFalse(checked['canRun'])
        self.assertEqual(len(calls),3)
        service.skills.approve(dict(id=checked['id'],expectedHash=checked['versionHash']))
        fresh=self.files._save('new.txt',b'7','local')
        output=service.skills.run(checked['id'],{'input.txt':fresh['id']},checked['scopeId'])
        final=self.files.bytes(output['files'][0]['id'])
        self.assertTrue(compare_output(final,result(14),'result.xlsx','workbook'))
        self.assertTrue(self.files.validate(output['files'][0]['id'])['structuralPassed'])


if __name__ == '__main__':unittest.main()
