"""Safe workbook structure and a separate strict literal-table adapter.

Merged cells, formulas and multiple worksheets are document structure, not
executable capabilities. No formulas, links or macros are ever evaluated.
"""
from io import BytesIO
from datetime import date, datetime
from decimal import Decimal
from pathlib import PurePosixPath
import re
import zipfile
import json
import math
from xml.etree import ElementTree as ET

from .csv_documents import encode_csv

MAX_ROWS=100000
MAX_COLS=100
MAX_CELLS=1000000


def _xlsx_archive(data, *, literal_table=False):
    if not isinstance(data,bytes) or len(data)>10*1024*1024:
        raise ValueError('XLSX 文件不能超过 10 MB')
    try:
        with zipfile.ZipFile(BytesIO(data)) as z:
            items=z.infolist()
            if len(items)>1500 or sum(i.file_size for i in items)>64*1024*1024:
                raise ValueError('XLSX 解压内容超过限制')
            if len({i.filename for i in items})!=len(items):raise ValueError('XLSX 存在重复内部文件')
            footprint=0;sheet_count=0
            for i in items:
                path=PurePosixPath(i.filename)
                if path.is_absolute() or '..' in path.parts or '\\' in i.filename or i.flag_bits&1:
                    raise ValueError('XLSX 内部路径或加密内容不受支持')
                lower=i.filename.lower()
                if any(s in lower for s in ('vbaproject','externallinks/','embeddings/','activex/')):
                    raise ValueError('XLSX 含宏、外部链接或嵌入对象，不支持读取')
                if lower.endswith(('.xml','.rels')):
                    raw=z.read(i)
                    if re.search(br'<!\s*(DOCTYPE|ENTITY)\b',raw,re.I):raise ValueError('XLSX XML 实体不受支持')
                    if re.search(br'TargetMode\s*=\s*[\'"]External[\'"]',raw,re.I):raise ValueError('XLSX 外部关系不受支持')
                    if literal_table and re.search(br'<(?:\w+:)?mergeCell\b',raw):raise ValueError('此表格操作不接受合并单元格；请使用工作簿结构读取或隔离脚本处理原件')
                    if lower.startswith('xl/worksheets/') and lower.endswith('.xml'):
                        sheet_count+=1
                        if sheet_count>100:raise ValueError('XLSX 工作表数量超过 100')
                        # Check dimensions *before* openpyxl expands merged cells.
                        # A tiny ZIP can otherwise request billions of cells.
                        from openpyxl.utils.cell import range_boundaries
                        root=ET.fromstring(raw);max_row=max_col=1
                        merge_count=0;current_row=0;current_col=0
                        for node in root.iter():
                            tag=node.tag.rsplit('}',1)[-1]
                            if tag=='row':
                                try:current_row=int(node.get('r',current_row+1))
                                except (ValueError,TypeError):raise ValueError('XLSX 行坐标无效') from None
                                if not 1<=current_row<=MAX_ROWS+1:raise ValueError('XLSX 行坐标超过限制')
                                current_col=0;max_row=max(max_row,current_row)
                            if tag=='mergeCell':
                                merge_count+=1
                                if merge_count>10000:raise ValueError('XLSX 合并范围数量超过限制')
                            reference=node.get('ref') if tag in ('dimension','mergeCell') else node.get('r') if tag=='c' else None
                            if tag=='c' and not reference:
                                current_col+=1
                                if current_col>MAX_COLS:raise ValueError('XLSX 列坐标超过限制')
                                max_col=max(max_col,current_col)
                            if reference:
                                try:a,b,c,d=range_boundaries(reference)
                                except (ValueError,TypeError):raise ValueError('XLSX 单元格范围无效') from None
                                if None in (a,b,c,d) or min(a,b)<1 or c<a or d<b or c>MAX_COLS or d>MAX_ROWS+1:
                                    raise ValueError('XLSX 工作表或合并范围超过行列限制')
                                max_row=max(max_row,d);max_col=max(max_col,c)
                                if tag=='c':current_col=c
                        footprint+=max_row*max_col
                        if footprint>MAX_CELLS:raise ValueError('XLSX 工作簿单元格总范围超过限制')
            if 'xl/workbook.xml' not in z.namelist():raise ValueError('不是有效的 XLSX 工作簿')
            if b'macroEnabled' in z.read('[Content_Types].xml'):raise ValueError('不支持启用宏的工作簿')
    except (zipfile.BadZipFile,KeyError,RuntimeError,ET.ParseError):raise ValueError('XLSX 文件损坏或不可读取') from None


def _cell(cell):
    if cell.data_type=='f':raise ValueError('XLSX 含公式，必须先由用户计算并另存为纯值；不能假定缓存结果最新')
    if cell.data_type=='e':raise ValueError('XLSX 含错误单元格，请先修正')
    value=cell.value
    if value is None:return ''
    if isinstance(value,bool):return 'TRUE' if value else 'FALSE'
    if isinstance(value,(datetime,date)):return value.isoformat()
    if isinstance(value,(int,float,Decimal)):
        number=Decimal(str(value))
        if not number.is_finite():raise ValueError('XLSX 含非有限数字')
        fmt=cell.number_format or ''
        if re.fullmatch(r'0{2,30}',fmt) and number==number.to_integral_value() and number>=0:
            return str(int(number)).zfill(len(fmt))
        return format(number,'f')
    value=str(value)
    if len(value)>20000:raise ValueError('XLSX 单元格内容过长')
    return value


def read_table(data,extension,sheet_name=None):
    """Return all bounded table rows as strings; callers must not use an index slice."""
    if extension=='csv':
        # Reuse the exact CSV reader used by transformations, without truncation.
        from .csv_documents import _read
        frame,meta=_read(data)
        return dict(columns=list(frame.columns),rows=frame.values.tolist(),warnings=meta['warnings'])
    if extension!='xlsx':raise ValueError('已学习的表格规则支持 CSV 或 XLSX')
    _xlsx_archive(data,literal_table=True)
    from openpyxl import load_workbook
    try:book=load_workbook(BytesIO(data),read_only=True,data_only=False,keep_links=False)
    except Exception:raise ValueError('XLSX 无法打开') from None
    try:
        tables={};count=0
        for sheet in book.worksheets:
            if sheet.max_row and sheet.max_row>MAX_ROWS+1 or sheet.max_column and sheet.max_column>MAX_COLS:
                raise ValueError('XLSX 表格行列超过限制')
            rows=[]
            for row in sheet.iter_rows():
                count+=len(row)
                if count>MAX_CELLS:raise ValueError('XLSX 单元格总数超过限制')
                rows.append([_cell(c) for c in row])
            while rows and not any(rows[-1]):rows.pop()
            if rows:
                while rows[0] and not any(r[-1] for r in rows):
                    for r in rows:r.pop()
                tables[sheet.title]=rows
        if sheet_name is None:
            if len(tables)!=1:raise ValueError('XLSX 有多个非空工作表或没有数据，请明确指定工作表')
            sheet_name=next(iter(tables))
        if sheet_name not in tables:raise ValueError('指定的 XLSX 工作表不存在或没有数据')
        rows=tables[sheet_name];columns=rows[0]
        if not columns or any(not c.strip() for c in columns) or len(set(columns))!=len(columns):
            raise ValueError('XLSX 首行必须是非空且不重复的列名；请明确表格布局')
        return dict(columns=columns,rows=rows[1:],sheetName=sheet_name,sheetNames=list(tables),warnings=[])
    finally:book.close()


def _open_workbook(data, *, data_only=False, checked=False):
    if not checked:_xlsx_archive(data)
    from openpyxl import load_workbook
    try:
        return load_workbook(BytesIO(data),read_only=False,data_only=data_only,keep_links=False)
    except Exception:raise ValueError('XLSX 文件损坏或工作簿结构无法读取') from None


def _value(value):
    if isinstance(value,float) and not math.isfinite(value):raise ValueError('XLSX 含非有限数值')
    if isinstance(value,(datetime,date)):return value.isoformat()
    if isinstance(value,Decimal):return format(value,'f')
    if value is None or isinstance(value,(str,int,float,bool)):return value
    # openpyxl array formulas expose their text/ref as objects, not strings.
    if hasattr(value,'text'):return value.text
    if hasattr(value,'__iter__') and type(value).__module__=='openpyxl.worksheet.formula':return dict(value)
    return str(value)


def _sheet_info(sheet):
    merges=sorted(str(v) for v in sheet.merged_cells.ranges)
    return dict(name=sheet.title,rowCount=sheet.max_row,columnCount=sheet.max_column,
                state=sheet.sheet_state,mergedRanges=merges[:100],mergedRangeCount=len(merges),mergedRangesTruncated=len(merges)>100,
                freezePanes=str(sheet.freeze_panes) if sheet.freeze_panes else None,
                defaultRowHeight=sheet.sheet_format.defaultRowHeight,defaultColumnWidth=sheet.sheet_format.defaultColWidth,
                columns={k:dict(width=v.width,min=v.min,max=v.max,hidden=bool(v.hidden)) for k,v in sheet.column_dimensions.items()},
                columnWidths={k:v.width for k,v in sheet.column_dimensions.items()})


def _record(cell,cache=None):
    value=_value(cell.value)
    result=dict(coordinate=cell.coordinate,row=cell.row,column=cell.column,
                value=value,dataType=cell.data_type,numberFormat=cell.number_format)
    if cell.data_type=='f':
        cached=_value(cache.value) if cache is not None else None
        result.update(formula=value,cachedValue=cached,cacheStatus='missing' if cached is None else 'unverified')
        if hasattr(cell.value,'ref'):result['formulaRange']=cell.value.ref
    return result


def inspect_xlsx(data,offset=0,limit=30,sheet_name=None,cell_range=None):
    """Read a bounded physical worksheet range without flattening its layout.

    Explicit sheet/range requests paginate physical rows (including headings).
    For an old simple-table request, legacy columns/rows/offset semantics remain;
    richer coordinate records and the complete sheet catalog are always present.
    """
    if type(offset)is not int or offset<0 or type(limit)is not int or not 1<=limit<=100:raise ValueError('表格分页无效')
    from openpyxl.utils.cell import range_boundaries,get_column_letter
    book=_open_workbook(data);cache=None
    try:
        if sheet_name is not None and sheet_name not in book.sheetnames:raise ValueError('指定的 XLSX 工作表不存在')
        sheet=book[sheet_name or book.sheetnames[0]]
        a,b,c,d=1,1,sheet.max_column,sheet.max_row
        if cell_range is not None:
            if not isinstance(cell_range,str) or not re.fullmatch(r'\$?[A-Z]{1,3}\$?[1-9][0-9]*(?::\$?[A-Z]{1,3}\$?[1-9][0-9]*)?',cell_range):raise ValueError('请用 A1:D20 格式指定单个工作表范围')
            a,b,c,d=range_boundaries(cell_range)
            if c<a or d<b or c>MAX_COLS or d>MAX_ROWS+1:raise ValueError('工作表读取范围超过限制')
        table=None
        if sheet_name is None and cell_range is None:
            try:table=read_table(data,'xlsx')
            except ValueError:pass
        if table:
            # Legacy literal table offsets exclude its first-row header.
            b=2;d=len(table['rows'])+1;c=len(table['columns'])
        total=max(0,d-b+1);start=b+min(offset,total);stop=min(d+1,start+limit)
        records=[];rows=[];used=0;next_offset=None;truncated=False;styles={}
        merged_anchors={}
        for merged in sheet.merged_cells.ranges:
            for row_index in range(max(start,merged.min_row),min(stop-1,merged.max_row)+1):
                for col_index in range(max(a,merged.min_col),min(c,merged.max_col)+1):
                    merged_anchors[(row_index,col_index)]=merged.start_cell.coordinate
        for row in sheet.iter_rows(min_row=start,max_row=stop-1,min_col=a,max_col=c) if start<stop else []:
            if any(v.data_type=='f' for v in row) and cache is None:cache=_open_workbook(data,data_only=True,checked=True)
            entries=[];row_styles={}
            for cell in row:
                item=_record(cell,cache[sheet.title][cell.coordinate] if cache is not None else None)
                style_id=str(cell.style_id);item['styleId']=style_id
                if style_id not in styles:row_styles[style_id]=_style(cell)
                for key in ('value','formula','cachedValue'):
                    if isinstance(item.get(key),str) and len(item[key])>200:
                        item[key]=item[key][:200];item[key+'Truncated']=True;truncated=True
                anchor=merged_anchors.get((cell.row,cell.column))
                if anchor:item['mergedAnchor']=anchor
                entries.append(item)
            size=len(json.dumps([entries,row_styles],ensure_ascii=False))
            if rows and used+size>48000:next_offset=offset+len(rows);break
            used+=size;records.extend(entries);styles.update(row_styles);rows.append([v['value'] for v in entries])
        end=offset+len(rows)
        if next_offset is None and end<total:next_offset=end
        warnings=['公式只读取，不会计算；缓存值即使存在也未核实是否最新。']
        if truncated:warnings.append('长单元格预览已截断；原件保持完整，脚本可读取原字节。')
        merges=sorted(str(v) for v in sheet.merged_cells.ranges)
        result=dict(layout='table' if table else 'worksheet',sheetName=sheet.title,
            sheetNames=list(book.sheetnames),sheets=[_sheet_info(s) for s in book.worksheets],
            mergedRanges=merges[:200],mergedRangeCount=len(merges),mergedRangesTruncated=len(merges)>200,cells=records,styles=styles,
            rowHeights={str(k):v.height for k,v in sheet.row_dimensions.items() if start<=k<start+len(rows) and v.height is not None},
            columns=table['columns'] if table else [get_column_letter(i) for i in range(a,c+1)],
            rows=table['rows'][offset:end] if table else rows,rowCount=total,columnCount=c-a+1,
            offset=offset,nextOffset=next_offset,requestedRange=cell_range,
            offsetMeaning='data rows after header' if table else 'physical rows within requested range',
            returnedRange=f'{get_column_letter(a)}{start}:{get_column_letter(c)}{start+len(rows)-1}' if rows else None,
            formulasEvaluated=False,cellValuesTruncated=truncated,warnings=warnings)
        return result
    finally:
        book.close()
        if cache is not None:cache.close()


def xlsx_text(data):
    value,warnings,_=xlsx_index(data)
    return value,warnings


def xlsx_index(data):
    """Validate a workbook and build an optional coordinate-based search index.

    The index is not a table contract and its truncation never alters raw bytes.
    Container/security/structural errors remain hard failures.
    """
    book=_open_workbook(data)
    try:
        parts=[];size=0;truncated=False;formula_count=0;error_count=0
        for sheet in book.worksheets:
            heading=f'【工作表 {sheet.title}】\n';parts.append(heading);size+=len(heading)
            for row in sheet.iter_rows():
                for cell in row:
                    if cell.value is None:continue
                    if cell.data_type=='f':formula_count+=1
                    if cell.data_type=='e':error_count+=1
                    line=f'{cell.coordinate}: {_value(cell.value)}\n'
                    if size+len(line)>100000:truncated=True;continue
                    parts.append(line);size+=len(line)
        warnings=[]
        if formula_count:warnings.append('含公式；索引保存公式文本，未计算，也未把缓存值当作已核实结果。')
        if error_count:warnings.append('含 Excel 错误单元格；已原样保存，处理时应核对对应坐标。')
        if truncated:warnings.append('搜索索引已截断；原始 XLSX 保持完整，请按工作表/范围读取或用隔离脚本分析。')
        return ''.join(parts),warnings,dict(parseStatus='parsed',indexTruncated=truncated,
            sheetCount=len(book.worksheets),formulaCount=formula_count,errorCellCount=error_count,
            formulasEvaluated=False)
    finally:book.close()


def _style(cell):
    """Visual semantics, independent of style IDs and package timestamps."""
    def colour(value):
        if value is None:return None
        return dict(type=value.type,value=value.value,tint=value.tint)
    def side(value):return None if value is None else dict(style=value.style,color=colour(value.color))
    return dict(numberFormat=cell.number_format,
        font=dict(name=cell.font.name,size=cell.font.sz,bold=bool(cell.font.b),italic=bool(cell.font.i),
                  underline=cell.font.u,strike=bool(cell.font.strike),color=colour(cell.font.color)),
        fill=dict(type=cell.fill.fill_type,fg=colour(cell.fill.fgColor),bg=colour(cell.fill.bgColor)),
        alignment=dict(horizontal=cell.alignment.horizontal,vertical=cell.alignment.vertical,
                       wrap=bool(cell.alignment.wrap_text),rotation=cell.alignment.text_rotation,
                       shrink=bool(cell.alignment.shrink_to_fit),indent=cell.alignment.indent),
        border={k:side(getattr(cell.border,k)) for k in ('left','right','top','bottom')})


def workbook_signature(data):
    """Canonical data/template structure; ignores creator/time/ZIP/style IDs.

    Keeps blank styled cells, sheets, types, formulas and template formatting.
    Cached formula values are excluded: they are not verified recalculations.
    """
    book=_open_workbook(data)
    try:
        sheets=[]
        for sheet in book.worksheets:
            cells={}
            for row in sheet.iter_rows():
                for cell in row:
                    if cell.value is not None or cell.has_style:
                        item=dict(value=_value(cell.value),dataType=cell.data_type,style=_style(cell))
                        if hasattr(cell.value,'ref'):item['formulaRange']=cell.value.ref
                        cells[cell.coordinate]=item
            sheets.append(dict(name=sheet.title,state=sheet.sheet_state,cells=cells,
                mergedRanges=sorted(str(v) for v in sheet.merged_cells.ranges),
                freezePanes=str(sheet.freeze_panes) if sheet.freeze_panes else None,
                columns={k:dict(width=v.width,hidden=bool(v.hidden),min=v.min,max=v.max) for k,v in sheet.column_dimensions.items()},
                rows={str(k):dict(height=v.height,hidden=bool(v.hidden)) for k,v in sheet.row_dimensions.items() if v.height is not None or v.hidden},
                defaultRowHeight=sheet.sheet_format.defaultRowHeight,defaultColumnWidth=sheet.sheet_format.defaultColWidth,
                autoFilter=sheet.auto_filter.ref,showGridLines=sheet.sheet_view.showGridLines,
                tables={name:dict(ref=sheet.tables[name].ref,style=sheet.tables[name].tableStyleInfo.name if sheet.tables[name].tableStyleInfo else None) for name in sheet.tables}))
        return sheets
    finally:book.close()


def workbook_difference(actual,expected):
    left=workbook_signature(actual);right=workbook_signature(expected)
    if [s['name'] for s in left]!=[s['name'] for s in right]:return '工作表名称或顺序不同'
    for a,b in zip(left,right):
        for coordinate in sorted(set(a['cells'])|set(b['cells'])):
            if a['cells'].get(coordinate)!=b['cells'].get(coordinate):
                av=a['cells'].get(coordinate);bv=b['cells'].get(coordinate)
                if av is None or bv is None:return f"工作表 {a['name']} 单元格 {coordinate} 内容或格式单元格缺失"
                for key in ('value','dataType','formulaRange'):
                    if av.get(key)!=bv.get(key):return f"工作表 {a['name']} 单元格 {coordinate} 的 {key} 不同：实际 {str(av.get(key))[:160]}；样例 {str(bv.get(key))[:160]}"
                for key in av['style']:
                    if av['style'][key]!=bv['style'][key]:return f"工作表 {a['name']} 单元格 {coordinate} 的 {key} 格式不同：实际 {str(av['style'][key])[:200]}；样例 {str(bv['style'][key])[:200]}"
        for key in a:
            if key!='cells' and a[key]!=b[key]:return f"工作表 {a['name']} 的 {key} 不同"
    return ''


def encode_xlsx(columns,rows,sheet_name='Result'):
    # CSV validation enforces headers/cell/count limits, but XLSX cells remain
    # literal strings so identifiers and formula-looking text retain their bytes.
    encode_csv(columns,rows)
    if not isinstance(sheet_name,str) or not sheet_name or len(sheet_name)>31 or re.search(r'[\[\]:*?/\\]',sheet_name):
        raise ValueError('XLSX 工作表名无效')
    from openpyxl import Workbook
    from openpyxl.styles import Alignment,Font,PatternFill
    from openpyxl.utils import get_column_letter
    book=Workbook();sheet=book.active;sheet.title=sheet_name
    for index,row in enumerate([columns]+rows,1):
        for col,value in enumerate(row,1):
            cell=sheet.cell(index,col,'' if value is None else str(value))
            cell.data_type='s';cell.number_format='@'
            cell.font=Font(name='Arial',size=11,color='253342')
            cell.alignment=Alignment(vertical='center')
            if index==1:
                cell.font=Font(name='Arial',size=11,bold=True,color='FFFFFF')
                cell.fill=PatternFill('solid',fgColor='27384B')
                cell.alignment=Alignment(vertical='center',horizontal='center',wrap_text=True)
        sheet.row_dimensions[index].height=30 if index==1 else 22
    for index,column in enumerate(columns,1):
        sample=[str(column)]+[str(row[index-1]) for row in rows[:200]]
        # Wider CJK glyphs count twice; exceptionally long prose wraps instead of
        # producing an unusably wide sheet. Raw cell values remain unchanged.
        width=min(42,max(12,max(sum(2 if ord(c)>255 else 1 for c in value) for value in sample)+3))
        sheet.column_dimensions[get_column_letter(index)].width=width
        for row_index in range(2,len(rows)+2):
            cell=sheet.cell(row_index,index)
            if len(str(cell.value or ''))>width-3:cell.alignment=Alignment(vertical='top',wrap_text=True)
            value=str(cell.value or '')
            if re.fullmatch(r'0[0-9]+',value) and len(value)<=30:cell.number_format='0'*len(value)
    sheet.sheet_view.showGridLines=False
    sheet.freeze_panes='A2'
    output=BytesIO();book.save(output);book.close()
    # XLSX containers otherwise contain wall-clock timestamps, defeating exact
    # preview hashes and recovery after a process stops between two commits.
    stable=BytesIO()
    with zipfile.ZipFile(BytesIO(output.getvalue())) as source,zipfile.ZipFile(stable,'w',zipfile.ZIP_DEFLATED) as target:
        for name in sorted(source.namelist()):
            value=source.read(name)
            if name=='docProps/core.xml':
                value=re.sub(br'(<dcterms:(?:created|modified)[^>]*>)[^<]+',br'\g<1>2000-01-01T00:00:00Z',value)
            entry=zipfile.ZipInfo(name,(2000,1,1,0,0,0));entry.compress_type=zipfile.ZIP_DEFLATED
            target.writestr(entry,value)
    data=stable.getvalue();read_table(data,'xlsx',sheet_name)
    return data
