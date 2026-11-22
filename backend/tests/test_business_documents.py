"""Structured rendering, deterministic amounts and durable revision integration."""
from network_guard import no_external_network
from copy import deepcopy
from io import BytesIO
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from task_backend.business_documents import normalize_document,patch_document,render_pdf,render_docx
from task_backend.document_formats import inspect_docx
from task_backend.service import BackendService
from task_backend.agent_tools import AgentTools


def specimen():
    return dict(title='MERIDIAN COMPONENTS',subtitle='DRAFT QUOTATION / M-204',recipient='Prepared for: Cedar Labs',metadata=['Reference: C-021','USD; prices include tax; delivery not confirmed'],columns=[
        dict(key='sku',label='Item',width=.8),dict(key='description',label='Description',width=2.4),dict(key='quantity',label='Quantity',width=1),dict(key='unit',label='Unit price',width=1),dict(key='amount',label='Amount',width=1)],rows=[
        dict(id='line_a',values=dict(sku='00071',description='Control adapter',quantity='200',unit='2.825',amount='wrong')),
        dict(id='line_b',values=dict(sku='00092',description='Coupling',quantity='30',unit='7.91'))],
        calculation=dict(quantityColumn='quantity',unitPriceColumn='unit',amountColumn='amount',adjustment='18',currency='USD'),
        notes=['Customer request only. Availability remains unconfirmed.'],footer='Prepared by Sales Operations')


class BusinessDocumentTests(unittest.TestCase):
    def test_currency_schema_rejects_placeholder_strings_before_creating_or_revising_files(self):
        from task_backend.business_documents import BusinessDocument,DocumentCalculationPatch
        self.assertIn('pattern',BusinessDocument.model_json_schema()['$defs']['DocumentCalculation']['properties']['currency'])
        with tempfile.TemporaryDirectory() as directory:
            service=BackendService(Path(directory),worker=True);ctx=AgentTools(service,'currency','chat')
            try:
                first=ctx.create_business_document('valid','pdf',specimen(),call_id='valid')
                before=service.files.bytes(first['id'])
                for bad in ('USDasd','USDCls_placeholder','USDX9b','USD2bad','usd','USD ','美元'):
                    value=specimen();value['calculation']['currency']=bad
                    with self.subTest(currency=bad):
                        with self.assertRaises(ValueError):ctx.create_business_document('bad','pdf',value,call_id='create-'+bad)
                        with self.assertRaises(ValueError):ctx.revise_business_document(first['id'],'bad',dict(calculation=dict(currency=bad)),call_id='revise-'+bad)
                        with self.assertRaises(ValueError):DocumentCalculationPatch.model_validate(dict(currency=bad))
                self.assertEqual(len(service.files.list_files()),1)
                self.assertEqual(service.files.bytes(first['id']),before)
                self.assertEqual(service.files.get(first['id'])['lifecycleStatus'],'active')
                for currency in ('','USD','HKD','EUR'):
                    value=specimen();value['calculation']['currency']=currency
                    self.assertEqual(normalize_document(value)[1]['currency'],currency)
            finally:ctx.close();service.close()

    def test_unspecified_shipping_is_hidden_but_explicit_waiver_and_legacy_labels_remain(self):
        from task_backend.business_documents import _summaries
        from pypdf import PdfReader
        from docx import Document
        value=specimen();value['calculation'].pop('adjustment')
        spec,report=normalize_document(value)
        self.assertEqual(len(_summaries(spec,report)),2)
        pdf=PdfReader(BytesIO(render_pdf(spec,report))).pages[0].extract_text()
        self.assertNotIn('Shipping',pdf);self.assertNotIn('Adjustment',pdf)
        doc=Document(BytesIO(render_docx(spec,report)))
        self.assertEqual(len(doc.tables[-1].columns),3) # two cards and one spacer
        explicit=specimen();explicit['calculation']['adjustmentLabel']='SHIPPING'
        spec,report=normalize_document(explicit)
        waived,proof=patch_document(spec,dict(calculation=dict(adjustment='0')))
        self.assertIn(('SHIPPING','0.00'),_summaries(waived,proof))
        self.assertEqual(len(_summaries(waived,proof)),3)
        self.assertEqual(len(Document(BytesIO(render_docx(waived,proof))).tables[-1].columns),5)
        self.assertIn('SHIPPING',PdfReader(BytesIO(render_pdf(waived,proof))).pages[0].extract_text())
        # Old normalized specs stored Shipping explicitly: opening or revising
        # them keeps that established display; no rewrite of old artifacts.
        legacy=deepcopy(waived);legacy['calculation']['adjustmentLabel']='Shipping'
        updated,proof=patch_document(legacy,dict(notes=['Other edit']))
        self.assertIn(('Shipping','0.00'),_summaries(updated,proof))
        hidden,proof=patch_document(updated,dict(calculation=dict(adjustmentLabel='')))
        self.assertEqual(len(_summaries(hidden,proof)),2)
        nonzero,proof=patch_document(hidden,dict(calculation=dict(adjustment='18')))
        self.assertIn(('Adjustment','18.00'),_summaries(nonzero,proof))

    def test_decimal_preserves_precision_before_extending_and_no_implicit_tax(self):
        spec,report=normalize_document(specimen())
        self.assertEqual(report['subtotal'],'802.30');self.assertEqual(report['total'],'820.30')
        self.assertEqual(spec['rows'][0]['values']['amount'],'565.00')
        self.assertEqual(spec['rows'][0]['values']['sku'],'00071')
        self.assertEqual(spec['rows'][0]['values']['unit'],'2.825')
        self.assertEqual(report['rows'][1]['amount'],'237.30')

    def test_partial_correction_preserves_layout_other_rows_and_one_off_fee(self):
        spec,_=normalize_document(specimen())
        changed,proof=patch_document(spec,dict(rows=[dict(id='line_b',values=dict(quantity='12'))],calculation=dict(adjustment='0'),style=dict(headerColor='#112244')))
        self.assertEqual(changed['rows'][0],spec['rows'][0])
        self.assertEqual(changed['rows'][1]['values']['amount'],'94.92')
        self.assertEqual(changed['columns'],spec['columns'])
        self.assertEqual(changed['style']['headerColor'],'#112244')
        self.assertEqual(changed['style']['summaryLayout'],'cards')
        self.assertEqual(proof['total'],'659.92')
        self.assertEqual(spec['calculation']['adjustment'],'18')

    def test_missing_or_ambiguous_price_is_not_zero_and_unknown_patch_does_not_mutate(self):
        for invalid in ['', '19 per box', 'NaN', '1e2', '-3']:
            value=specimen();value['rows'][0]['values']['unit']=invalid
            with self.subTest(invalid=invalid),self.assertRaises(ValueError):normalize_document(value)
        original,_=normalize_document(specimen());saved=deepcopy(original)
        for change in [dict(rows=[dict(id='unknown',values=dict(quantity='2'))]),dict(removeRowIds=['unknown']),dict(rows=[dict(id='line_a',values=dict(nonexistent='2'))])]:
            with self.assertRaises(ValueError):patch_document(original,change)
            self.assertEqual(original,saved)

    def test_render_pdf_actual_header_and_three_cards(self):
        import pypdfium2 as pdfium
        from pypdf import PdfReader
        spec,report=normalize_document(specimen());data=render_pdf(spec,report)
        parsed=PdfReader(BytesIO(data));self.assertEqual(len(parsed.pages),1)
        content=parsed.pages[0].extract_text();self.assertIn('00071',content);self.assertIn('820.30',content)
        document=pdfium.PdfDocument(data);page=document[0];bitmap=page.render(scale=1);picture=bitmap.to_pil().convert('RGB')
        self.assertEqual(picture.getpixel((20,20)),(38,59,74))
        # Three distinct light summary cards share one horizontal row and have gaps.
        target=(240,243,245);segments=[]
        for y in range(200,picture.height-80):
            row=[picture.getpixel((x,y))==target for x in range(picture.width)]
            starts=[x for x in range(1,len(row)) if row[x] and not row[x-1]]
            if len(starts)==3:segments=starts;break
        self.assertEqual(len(segments),3)
        bitmap.close();page.close();document.close()

    def test_word_has_configurable_column_widths_and_real_financial_values(self):
        from docx import Document
        spec,report=normalize_document(specimen());data=render_docx(spec,report)
        info=inspect_docx(data);doc=Document(BytesIO(data))
        self.assertEqual(len(info['tables']),3) # header, line items, summaries
        self.assertEqual(info['tables'][1]['rows'][1][0]['text'],'00071')
        self.assertEqual(info['tables'][1]['rows'][1][-1]['text'],'565.00')
        self.assertGreater(doc.tables[1].columns[1].width,doc.tables[1].columns[0].width)
        self.assertIn('820.30',info['tables'][2]['rows'][0][4]['text'])

    def test_long_documents_paginate_with_repeated_header(self):
        from pypdf import PdfReader
        value=specimen();value['rows']=[dict(id='row_'+str(i),values=dict(sku=str(i).zfill(5),description='A long description '*3,quantity='2',unit='1.005')) for i in range(60)]
        spec,report=normalize_document(value);pdf=PdfReader(BytesIO(render_pdf(spec,report)))
        self.assertGreater(len(pdf.pages),1)
        for page in pdf.pages:self.assertIn('MERIDIAN COMPONENTS',page.extract_text())
        self.assertEqual(report['subtotal'],'120.60')

    def test_generated_word_edit_retires_only_generated_parent(self):
        from task_backend.mail_files import make_docx
        with tempfile.TemporaryDirectory() as directory:
            service=BackendService(Path(directory),worker=True);ctx=AgentTools(service,'word-family','chat')
            try:
                source=service.files._save('source.docx',make_docx('Report','Pending'),'local')
                first=ctx.edit_word(source['id'],'draft',[dict(target='p1',expected='Pending',text='Checking')],call_id='copy')
                second=ctx.edit_word(first['id'],'draft',[dict(target='p1',expected='Checking',text='Checked')],call_id='update')
                self.assertEqual({r['id'] for r in service.files.list_files()},{source['id'],second['id']})
                self.assertEqual(service.files.get(first['id'])['lifecycleStatus'],'superseded')
                self.assertNotIn('lifecycleStatus',service.files.get(source['id']))
                with self.assertRaisesRegex(ValueError,'替代'):
                    ctx.edit_word(first['id'],'stale',[dict(target='p1',expected='Checking',text='Stale')],call_id='stale')
            finally:ctx.close();service.close()

    def test_service_reopen_targeted_revision_idempotence_and_stale_id(self):
        with tempfile.TemporaryDirectory() as directory,no_external_network():
            service=BackendService(Path(directory),worker=True);ctx=AgentTools(service,'render','chat')
            try:
                first=ctx.create_business_document('quote','pdf',specimen(),call_id='first')
                source_bytes=service.files.bytes(first['id']);first_id=first['id']
                spec=service.files.inspect(first_id)['businessDocument'];self.assertEqual(spec['calculation']['total'],'820.30')
            finally:ctx.close();service.close()
            service=BackendService(Path(directory),worker=True);ctx=AgentTools(service,'revision','chat')
            try:
                args=dict(file_id=first_id,name='quote',changes=dict(calculation=dict(adjustment='0')),expected_revision=1,call_id='second')
                second=ctx.revise_business_document(**args)
                self.assertEqual(second['report']['total'],'802.30')
                self.assertEqual(service.files.bytes(first_id),source_bytes)
                self.assertEqual(service.files.get(first_id)['lifecycleStatus'],'superseded')
                self.assertEqual([r['id'] for r in service.files.list_files()],[second['id']])
                self.assertEqual(service.files.get(second['id'])['revision'],2)
                # Resume after version pointer committed but tool result did not.
                ctx.db.execute('UPDATE agent_tool_results SET result=NULL WHERE id=?',(second['id'],))
                self.assertEqual(ctx.revise_business_document(**args)['id'],second['id'])
                with self.assertRaisesRegex(ValueError,'替代'):
                    ctx.revise_business_document(first_id,'stale',dict(calculation=dict(adjustment='99')),expected_revision=1,call_id='stale')
                validation=service.files.validate(second['id'])
                self.assertTrue(validation['structuralPassed']);self.assertTrue(validation['rendered'])
                self.assertEqual(validation['businessCalculation']['total'],'802.30')
            finally:ctx.close();service.close()


if __name__=='__main__':unittest.main()
