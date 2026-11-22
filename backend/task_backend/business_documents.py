"""Data-driven documents using ReportLab and python-docx, not a new agent loop.

The model identifies business facts and reference appearance; the renderer owns
layout and Decimal arithmetic. These are managed, single-use documents. Turning
one into a reusable executable skill remains a separate reviewed operation.
"""
from copy import deepcopy
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP, localcontext
from io import BytesIO
from pathlib import Path
from typing import Annotated, Literal
from xml.sax.saxutils import escape
import json
import re

from pydantic import BaseModel, ConfigDict, Field, StrictStr


class StrictModel(BaseModel):
    model_config = ConfigDict(extra='forbid')


Short = Annotated[StrictStr, Field(max_length=300)]
Key = Annotated[StrictStr, Field(pattern=r'^[A-Za-z0-9_-]{1,64}$')]
Cell = Annotated[StrictStr, Field(max_length=2000)]
Color = Annotated[StrictStr, Field(pattern=r'^#[0-9a-fA-F]{6}$')]
Currency = Annotated[StrictStr, Field(pattern=r'^(?:[A-Z]{3})?$', max_length=3,
    description='Source-confirmed uppercase three-letter currency code (for example USD, HKD, EUR), or empty if unspecified. No placeholders, suffixes or display labels.')]


class DocumentColumn(StrictModel):
    key: Key
    label: Short
    width: float = Field(default=1, ge=0.2, le=20, description='Relative column width; give descriptions/dates more room.')
    align: Literal['left', 'right', 'center'] = 'left'


class DocumentRow(StrictModel):
    id: Key
    values: dict[Key, Cell] = Field(max_length=12)


class DocumentStyle(StrictModel):
    page: Literal['A4', 'letter'] = 'A4'
    landscape: bool = False
    headerColor: Color = '#263B4A'
    headerTextColor: Color = '#FFFFFF'
    tableHeaderColor: Color = '#E8EEF1'
    summaryColor: Color = '#F0F3F5'
    textColor: Color = '#263640'
    fontSize: float = Field(default=9, ge=8, le=13)
    headerBand: bool = True
    summaryLayout: Literal['cards', 'stacked'] = 'cards'


class DocumentCalculation(StrictModel):
    quantityColumn: Key
    unitPriceColumn: Key
    amountColumn: Key
    decimals: int = Field(default=2, ge=0, le=4)
    adjustment: Short = Field(default='0', description='Confirmed shipping/discount amount. Negative means discount. No implicit tax added.')
    subtotalLabel: Short = 'Subtotal'
    adjustmentLabel: Short = Field(default='', description='Use an explicit confirmed charge label. Empty with a zero adjustment hides the row; never invent free shipping. Nonzero unlabeled adjustments display as Adjustment.')
    totalLabel: Short = 'Total'
    currency: Currency = ''


class BusinessDocument(StrictModel):
    title: Short
    subtitle: Short = ''
    recipient: Short = ''
    metadata: list[Short] = Field(default_factory=list, max_length=8)
    columns: list[DocumentColumn] = Field(min_length=1, max_length=12)
    rows: list[DocumentRow] = Field(min_length=1, max_length=200)
    notes: list[Cell] = Field(default_factory=list, max_length=12)
    footer: Short = ''
    style: DocumentStyle = Field(default_factory=DocumentStyle)
    calculation: DocumentCalculation | None = Field(default=None, description='Use only after unit basis is known. Qty must be in the same units as unit price. Amount cells and totals computed by Decimal, not model-supplied arithmetic.')


class DocumentCalculationPatch(StrictModel):
    quantityColumn: Key | None = None
    unitPriceColumn: Key | None = None
    amountColumn: Key | None = None
    decimals: int | None = Field(default=None, ge=0, le=4)
    adjustment: Short | None = None
    subtotalLabel: Short | None = None
    adjustmentLabel: Short | None = None
    totalLabel: Short | None = None
    currency: Currency | None = None


class BusinessDocumentPatch(StrictModel):
    title: Short | None = None
    subtitle: Short | None = None
    recipient: Short | None = None
    metadata: list[Short] | None = Field(default=None, max_length=8)
    notes: list[Cell] | None = Field(default=None, max_length=12)
    footer: Short | None = None
    style: DocumentStyle | None = None
    columns: list[DocumentColumn] | None = Field(default=None, min_length=1, max_length=12)
    rows: list[DocumentRow] | None = Field(default=None, min_length=1, max_length=200, description='Partial updates: stable row id and only changed cells. Unmentioned rows/cells are preserved. No positional guessing.')
    removeRowIds: list[Key] = Field(default_factory=list, max_length=200)
    calculation: DocumentCalculationPatch | None = None


def decimal_value(value, label):
    if not isinstance(value, str) or not re.fullmatch(r'-?\d{1,16}(?:\.\d{1,8})?', value):
        raise ValueError(label+'必须是明确的十进制数字字符串，不能是空值、单位文字或推测值')
    try:
        result=Decimal(value)
    except InvalidOperation:
        raise ValueError(label+'不是有效数字') from None
    if not result.is_finite():raise ValueError(label+'不是有限数字')
    return result


def normalize_document(value):
    spec=BusinessDocument.model_validate(value).model_dump()
    if len(json.dumps(spec,ensure_ascii=False))>120000:raise ValueError('文档数据过大，请拆分')
    keys=[c['key'] for c in spec['columns']]
    if len(set(keys))!=len(keys):raise ValueError('文档列标识重复')
    ids=[r['id'] for r in spec['rows']]
    if len(set(ids))!=len(ids):raise ValueError('文档行标识重复')
    for row in spec['rows']:
        if set(row['values'])-set(keys):raise ValueError('文档包含未定义列')
    report={'calculated':False,'layout':'structured-document-v1','originalLayoutPreserved':False}
    calc=spec['calculation']
    if calc:
        fields=[calc[k] for k in ('quantityColumn','unitPriceColumn','amountColumn')]
        if len(set(fields))!=3 or not set(fields)<=set(keys):raise ValueError('计算列必须是三个不同的现有列')
        quantum=Decimal(1).scaleb(-calc['decimals']);subtotal=Decimal('0');amounts=[]
        for row in spec['rows']:
            qty=decimal_value(row['values'].get(fields[0],''),'数量')
            price=decimal_value(row['values'].get(fields[1],''),'单价')
            if qty<0 or price<0:raise ValueError('数量和单价不能为负数；退货请使用专用业务流程')
            with localcontext() as context:
                context.prec=60
                amount=(qty*price).quantize(quantum,rounding=ROUND_HALF_UP)
            if abs(amount)>Decimal('9999999999999999'):raise ValueError('行金额超过处理范围')
            row['values'][fields[2]]=format(amount,'f');subtotal+=amount
            amounts.append({'rowId':row['id'],'quantity':str(qty),'unitPrice':str(price),'amount':format(amount,'f')})
        adjustment=decimal_value(calc['adjustment'],'调整金额').quantize(quantum,rounding=ROUND_HALF_UP)
        report.update(calculated=True,rows=amounts,subtotal=format(subtotal,'f'),adjustment=format(adjustment,'f'),total=format(subtotal+adjustment,'f'),currency=calc['currency'],rounding='ROUND_HALF_UP per line; preserve unit price precision before multiplication',taxHandling='No implicit tax conversion; input prices are used as provided.')
    return spec,report


def patch_document(original, changes):
    patch=BusinessDocumentPatch.model_validate(changes).model_dump(exclude_unset=True)
    spec=deepcopy(original)
    row_edits=patch.pop('rows',None);remove=set(patch.pop('removeRowIds',[]))
    ids={r['id'] for r in spec['rows']}
    if not remove<=ids:raise ValueError('待移除行不存在，请先inspect_file核对')
    if row_edits is not None:
        if len({r['id'] for r in row_edits})!=len(row_edits):raise ValueError('修订行标识重复')
        for edit in row_edits:
            if edit['id'] not in ids or edit['id'] in remove:raise ValueError('待修改行不存在或同时被移除')
            next(r for r in spec['rows'] if r['id']==edit['id'])['values'].update(edit['values'])
    spec['rows']=[r for r in spec['rows'] if r['id'] not in remove]
    for key,value in patch.items():
        if value is None:raise ValueError('修订字段不能为null；保留原值请省略此字段')
        # Partial style/calculation preserves attributes omitted by model.
        if key in ('style','calculation') and spec.get(key):spec[key].update(value)
        else:spec[key]=value
    return normalize_document(spec)


def _summaries(spec,report):
    if not report['calculated']:return []
    calc=spec['calculation']
    summaries=[(calc['subtotalLabel'],report['subtotal'])]
    label=calc['adjustmentLabel'].strip()
    if label or Decimal(report['adjustment'])!=0:
        summaries.append((label or 'Adjustment',report['adjustment']))
    summaries.append((calc['totalLabel']+(' '+calc['currency'] if calc['currency'] else ''),report['total']))
    return summaries


def render_pdf(spec,report):
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4,letter,landscape
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.enums import TA_LEFT,TA_RIGHT,TA_CENTER
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    from reportlab.platypus import SimpleDocTemplate,Paragraph,Spacer,LongTable,Table,TableStyle,KeepTogether
    font='TaskManagerBusinessNotoSC'
    if font not in pdfmetrics.getRegisteredFontNames():pdfmetrics.registerFont(TTFont(font,str(Path(__file__).parent/'assets/NotoSansSC.ttf')))
    style=spec['style'];size=A4 if style['page']=='A4' else letter
    if style['landscape']:size=landscape(size)
    width,height=size;margin=36;usable=width-2*margin
    base=ParagraphStyle('body',fontName=font,fontSize=style['fontSize'],leading=style['fontSize']*1.5,textColor=colors.HexColor(style['textColor']),wordWrap='CJK',spaceAfter=8)
    def para(value,**kw):return Paragraph(escape(value).replace('\n','<br/>'),ParagraphStyle('p',parent=base,**kw))
    story=[]
    if not style['headerBand']:
        story.extend([para(spec['title'],fontSize=20,leading=27),para(spec['subtitle'])])
    if spec['recipient']:story.append(para(spec['recipient'],fontSize=12,leading=18))
    story.extend(para(v) for v in spec['metadata']);story.append(Spacer(1,16))
    weights=sum(c['width'] for c in spec['columns']);widths=[usable*c['width']/weights for c in spec['columns']]
    rows=[[para(c['label']) for c in spec['columns']]]
    for row in spec['rows']:
        rows.append([para(row['values'].get(c['key'],''),alignment={'left':TA_LEFT,'right':TA_RIGHT,'center':TA_CENTER}[c['align']]) for c in spec['columns']])
    table=LongTable(rows,colWidths=widths,repeatRows=1,hAlign='LEFT')
    table.setStyle(TableStyle([('BACKGROUND',(0,0),(-1,0),colors.HexColor(style['tableHeaderColor'])),('VALIGN',(0,0),(-1,-1),'TOP'),('LINEBELOW',(0,0),(-1,-1),.4,colors.HexColor('#CBD5DC')),('LEFTPADDING',(0,0),(-1,-1),6),('RIGHTPADDING',(0,0),(-1,-1),6),('TOPPADDING',(0,0),(-1,-1),10),('BOTTOMPADDING',(0,0),(-1,-1),10)]))
    story.extend([table,Spacer(1,24)])
    totals=_summaries(spec,report)
    if totals:
        if style['summaryLayout']=='cards':
            card_width=(usable-8*(len(totals)-1))/len(totals);cards=[]
            for label,value in totals:
                card=Table([[para(label,fontSize=8,leading=12)],[para(value,fontSize=18,leading=24)]],colWidths=[card_width])
                card.setStyle(TableStyle([('BACKGROUND',(0,0),(-1,-1),colors.HexColor(style['summaryColor'])),('LEFTPADDING',(0,0),(-1,-1),12),('RIGHTPADDING',(0,0),(-1,-1),12),('TOPPADDING',(0,0),(-1,0),10),('BOTTOMPADDING',(0,-1),(-1,-1),10)]))
                cards.append(card)
            cells=[];summary_widths=[]
            for card in cards:
                if cells:cells.append('');summary_widths.append(8)
                cells.append(card);summary_widths.append(card_width)
            summary=Table([cells],colWidths=summary_widths)
            summary.setStyle(TableStyle([('LEFTPADDING',(0,0),(-1,-1),0),('RIGHTPADDING',(0,0),(-1,-1),0),('VALIGN',(0,0),(-1,-1),'TOP')]))
        else:summary=Table([[para(k),para(v,alignment=TA_RIGHT)] for k,v in totals],colWidths=[usable*.65,usable*.35])
        story.extend([KeepTogether(summary),Spacer(1,24)])
    story.extend(para(v) for v in spec['notes'])
    stream=BytesIO();doc=SimpleDocTemplate(stream,pagesize=size,leftMargin=margin,rightMargin=margin,topMargin=140 if style['headerBand'] else 36,bottomMargin=62,title=spec['title'],author='Task Manager')
    def decorate(canvas,document):
        canvas.saveState()
        if style['headerBand']:
            canvas.setFillColor(colors.HexColor(style['headerColor']));canvas.rect(0,height-116,width,116,fill=1,stroke=0)
            title=para(spec['title'],fontSize=20,leading=26,textColor=colors.HexColor(style['headerTextColor']));_,th=title.wrap(usable,70)
            if th>70:raise ValueError('抬头过长，请缩短或选择无色块标题')
            title.drawOn(canvas,margin,height-28-th)
            sub=para(spec['subtitle'],fontSize=10,leading=14,textColor=colors.HexColor(style['headerTextColor']));_,sh=sub.wrap(usable,25)
            if sh>28:raise ValueError('副标题过长，请移到metadata')
            sub.drawOn(canvas,margin,height-100)
        footer=para(spec['footer'],fontSize=8,leading=11);_,fh=footer.wrap(usable-30,40)
        if fh>40:raise ValueError('页脚过长')
        footer.drawOn(canvas,margin,25)
        canvas.setFont(font,8);canvas.setFillColor(colors.HexColor(style['textColor']));canvas.drawRightString(width-margin,25,str(document.page));canvas.restoreState()
    doc.build(story,onFirstPage=decorate,onLaterPages=decorate)
    return stream.getvalue()


def render_docx(spec,report):
    from docx import Document
    from docx.shared import Inches,Pt,RGBColor
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn
    from .mail_files import _plain_docx
    from .document_formats import _refresh_embedded_font
    doc=Document(BytesIO(_plain_docx('','')))
    for child in list(doc.element.body):
        if child.tag!=qn('w:sectPr'):doc.element.body.remove(child)
    style=spec['style'];section=doc.sections[0]
    w,h=(8.27,11.69) if style['page']=='A4' else (8.5,11)
    if style['landscape']:w,h=h,w
    section.page_width=Inches(w);section.page_height=Inches(h)
    section.left_margin=section.right_margin=Inches(.5);section.top_margin=section.bottom_margin=Inches(.5)
    usable=w-1
    normal=doc.styles['Normal'];normal.font.size=Pt(style['fontSize']);normal.paragraph_format.space_after=Pt(6)
    normal.paragraph_format.line_spacing=1.1
    def paragraph(container,value,size=None,color=None):
        p=container.add_paragraph() if hasattr(container,'add_paragraph') else container
        p.paragraph_format.space_after=Pt(6)
        for i,line in enumerate(value.split('\n')):
            run=p.add_run(('\n' if i else '')+line);run.font.size=Pt(size or style['fontSize']);run.font.color.rgb=RGBColor.from_string((color or style['textColor'])[1:])
            run.font.name='Noto Sans SC';run._r.get_or_add_rPr().get_or_add_rFonts().set(qn('w:eastAsia'),'Noto Sans SC')
        return p
    def shade(cell,color):
        shd=OxmlElement('w:shd');shd.set(qn('w:fill'),color[1:]);cell._tc.get_or_add_tcPr().append(shd)
    if style['headerBand']:
        header=doc.add_table(rows=1,cols=1);header.autofit=False;header.columns[0].width=Inches(usable);cell=header.cell(0,0);shade(cell,style['headerColor'])
        cell.paragraphs[0]._element.getparent().remove(cell.paragraphs[0]._element)
        paragraph(cell,spec['title'],20,style['headerTextColor']);paragraph(cell,spec['subtitle'],10,style['headerTextColor'])
    else:paragraph(doc,spec['title'],20);paragraph(doc,spec['subtitle'])
    if spec['recipient']:paragraph(doc,spec['recipient'],12)
    for value in spec['metadata']:paragraph(doc,value)
    table=doc.add_table(rows=1,cols=len(spec['columns']));table.autofit=False
    weights=sum(c['width'] for c in spec['columns'])
    for i,c in enumerate(spec['columns']):table.columns[i].width=Inches(usable*c['width']/weights)
    repeat=OxmlElement('w:tblHeader');table.rows[0]._tr.get_or_add_trPr().append(repeat)
    matrix=[[c['label'] for c in spec['columns']]]+[[r['values'].get(c['key'],'') for c in spec['columns']] for r in spec['rows']]
    for ri,row in enumerate(matrix):
        cells=table.rows[0].cells if ri==0 else table.add_row().cells
        for ci,value in enumerate(row):
            cells[ci].width=Inches(usable*spec['columns'][ci]['width']/weights)
            p=cells[ci].paragraphs[0];paragraph(p,value)
            p.alignment={'left':WD_ALIGN_PARAGRAPH.LEFT,'right':WD_ALIGN_PARAGRAPH.RIGHT,'center':WD_ALIGN_PARAGRAPH.CENTER}[spec['columns'][ci]['align']]
            if ri==0:shade(cells[ci],style['tableHeaderColor'])
        no_split=OxmlElement('w:cantSplit');table.rows[ri]._tr.get_or_add_trPr().append(no_split)
    totals=_summaries(spec,report)
    if totals:
        paragraph(doc,'')
        if style['summaryLayout']=='cards':
            summary=doc.add_table(rows=1,cols=len(totals)*2-1);summary.autofit=False
            width=(usable-.08*(len(totals)-1))/len(totals)
            widths=[width if i%2==0 else .08 for i in range(len(totals)*2-1)]
            for i,width in enumerate(widths):
                summary.columns[i].width=Inches(width);summary.cell(0,i).width=Inches(width)
            for i,(label,value) in enumerate(totals):
                cell=summary.cell(0,i*2);shade(cell,style['summaryColor']);paragraph(cell.paragraphs[0],label,8);paragraph(cell,value,17)
        else:
            for label,value in totals:paragraph(doc,label+': '+value)
    for value in spec['notes']:paragraph(doc,value)
    paragraph(section.footer.paragraphs[0],spec['footer'],8)
    out=BytesIO();doc.save(out);return _refresh_embedded_font(out.getvalue())
