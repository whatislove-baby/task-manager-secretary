"""Structured document inspection and edits using python-docx and markdown-it-py.

Node IDs are stable within an immutable source version. Edits require exact old
text and never regenerate unrelated XML, tables, styles or image relationships.
"""
from io import BytesIO
from pathlib import Path
from difflib import SequenceMatcher
import json
import re
import math
import zipfile
from xml.etree import ElementTree as ET

W = 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'
CJK = re.compile(r'[\u2e80-\u9fff\uf900-\ufaff\uff00-\uffef\U00020000-\U0003134f]')


def check_docx(data, rendering=False):
    with zipfile.ZipFile(BytesIO(data)) as archive:
        items = archive.infolist()
        if len(items)>1000 or sum(v.file_size for v in items)>20*1024*1024 or any(v.file_size>(12*1024*1024 if v.filename.startswith('word/fonts/') else 8*1024*1024) for v in items):
            raise ValueError('Word 文档解压体积或文件数超过上限')
        if len({v.filename for v in items})!=len(items):raise ValueError('Word 文档包含重复 ZIP 路径')
        if any('vbaproject' in v.filename.lower() for v in items):raise ValueError('暂不支持含宏的 Word 文档')
        for item in items:
            if item.filename.endswith(('.xml','.rels')):
                raw=archive.read(item)
                if b'<!DOCTYPE' in raw.upper() or b'<!ENTITY' in raw.upper():raise ValueError('Word 文档 XML 含不支持的实体定义')
                if rendering:
                    root=ET.fromstring(raw)
                    if item.filename.endswith('.rels'):
                        for rel in root:
                            if rel.get('TargetMode')=='External' and not rel.get('Type','').endswith('/hyperlink'):
                                raise ValueError('文档含外部资源链接，不能自动渲染；请先保存为自包含副本')
                    if any(e.tag.rsplit('}',1)[-1] in ('altChunk','object','OLEObject') for e in root.iter()):
                        raise ValueError('文档含嵌入对象，不能自动渲染')
                    instructions=' '.join((e.text or '') for e in root.iter() if e.tag.rsplit('}',1)[-1]=='instrText')
                    if re.search(r'\b(DDE|DDEAUTO|INCLUDEPICTURE|INCLUDETEXT|LINK|DATABASE)\b',instructions,re.I):
                        raise ValueError('文档含动态外部字段，不能自动渲染')
        return archive.read('word/document.xml')


def inspect_docx(data):
    from docx import Document
    from docx.enum.style import WD_STYLE_TYPE
    check_docx(data)
    doc=Document(BytesIO(data))
    paragraphs=[dict(id=f'p{i}',text=p.text,style=p.style.name if p.style is not None else '',
                     format=dict(pageBreakBefore=p.paragraph_format.page_break_before,
                                 keepWithNext=p.paragraph_format.keep_with_next,
                                 keepTogether=p.paragraph_format.keep_together)) for i,p in enumerate(doc.paragraphs)]
    tables=[]
    for i,t in enumerate(doc.tables):
        rows=[]
        for ri,row in enumerate(t.rows):
            rows.append([dict(id=f't{i}.r{ri}.c{ci}',text=c.text) for ci,c in enumerate(row.cells)])
        tables.append(dict(id=f't{i}',rows=rows,
                           columnWidthsCm=[round(c.width.cm,3) if c.width is not None else None for c in t.columns]))
    images=[dict(id=f'image{i}',width=int(s.width),height=int(s.height)) for i,s in enumerate(doc.inline_shapes)]
    headers=[s.header.paragraphs for s in doc.sections]
    footers=[s.footer.paragraphs for s in doc.sections]
    return dict(paragraphs=paragraphs,tables=tables,images=images,
                paragraphStyles=[s.name for s in doc.styles if s.type==WD_STYLE_TYPE.PARAGRAPH],
                headers=[[p.text for p in part] for part in headers],
                footers=[[p.text for p in part] for part in footers],
                warnings=['结构读取包含正文段落、表格、页眉页脚；图片内容及浮动布局请查看页面。嵌套表格、批注与修订语义未完整展开。'])


def _set_paragraph(paragraph,value):
    # Retain formatting at unchanged character spans. A whole-paragraph rewrite
    # uses the dominant replaced run, not a short bold label at its beginning.
    if paragraph._p.xpath('./w:hyperlink|./w:ins|./w:del|./w:fldSimple|.//w:fldChar|.//w:instrText'):
        raise ValueError('目标段落含超链接、字段或修订，请选择普通段落；未保存修改')
    runs=paragraph.runs or [paragraph.add_run()]
    owner=[i for i,run in enumerate(runs) for _ in run.text]
    old=''.join(run.text for run in runs);assigned=['']*len(runs)
    # Anchor the common prefix/suffix first. difflib alone may move a shared
    # space out of a bold label into the suffix of an insertion.
    prefix=0
    while prefix<min(len(old),len(value)) and old[prefix]==value[prefix]:prefix+=1
    suffix=0
    while suffix<min(len(old),len(value))-prefix and old[-suffix-1]==value[-suffix-1]:suffix+=1
    old_end=len(old)-suffix;new_end=len(value)-suffix
    operations=[('equal',0,prefix,0,prefix)]
    operations.extend((op,a+prefix,b+prefix,c+prefix,d+prefix) for op,a,b,c,d in SequenceMatcher(None,old[prefix:old_end],value[prefix:new_end],autojunk=False).get_opcodes())
    if suffix:operations.append(('equal',old_end,len(old),new_end,len(value)))
    for op,a,b,c,d in operations:
        if op=='equal':
            for pos,char in zip(range(a,b),value[c:d]):assigned[owner[pos]]+=char
        elif op in ('replace','insert'):
            affected=owner[a:b]
            # At a run boundary prefer the following run (e.g. after a bold
            # label) rather than leaking the preceding label's emphasis.
            target=max(set(affected),key=lambda i:(affected.count(i),i)) if affected else (owner[a] if a<len(owner) else owner[-1] if owner else 0)
            assigned[target]+=value[c:d]
    for run in paragraph.runs:
        for node in list(run._r):
            if node.tag in (f'{{{W}}}t',f'{{{W}}}tab',f'{{{W}}}br',f'{{{W}}}cr'):
                run._r.remove(node)
    from docx.oxml.ns import qn
    for run,new_text in zip(runs,assigned):
        if CJK.search(new_text):
            fonts=run._r.get_or_add_rPr().get_or_add_rFonts()
            fonts.set(qn('w:eastAsia'),'Noto Sans SC')
            fonts.attrib.pop(qn('w:eastAsiaTheme'),None)
        # add_text preserves rPr and embedded drawing children.
        for i,line in enumerate(new_text.split('\n')):
            if i:run.add_break()
            for j,segment in enumerate(line.split('\t')):
                if j:run.add_tab()
                run.add_text(segment)


def _refresh_embedded_font(data):
    """Embed/extend CJK glyphs using python-docx's OPC relationship allocator.

    Imported Arial templates usually have no embedded CJK font. Adding only an
    eastAsia name leaves blank glyphs in the bundled LibreOffice runtime. Reuse
    existing app-owned font parts, otherwise attach a new one without stealing
    another font's relationship ID or regenerating the document body.
    """
    from docx import Document
    from docx.oxml import OxmlElement,parse_xml
    from docx.oxml.ns import qn
    from docx.opc.part import Part
    from docx.opc.packuri import PackURI
    from docx.opc.constants import RELATIONSHIP_TYPE as RT,CONTENT_TYPE as CT
    from lxml import etree
    from uuid import UUID,uuid4
    with zipfile.ZipFile(BytesIO(data)) as archive:
        roots=[ET.fromstring(archive.read(n)) for n in archive.namelist() if n.startswith('word/') and n.endswith('.xml')]
        wanted=any(e.get(qn('w:eastAsia'))=='Noto Sans SC' or
                   (e.tag==qn('w:font') and e.get(qn('w:name'))=='Noto Sans SC') for root in roots for e in root.iter())
        if not wanted:return data
        # Include headers, footers and table text, including glyphs introduced
        # by earlier edits. Repeated editing must not shrink existing coverage.
        content=' '.join(' '.join(root.itertext()) for root in roots)
    doc=Document(BytesIO(data));package=doc.part.package
    try:font_table=doc.part.part_related_by(RT.FONT_TABLE)
    except KeyError:
        partname=package.next_partname('/word/fontTable%d.xml')
        font_table=Part(partname,CT.WML_FONT_TABLE,b'<w:fonts xmlns:w="'+W.encode()+b'"/>',package)
        doc.part.relate_to(font_table,RT.FONT_TABLE)
    table=parse_xml(font_table.blob)
    entry=next((f for f in table if f.tag==qn('w:font') and f.get(qn('w:name'))=='Noto Sans SC'),None)
    if entry is None:
        entry=OxmlElement('w:font');entry.set(qn('w:name'),'Noto Sans SC');table.append(entry)
        family=OxmlElement('w:family');family.set(qn('w:val'),'swiss');entry.append(family)
    embedded=entry.find(qn('w:embedRegular'));font_part=None;font_id=None
    if embedded is not None:
        relation=font_table.rels.get(embedded.get(qn('r:id')))
        if relation is not None and not relation.is_external and relation.reltype==RT.FONT:
            candidate=relation.target_part
            # A third-party font called Noto may share bytes with other fonts.
            # Only replace this application's explicitly named owned part.
            if Path(str(candidate.partname)).name.startswith('NotoSansSC'):
                try:font_id=UUID(embedded.get(qn('w:fontKey')));font_part=candidate
                except (ValueError,TypeError,AttributeError):pass
    if font_part is None:
        occupied={p.partname for p in package.parts}
        partname=PackURI('/word/fonts/NotoSansSC.odttf')
        if partname in occupied:partname=package.next_partname('/word/fonts/NotoSansSC%d.odttf')
        font_part=Part(partname,'application/vnd.openxmlformats-officedocument.obfuscatedFont',b'',package)
        font_id=uuid4()
    if embedded is None:embedded=OxmlElement('w:embedRegular');entry.append(embedded)
    embedded.set(qn('r:id'),font_table.relate_to(font_part,RT.FONT))
    embedded.set(qn('w:fontKey'),'{'+str(font_id).upper()+'}')
    from fontTools.ttLib import TTFont
    from fontTools import subset
    font=TTFont(Path(__file__).parent/'assets/NotoSansSC.ttf')
    try:
        options=subset.Options();options.name_IDs=['*'];options.name_legacy=True;options.name_languages=['*']
        subsetter=subset.Subsetter(options=options);subsetter.populate(text=content+' 0123456789');subsetter.subset(font)
        stream=BytesIO();font.save(stream);payload=bytearray(stream.getvalue())
    finally:font.close()
    key=font_id.bytes[::-1]
    for i in range(32):payload[i]^=key[i%16]
    font_part._blob=bytes(payload)
    font_table._blob=etree.tostring(table,xml_declaration=True,encoding='UTF-8',standalone=True)
    # CT_Settings orders embedding flags before proofState/defaultTabStop.
    # Inserting at the end happens to render in LibreOffice but can trigger
    # Word's XML repair dialog on otherwise valid imported templates.
    preceding=('writeProtection','view','zoom','removePersonalInformation','removeDateAndTime',
               'doNotDisplayPageBoundaries','displayBackgroundShape','printPostScriptOverText',
               'printFractionalCharacterWidth','printFormsData')
    for setting in ('embedTrueTypeFonts','saveSubsetFonts'):
        element=doc.settings.element.find(qn('w:'+setting))
        if element is None:
            element=OxmlElement('w:'+setting)
            before=set(qn('w:'+name) for name in preceding+(() if setting=='embedTrueTypeFonts' else ('embedTrueTypeFonts','embedSystemFonts')))
            index=next((i for i,node in enumerate(doc.settings.element) if node.tag not in before),len(doc.settings.element))
            doc.settings.element.insert(index,element)
        element.set(qn('w:val'),'true')
    out=BytesIO();doc.save(out);return out.getvalue()


def edit_docx_bytes(data,edits):
    from docx import Document
    check_docx(data)
    if not isinstance(edits,list) or not 1<=len(edits)<=100:raise ValueError('请提供 1 至 100 个 Word 修改项')
    doc=Document(BytesIO(data));targets=[];seen=set()
    for edit in edits:
        if not isinstance(edit,dict) or set(edit)!={'target','expected','text'}:raise ValueError('Word 修改须包含 target、expected 和 text')
        target,old,new=edit['target'],edit['expected'],edit['text']
        if not isinstance(target,str) or not isinstance(old,str) or not isinstance(new,str) or len(new)>20000:raise ValueError('Word 修改内容无效')
        if re.search(r'[\x00-\x08\x0b\x0c\x0e-\x1f]',new):raise ValueError('Word 修改含不支持的控制字符')
        try:
            match=re.fullmatch(r'p(\d+)',target)
            if match:node=doc.paragraphs[int(match[1])];kind='paragraph'
            else:
                match=re.fullmatch(r't(\d+)\.r(\d+)\.c(\d+)',target)
                if not match:raise ValueError('Word 目标节点无效，请先读取结构')
                node=doc.tables[int(match[1])].rows[int(match[2])].cells[int(match[3])];kind='cell'
            element=node._p if kind=='paragraph' else node._tc
            if element in seen:raise ValueError('修改目标重复，包含同一合并单元格时请只修改一次')
            seen.add(element)
            if node.text!=old:raise ValueError('Word 目标原文不匹配，未保存；请重新读取最新副本')
            if kind=='cell' and node.tables:raise ValueError('含嵌套表格的单元格暂不支持改写')
            targets.append((node,kind,new))
        except (IndexError,KeyError):raise ValueError('Word 目标节点不存在，请先读取结构') from None
    for node,kind,new in targets:
        if kind=='paragraph':_set_paragraph(node,new)
        else:
            _set_paragraph(node.paragraphs[0],new)
            for p in node.paragraphs[1:]:_set_paragraph(p,'')
            # Preserve paragraphs containing drawings. Empty secondary paragraphs
            # have formatting but no text; do not drop objects or relationships.
    out=BytesIO();doc.save(out);return _refresh_embedded_font(out.getvalue())


def format_docx_bytes(data,changes):
    """Patch existing body layout, retaining every other OPC part byte for byte.

    python-docx resolves paragraph styles and Word grid spans. We overlay its
    modified document XML into the original ZIP instead of resaving the package:
    opaque template parts, images, fields, headers and embedded fonts survive.
    Targets are from inspect_file on this immutable source version. No text is
    rewritten, and a failed/stale patch never produces an output file.
    """
    from docx import Document
    from docx.enum.style import WD_STYLE_TYPE
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn
    from docx.shared import Cm,Pt
    check_docx(data)
    if not isinstance(changes,list) or not 1<=len(changes)<=100:
        raise ValueError('请提供 1 至 100 个 Word 排版修改项')
    doc=Document(BytesIO(data));seen=set()
    paragraph_keys={'style','page_break_before','keep_with_next','keep_together','space_before_pt','space_after_pt'}
    table_keys={'column_widths_cm','repeat_header','keep_rows_together'}
    for change in changes:
        if not isinstance(change,dict) or not isinstance(change.get('target'),str):
            raise ValueError('排版修改需要有效 target；请先 inspect_file 读取结构')
        target=change['target']
        if target in seen:raise ValueError('排版目标重复，请合并同一目标的修改')
        seen.add(target)
        match=re.fullmatch(r'p(\d+)',target)
        if match:
            if set(change)-({'target','expected'}|paragraph_keys) or not (set(change)&paragraph_keys):
                raise ValueError('段落排版只支持现有 style、分页、保持段落和段间距；改文字请用 edit_word')
            try:paragraph=doc.paragraphs[int(match[1])]
            except IndexError:raise ValueError('Word 目标节点不存在，请先读取结构') from None
            if not isinstance(change.get('expected'),str) or paragraph.text!=change['expected']:
                raise ValueError('Word 目标原文不匹配，未保存；请重新读取最新副本')
            if 'style' in change:
                name=change['style']
                if not isinstance(name,str):raise ValueError('style 必须是模板中已有的段落样式名称')
                style=next((s for s in doc.styles if s.name==name and s.type==WD_STYLE_TYPE.PARAGRAPH),None)
                if style is None:raise ValueError('模板中不存在指定的段落样式')
                paragraph.style=style
            for key in ('page_break_before','keep_with_next','keep_together'):
                if key in change:
                    if type(change[key]) is not bool:raise ValueError(key+' 必须是布尔值')
                    setattr(paragraph.paragraph_format,key,change[key])
            for key in ('space_before_pt','space_after_pt'):
                if key in change:
                    value=change[key]
                    if type(value) not in (int,float) or not math.isfinite(value) or not 0<=value<=72:
                        raise ValueError('段间距须为 0 至 72 磅的有限数值')
                    setattr(paragraph.paragraph_format,key.removesuffix('_pt'),Pt(value))
            continue
        match=re.fullmatch(r't(\d+)',target)
        if not match:raise ValueError('排版 target 只支持正文 pN 或表格 tN；请先读取结构')
        if set(change)-({'target','expected_headers'}|table_keys) or not (set(change)&table_keys):
            raise ValueError('表格排版只支持列宽、重复表头和行内不分页')
        try:table=doc.tables[int(match[1])]
        except IndexError:raise ValueError('Word 目标节点不存在，请先读取结构') from None
        headers=[cell.text for cell in table.rows[0].cells] if table.rows else []
        if change.get('expected_headers')!=headers:raise ValueError('Word 表头不匹配，未保存；请重新读取最新副本')
        if 'column_widths_cm' in change:
            widths=change['column_widths_cm']
            if (not isinstance(widths,list) or len(widths)!=len(table.columns) or
                    any(type(v) not in (int,float) or not math.isfinite(v) or v<0.25 for v in widths)):
                raise ValueError('列宽数量须与表格网格列数相同，每列至少 0.25 厘米')
            # Section properties live at the END of their section in OOXML.
            section_element=None
            for node in table._tbl.itersiblings():
                section_element=node if node.tag==qn('w:sectPr') else node.find('./'+qn('w:pPr')+'/'+qn('w:sectPr'))
                if section_element is not None:break
            from docx.section import Section
            section=Section(section_element,doc.part) if section_element is not None else doc.sections[-1]
            if section._sectPr.xpath('./w:cols[@w:num and @w:num!="1"]'):
                raise ValueError('多栏节的列宽需要专用模板编辑；未保存修改')
            available=(section.page_width-section.left_margin-section.right_margin-(section.gutter or 0))/360000
            if sum(widths)>available+0.01:raise ValueError('列宽总和超过当前节的可用页面宽度')
            table.autofit=False
            for column,width in zip(table.columns,widths):column.width=Cm(width)
            for row in table.rows:
                grid_index=row.grid_cols_before
                for cell in row._tr.tc_lst:
                    span=cell.grid_span
                    if grid_index+span>len(widths):raise ValueError('表格合并网格无效，未保存修改')
                    cell.width=Cm(sum(widths[grid_index:grid_index+span]));grid_index+=span
        for key in ('repeat_header','keep_rows_together'):
            if key in change and type(change[key]) is not bool:raise ValueError(key+' 必须是布尔值')
        if 'repeat_header' in change and table.rows:
            properties=table.rows[0]._tr.get_or_add_trPr()
            current=properties.find(qn('w:tblHeader'))
            if current is not None:properties.remove(current)
            if change['repeat_header']:
                marker=OxmlElement('w:tblHeader');properties.append(marker)
        if 'keep_rows_together' in change:
            for row in table.rows:
                properties=row._tr.get_or_add_trPr();current=properties.find(qn('w:cantSplit'))
                if current is not None:properties.remove(current)
                if change['keep_rows_together']:
                    marker=OxmlElement('w:cantSplit');properties.append(marker)
    stream=BytesIO()
    with zipfile.ZipFile(BytesIO(data)) as old,zipfile.ZipFile(stream,'w') as output:
        for item in old.infolist():
            output.writestr(item,doc.part.blob if item.filename=='word/document.xml' else old.read(item.filename))
    return stream.getvalue()


def inline_runs(tokens):
    """Keep parsed inline semantics; never fetch a link or interpret raw HTML."""
    runs=[];bold=0;italic=0
    def add(value,code=False):
        if value:runs.append(dict(text=value,bold=bold>0,italic=italic>0,code=code))
    for token in tokens or []:
        kind=token.type
        if kind=='strong_open':bold+=1
        elif kind=='strong_close':bold=max(0,bold-1)
        elif kind=='em_open':italic+=1
        elif kind=='em_close':italic=max(0,italic-1)
        elif kind in ('text','html_inline'):add(token.content)
        elif kind=='code_inline':add(token.content,True)
        elif kind in ('softbreak','hardbreak'):add('\n')
        elif kind=='image':
            # Image-generation is not a document-writer capability. Retain its
            # textual label without creating a remote relationship or download.
            add(''.join(r['text'] for r in inline_runs(token.children)) or token.content)
        # link_open/close retain child label runs only. Destinations never become
        # active file references, PDF annotations or Word relationships.
    return runs


def inline_text(runs):
    return ''.join(run['text'] for run in runs)


def chart_spec(content):
    """A deliberately small data-only contract; never evaluates chart code."""
    try:spec=json.loads(content)
    except (TypeError,ValueError):raise ValueError('chart 代码块须为 JSON 对象，不是 Python 或 Markdown') from None
    if not isinstance(spec,dict) or set(spec)-{'type','title','labels','values','unit','max'} or not {'type','labels','values'}<=set(spec):
        raise ValueError('chart 支持 type、title、labels、values、unit、max 字段')
    if spec['type']!='bar':raise ValueError('chart 当前支持 type="bar" 水平条形图')
    labels=spec['labels'];values=spec['values']
    if not isinstance(labels,list) or not 1<=len(labels)<=12 or any(not isinstance(s,str) or not s.strip() or len(s)>40 or '\n' in s for s in labels):
        raise ValueError('chart 须有 1 至 12 个标签，每个不超过40字符且不能换行')
    if not isinstance(values,list) or len(values)!=len(labels) or any(type(v) not in (float,int) or not math.isfinite(v) or not 0<=v<=1e12 for v in values):
        raise ValueError('chart values 须与 labels 等长，数值有限且为非负数')
    for key,limit in (('title',60),('unit',8)):
        if not isinstance(spec.get(key,''),str) or len(spec.get(key,''))>limit or '\n' in spec.get(key,''):
            raise ValueError('chart 标题或单位过长或无效')
    maximum=spec.get('max',max(values) or 1)
    if type(maximum) not in (float,int) or not math.isfinite(maximum) or not max(values)<=maximum<=1e12 or maximum<=0:
        raise ValueError('chart max 须为正数且不小于所有 values')
    return dict(spec,title=spec.get('title',''),unit=spec.get('unit',''),max=maximum)


def chart_png(spec):
    """Render native ReportLab chart geometry; embed a fixed image, not text bars."""
    import base64
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    from reportlab.graphics.charts.barcharts import HorizontalBarChart
    from reportlab.graphics.shapes import Drawing,String
    from reportlab.graphics import renderPDF
    from reportlab.lib.colors import HexColor
    from .document_render import render_page
    font='TaskManagerChartNotoSC'
    if font not in pdfmetrics.getRegisteredFontNames():
        pdfmetrics.registerFont(TTFont(font,str(Path(__file__).parent/'assets/NotoSansSC.ttf')))
    labels=spec['labels'];values=spec['values'];height=58+24*len(labels)
    # Long labels get a wider plot container rather than tiny unreadable glyphs.
    label_width=max(pdfmetrics.stringWidth(label,font,10) for label in labels)+12
    right_pad=max(54,max(pdfmetrics.stringWidth(f'{v:g}{spec["unit"]}',font,9) for v in values)+12)
    width=max(468,label_width+270+right_pad);chart=HorizontalBarChart()
    chart.x=label_width;chart.y=26;chart.width=width-label_width-right_pad;chart.height=24*len(labels)
    chart.data=[list(reversed(values))];chart.categoryAxis.categoryNames=list(reversed(labels))
    chart.categoryAxis.labels.fontName=font;chart.categoryAxis.labels.fontSize=10
    chart.valueAxis.valueMin=0;chart.valueAxis.valueMax=spec['max'];chart.valueAxis.valueStep=spec['max']/4
    chart.valueAxis.labels.fontName=font;chart.valueAxis.labels.fontSize=8
    chart.valueAxis.labelTextFormat=lambda v:f'{v:g}'
    chart.bars[0].fillColor=HexColor('#335E83');chart.bars[0].strokeColor=None
    chart.barWidth=12;chart.barLabelFormat=lambda v:f'{v:g}{spec["unit"]}'
    chart.barLabels.fontName=font;chart.barLabels.fontSize=9;chart.barLabels.boxAnchor='w';chart.barLabels.dx=4
    drawing=Drawing(width,height);drawing.add(chart)
    if spec['title']:drawing.add(String(0,height-14,spec['title'],fontName=font,fontSize=12))
    preview=render_page(renderPDF.drawToString(drawing))
    return base64.b64decode(preview['dataUrl'].split(',',1)[1])


def table_widths(rows,total):
    """Share available space by readable text demand, with a minimum per column."""
    import unicodedata
    count=max(map(len,rows));wanted=[]
    for ci in range(count):
        lengths=[]
        for row in rows:
            content=inline_text(row[ci]) if ci<len(row) else ''
            lengths.append(max((sum(1 if unicodedata.east_asian_width(c) in ('W','F') else .52 for c in line) for line in content.split('\n')),default=0))
        wanted.append(min(50,max(4,max(lengths)))*9+14)
    floor=min(42,total/count*.7);remaining=total-floor*count
    weights=[max(1,v-floor) for v in wanted]
    return [floor+remaining*w/sum(weights) for w in weights]


def markdown_blocks(content):
    from markdown_it import MarkdownIt
    tokens=MarkdownIt('commonmark',{'html':False}).enable('table').parse(content)
    blocks=[];i=0
    while i<len(tokens):
        t=tokens[i]
        if t.type=='table_open':
            rows=[];row=[];i+=1
            while i<len(tokens) and tokens[i].type!='table_close':
                if tokens[i].type=='tr_open':row=[]
                elif tokens[i].type=='inline':row.append(inline_runs(tokens[i].children))
                elif tokens[i].type=='tr_close':rows.append(row)
                i+=1
            if rows:blocks.append(('table',rows))
        elif t.type=='heading_open' and i+1<len(tokens):
            blocks.append(('heading'+t.tag[1:],inline_runs(tokens[i+1].children)));i+=1
        elif t.type=='inline':blocks.append(('paragraph',inline_runs(t.children)))
        elif t.type=='fence' and t.info.strip()=='chart':
            blocks.append(('chart',chart_spec(t.content)))
        elif t.type in ('fence','code_block'):
            blocks.append(('code',[dict(text=t.content,bold=False,italic=False,code=True)]))
        i+=1
    return blocks


def rich_inline(blocks):
    for kind,value in blocks:
        if kind=='chart':return True
        groups=[cell for row in value for cell in row] if kind=='table' else [value]
        if any(run['bold'] or run['italic'] or run['code'] for group in groups for run in group):return True
    return False


def _write_inline(paragraph,runs,header=False):
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn
    from docx.shared import Pt
    for item in runs:
        run=paragraph.add_run(item['text'])
        if item['bold'] or header:run.bold=True
        if item['italic']:run.italic=True
        if item['code']:
            run.font.name='Courier New';run.font.size=Pt(10)
            run._r.get_or_add_rPr().rFonts.set(qn('w:eastAsia'),'Noto Sans SC')
            shade=OxmlElement('w:shd');shade.set(qn('w:fill'),'F1F1F1');run._r.get_or_add_rPr().append(shade)


def format_docx(template,content):
    from docx import Document
    from docx.oxml import OxmlElement
    from docx.shared import Pt,Emu
    from docx.oxml.ns import qn
    doc=Document(BytesIO(template))
    section=doc.sections[0]
    usable=section.page_width-section.left_margin-section.right_margin
    # The supplied template embeds a subset Chinese font and contains a title.
    for child in list(doc.element.body):
        if child.tag!=f'{{{W}}}sectPr' and child is not doc.paragraphs[0]._p:
            doc.element.body.remove(child)
    for kind,value in markdown_blocks(content):
        if kind=='chart':
            paragraph=doc.add_paragraph()
            paragraph.paragraph_format.keep_together=True
            paragraph.add_run().add_picture(BytesIO(chart_png(value)),width=Emu(usable))
            # A visible compact data caption keeps exact values searchable and
            # accessible even when the embedded graphic is read as an image.
            caption=doc.add_paragraph('; '.join(f'{label}: {number:g}{value["unit"]}' for label,number in zip(value['labels'],value['values'])))
            paragraph.paragraph_format.keep_with_next=True
            caption.paragraph_format.space_after=Pt(8)
            for run in caption.runs:run.font.size=Pt(9)
        elif kind=='table':
            count=max(len(row) for row in value)
            if count>12 or len(value)>500:raise ValueError('文档表格过大，请拆分')
            table=doc.add_table(rows=1,cols=count)
            widths=table_widths(value,usable/12700)
            table.autofit=False
            for column,width in zip(table.columns,widths):column.width=Pt(width)
            borders=OxmlElement('w:tblBorders')
            for side in ('top','left','bottom','right','insideH','insideV'):
                border=OxmlElement('w:'+side);border.set(qn('w:val'),'single');border.set(qn('w:sz'),'4');border.set(qn('w:color'),'CCCCCC');borders.append(border)
            table._tbl.tblPr.append(borders)
            for ri,row in enumerate(value):
                current=table.rows[0] if ri==0 else table.add_row()
                current._tr.get_or_add_trPr().append(OxmlElement('w:cantSplit'))
                cells=current.cells
                for ci,cell in enumerate(cells):
                    cell.width=Pt(widths[ci]);p=cell.paragraphs[0]
                    p.paragraph_format.space_after=Pt(4);p.paragraph_format.space_before=Pt(4)
                    p.paragraph_format.line_spacing=1.12
                for ci,cell in enumerate(row):
                    _write_inline(cells[ci].paragraphs[0],cell,header=ri==0)
            repeat=OxmlElement('w:tblHeader');table.rows[0]._tr.get_or_add_trPr().append(repeat)
        else:
            style='Heading'+str(min(3,int(kind[-1]))) if kind.startswith('heading') else 'Normal'
            _write_inline(doc.add_paragraph(style=style),value)
    out=BytesIO();doc.save(out);return _refresh_embedded_font(out.getvalue())
