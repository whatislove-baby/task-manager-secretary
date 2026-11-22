"""Selected, immutable document copies and generated artifacts. No arbitrary tool execution."""
from io import BytesIO
from pathlib import Path
from uuid import uuid4
from xml.etree import ElementTree as ET
from xml.sax.saxutils import escape
import hashlib,json,os,re,sqlite3,stat,zipfile
from .archive import text
from .tree import identifier,instant

MAX_BYTES=10*1024*1024
MAX_TEXT=100000
MAX_FILES=500
TYPES={'pdf':'application/pdf','docx':'application/vnd.openxmlformats-officedocument.wordprocessingml.document','txt':'text/plain','md':'text/markdown','csv':'text/csv',
       'png':'image/png','jpg':'image/jpeg','jpeg':'image/jpeg','webp':'image/webp','zip':'application/zip',
       'xlsx':'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'}
W='http://schemas.openxmlformats.org/wordprocessingml/2006/main'


def safe_name(value):
    if not isinstance(value,str):raise ValueError('文件名无效')
    name=re.sub(r'[\x00-\x1f\x7f<>:"/\\|?*]','_',value).strip().strip('.')[:160]
    if not name:name='document'
    return name


def checked_text(value):
    if len(value)>MAX_TEXT:raise ValueError('文件文字超过 100000 字符，请拆分后导入；没有截断原文')
    if not value.strip():raise ValueError('文件没有可读取的文字；扫描 PDF 需要先进行 OCR')
    if re.search(r'[\x00-\x08\x0b\x0c\x0e-\x1f]',value):raise ValueError('文件含有不支持的控制字符')
    return value


def extract_document(data,extension):
    if len(data)>MAX_BYTES:raise ValueError('单个文件不能超过 10 MB')
    warnings=[]
    try:
        if extension in ('txt','md'):
            value=data.decode('utf-8-sig')
        elif extension=='csv':
            from .csv_documents import csv_text
            return csv_text(data)
        elif extension=='xlsx':
            from .spreadsheet_files import xlsx_text
            return xlsx_text(data)
        elif extension in ('png','jpg','jpeg','webp'):
            from .bundle_files import inspect_image
            inspect_image(data,extension)
            return '', ['图片已验证并保存；未执行 OCR，内容需使用视觉工具查看。']
        elif extension=='zip':
            from .bundle_files import archive_index
            index=archive_index(data)
            return '压缩包目录（不含成员正文）\n'+'\n'.join(item['path'] for item in index['members']), [index['warning']]
        elif extension=='pdf':
            from pypdf import PdfReader
            from pypdf import Configuration
            Configuration.zlib_maximum_output_length=10*1024*1024
            if not data.startswith(b'%PDF-'):raise ValueError('文件内容不是有效 PDF')
            reader=PdfReader(BytesIO(data),strict=True)
            if reader.is_encrypted:raise ValueError('暂不支持加密 PDF，请先另存为未加密副本')
            if not reader.pages:raise ValueError('PDF 没有页面')
            if len(reader.pages)>80:raise ValueError('PDF 超过 80 页，请拆分后导入')
            parts=[];size=0;empty=[]
            for index,page in enumerate(reader.pages,1):
                content=page.extract_text() or '';size+=len(content)
                if size>MAX_TEXT:raise ValueError('文件文字超过 100000 字符，请拆分后导入；没有截断原文')
                if not content.strip():empty.append(index)
                else:parts.append(f'【第 {index} 页】\n'+content)
            value='\n\n'.join(parts)
            if empty:warnings.append('以下 PDF 页没有可提取文字，请使用页面视觉阅读：'+','.join(map(str,empty)))
            warnings.append('文本索引不包含图片、手写内容和复杂表格布局；请按页查看原图后判断。')
        elif extension=='docx':
            from .document_formats import check_docx
            check_docx(data)
            with zipfile.ZipFile(BytesIO(data)) as archive:
                items=archive.infolist()
                if len(items)>1000 or sum(v.file_size for v in items)>20*1024*1024 or any(v.file_size>(12*1024*1024 if v.filename.startswith('word/fonts/') else 8*1024*1024) for v in items):raise ValueError('Word 文档解压体积或文件数超过上限')
                if len({v.filename for v in items})!=len(items):raise ValueError('Word 文档包含重复 ZIP 路径')
                if any('vbaproject' in v.filename.lower() for v in items):raise ValueError('暂不支持含宏的 Word 文档')
                content=archive.read('word/document.xml')
                if b'<!DOCTYPE' in content.upper() or b'<!ENTITY' in content.upper():raise ValueError('Word 文档 XML 含不支持的实体定义')
                root=ET.fromstring(content);parts=[]
                for p in root.iter('{'+W+'}p'):
                    line=[]
                    for e in p.iter():
                        if e.tag=='{'+W+'}t':line.append(e.text or '')
                        elif e.tag=='{'+W+'}tab':line.append('\t')
                        elif e.tag in ('{'+W+'}br','{'+W+'}cr'):line.append('\n')
                    if line:parts.append(''.join(line))
                value='\n'.join(parts)
                warnings.append('文本索引包含正文及表格文字；结构工具提供段落和单元格定位，图片及版式请查看页面。批注和修订语义未完整解析。')
        else:raise ValueError('目前支持 PDF、DOCX、CSV、UTF-8 TXT 和 Markdown 文件')
    except UnicodeError:raise ValueError('文本不是 UTF-8 编码，请另存为 UTF-8 后导入') from None
    except ValueError:raise
    except Exception:raise ValueError('文件损坏、格式不受支持或无法完整读取') from None
    if extension in ('pdf','docx') and not value.strip():
        return '',warnings+['原文件已保存；没有可搜索文字，需要页面视觉阅读。']
    return checked_text(value),warnings


def _plain_docx(title,content):
    paragraphs=[]
    for index,line in enumerate([title]+content.splitlines()):
        style='Title' if index==0 else 'Normal'
        match=re.match(r'^(#{1,3})\s+(.+)$',line)
        if index and match:style='Heading'+str(len(match[1]));line=match[2]
        # Preserve explicit line boundaries; no fields, relationships, macros or remote objects.
        paragraphs.append('<w:p><w:pPr><w:pStyle w:val="'+style+'"/></w:pPr><w:r><w:t xml:space="preserve">'+escape(line)+'</w:t></w:r></w:p>')
    document='<?xml version="1.0" encoding="UTF-8" standalone="yes"?><w:document xmlns:w="'+W+'"><w:body>'+''.join(paragraphs)+'<w:sectPr><w:pgSz w:w="12240" w:h="15840"/><w:pgMar w:top="1440" w:right="1440" w:bottom="1440" w:left="1440"/></w:sectPr></w:body></w:document>'
    styles='<w:styles xmlns:w="'+W+'"><w:docDefaults><w:rPrDefault><w:rPr><w:rFonts w:ascii="Arial" w:hAnsi="Arial" w:eastAsia="Noto Sans SC"/><w:color w:val="000000"/><w:sz w:val="22"/></w:rPr></w:rPrDefault><w:pPrDefault><w:pPr><w:spacing w:after="160" w:line="300" w:lineRule="auto"/></w:pPr></w:pPrDefault></w:docDefaults>'
    for name,size in [('Normal',22),('Title',36),('Heading1',30),('Heading2',26),('Heading3',24)]:
        styles+='<w:style w:type="paragraph"'+(' w:default="1"' if name=='Normal' else '')+' w:styleId="'+name+'"><w:name w:val="'+name+'"/><w:pPr>'+('<w:keepNext/>'+('<w:spacing w:before="200"/>' if name.startswith('Heading') else '') if name!='Normal' else '')+'</w:pPr><w:rPr><w:color w:val="000000"/><w:sz w:val="'+str(size)+'"/>'+('<w:b/>' if name!='Normal' else '')+'</w:rPr></w:style>'
    styles+='</w:styles>'
    # Embed only the glyphs used by this document. A full CJK font adds 6 MB
    # per attachment even to a one-page reply, quickly exhausting SMTP limits.
    from fontTools.ttLib import TTFont
    from fontTools import subset
    font=TTFont(Path(__file__).parent/'assets/NotoSansSC.ttf')
    options=subset.Options();options.name_IDs=['*'];options.name_legacy=True;options.name_languages=['*']
    subsetter=subset.Subsetter(options=options);subsetter.populate(text=title+'\n'+content+' 0123456789')
    subsetter.subset(font);font_stream=BytesIO();font.save(font_stream);font.close()
    font_id=uuid4();font_data=bytearray(font_stream.getvalue());font_key=font_id.bytes[::-1]
    for i in range(32):font_data[i]^=font_key[i%16]
    out=BytesIO()
    with zipfile.ZipFile(out,'w',zipfile.ZIP_DEFLATED) as z:
        z.writestr('[Content_Types].xml','<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Default Extension="odttf" ContentType="application/vnd.openxmlformats-officedocument.obfuscatedFont"/><Override PartName="/word/fontTable.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.fontTable+xml"/><Override PartName="/word/settings.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.settings+xml"/><Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/><Override PartName="/word/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml"/></Types>')
        z.writestr('_rels/.rels','<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/></Relationships>')
        z.writestr('word/_rels/document.xml.rels','<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/><Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/fontTable" Target="fontTable.xml"/><Relationship Id="rId3" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/settings" Target="settings.xml"/></Relationships>')
        z.writestr('word/document.xml',document);z.writestr('word/styles.xml',styles)
        z.writestr('word/fonts/NotoSansSC.odttf',bytes(font_data))
        z.writestr('word/fontTable.xml','<w:fonts xmlns:w="'+W+'" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><w:font w:name="Noto Sans SC"><w:family w:val="swiss"/><w:embedRegular r:id="rId1" w:fontKey="{'+str(font_id).upper()+'}"/></w:font></w:fonts>')
        z.writestr('word/_rels/fontTable.xml.rels','<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/font" Target="fonts/NotoSansSC.odttf"/></Relationships>')
        z.writestr('word/settings.xml','<w:settings xmlns:w="'+W+'"><w:embedTrueTypeFonts/><w:saveSubsetFonts/></w:settings>')
    return out.getvalue()


def make_docx(title,content):
    from .document_formats import format_docx
    return format_docx(_plain_docx(title,content),content)


def make_pdf(title,content):
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.enums import TA_LEFT
    from reportlab.platypus import SimpleDocTemplate,Paragraph,Spacer,LongTable,TableStyle
    from reportlab.lib import colors
    from .document_formats import markdown_blocks,inline_text,rich_inline,table_widths
    blocks=markdown_blocks(content)
    if rich_inline(blocks):
        # Native Word layout gives the embedded Chinese font real emphasis and
        # consistent inline/code/table styling. No raw HTML or external link is
        # passed to LibreOffice; conversion retains its bounded private profile.
        from .document_render import pdf_bytes
        return pdf_bytes(make_docx(title,content),'docx')
    font='TaskManagerNotoSC'
    if font not in pdfmetrics.getRegisteredFontNames():pdfmetrics.registerFont(TTFont(font,str(Path(__file__).parent/'assets/NotoSansSC.ttf')))
    base=ParagraphStyle('body',fontName=font,fontSize=11,leading=17,spaceAfter=7,wordWrap='CJK',alignment=TA_LEFT)
    heading=ParagraphStyle('heading',parent=base,fontSize=15,leading=21,spaceBefore=10,keepWithNext=True)
    title_style=ParagraphStyle('title',parent=base,fontSize=19,leading=26,spaceAfter=16,keepWithNext=True)
    story=[Paragraph(escape(title),title_style)]
    for kind,value in blocks:
        if kind=='table':
            count=max(map(len,value))
            if count>12 or len(value)>500:raise ValueError('文档表格过大，请拆分')
            cell=ParagraphStyle('cell',parent=base,fontSize=9,leading=13,spaceAfter=0)
            rows=[[Paragraph(escape(inline_text(v)),cell) for v in row]+['']*(count-len(row)) for row in value]
            table=LongTable(rows,colWidths=table_widths(value,504),repeatRows=1,hAlign='LEFT',splitByRow=1,splitInRow=0)
            table.setStyle(TableStyle([('BACKGROUND',(0,0),(-1,0),colors.HexColor('#eeeeee')),('GRID',(0,0),(-1,-1),0.4,colors.HexColor('#cccccc')),('VALIGN',(0,0),(-1,-1),'TOP'),('LEFTPADDING',(0,0),(-1,-1),6),('RIGHTPADDING',(0,0),(-1,-1),6),('TOPPADDING',(0,0),(-1,-1),6),('BOTTOMPADDING',(0,0),(-1,-1),6)]))
            story.extend([table,Spacer(1,10)])
        else:
            story.append(Paragraph(escape(inline_text(value)).replace('\n','<br/>'),heading if kind.startswith('heading') else base))
    stream=BytesIO();doc=SimpleDocTemplate(stream,pagesize=(612,792),leftMargin=54,rightMargin=54,topMargin=54,bottomMargin=54,title=title,author='Task Manager')
    def footer(canvas,document):
        canvas.saveState();canvas.setFont(font,9);canvas.drawRightString(558,30,str(document.page));canvas.restoreState()
    doc.build(story,onFirstPage=footer,onLaterPages=footer)
    return stream.getvalue()


def selected_pdf_pages(data,pages):
    """Copy explicitly selected PDF pages with pypdf, retaining page resources."""
    if not isinstance(pages,list) or not 1<=len(pages)<=80 or any(type(page) is not int or page<1 for page in pages) or len(set(pages))!=len(pages):
        raise ValueError('请选择1至80个不重复的页码，页码从1开始，并按需要的输出顺序排列')
    extract_document(data,'pdf')  # Shared byte/page/encryption/parser bounds.
    from pypdf import PdfReader,PdfWriter
    reader=PdfReader(BytesIO(data),strict=True);count=len(reader.pages)
    if max(pages)>count:raise ValueError('所选页码超出原PDF范围（共'+str(count)+'页）')
    writer=PdfWriter()
    try:
        # append, unlike copying only streams or text, carries selected widgets,
        # images, crop boxes, rotation and other page resources with the pages.
        writer.append(reader,pages=[page-1 for page in pages],import_outline=False)
        stream=BytesIO();writer.write(stream)
        return stream.getvalue(),count
    finally:
        writer.close();reader.close()


class MailFiles:
    def __init__(self,service):
        self.service=service;self.root=Path(service.directory)/'mail-files';self.root.mkdir(mode=0o700,parents=True,exist_ok=True)
        self.db=sqlite3.connect(Path(service.directory)/'mail-files.sqlite',isolation_level=None,timeout=5)
        self.db.execute('CREATE TABLE IF NOT EXISTS files(id TEXT PRIMARY KEY,record TEXT NOT NULL,text TEXT NOT NULL,original_path TEXT,request_hash TEXT)')
        if 'request_hash' not in [r[1] for r in self.db.execute('PRAGMA table_info(files)')]:self.db.execute('ALTER TABLE files ADD COLUMN request_hash TEXT')
        self.db.execute('CREATE TABLE IF NOT EXISTS business_document_specs(file_id TEXT PRIMARY KEY,spec TEXT NOT NULL,report TEXT NOT NULL)')
    def close(self):self.db.close()
    def _row(self,id):
        identifier(id);row=self.db.execute('SELECT record,text,original_path FROM files WHERE id=?',(id,)).fetchone()
        if not row:raise ValueError('找不到此资料文件，请重新选择')
        return row
    def get(self,id):
        record=json.loads(self._row(id)[0])
        from .draft_lifecycle import DraftLifecycle
        with DraftLifecycle(self.root.parent) as lifecycle:
            if lifecycle.exists(id):
                version=lifecycle.get(id)
                record.update({k:version[k] for k in ('familyId','revision','lifecycleStatus','supersededBy')})
        return record
    def list_files(self):
        records=[self.get(r[0]) for r in self.db.execute('SELECT id FROM files ORDER BY rowid DESC LIMIT 500')]
        return [r for r in records if r.get('lifecycleStatus','active')=='active'][:100]
    def archive_generated(self,file_ids,reason,expected_revisions=None,*,operation_id):
        from .file_archive import archive_generated
        return archive_generated(self,file_ids,reason,expected_revisions,operation_id=operation_id)
    def restore_generated(self,file_id,expected_revision,*,operation_id):
        from .file_archive import restore_generated
        return restore_generated(self,file_id,expected_revision,operation_id=operation_id)
    def bytes(self,id):
        record=self.get(id);path=self.root/id
        if path.is_symlink() or not path.is_file() or path.stat().st_size>MAX_BYTES:raise ValueError('文件副本缺失或异常，请重新导入')
        value=path.read_bytes()
        if len(value)!=record['byteSize'] or hashlib.sha256(value).hexdigest()!=record['sha256']:raise ValueError('文件副本已发生变化，不能继续使用旧预览')
        return value
    def read(self,id):
        self.bytes(id)
        return self._row(id)[1]
    def _save(self,name,data,source,*,source_mail=None,parents=None,artifact_id=None,original_path=None,request_hash=None,archive_origin=None):
        if not isinstance(data,bytes) or len(data)>MAX_BYTES:raise ValueError('单个文件须为不超过 10 MB 的原始字节')
        extension=Path(name).suffix.lower().lstrip('.')
        if extension not in TYPES:raise ValueError('支持 PDF、DOCX、CSV/XLSX、UTF-8 TXT/Markdown、PNG/JPEG/WebP 和 ZIP 文件')
        parse_info={}
        if extension=='xlsx':
            # Preserve safe original workbooks independently of the optional
            # strict table adapter. A merged title or formula is not a reason
            # to make the bytes unavailable to the isolated script runner.
            from .spreadsheet_files import xlsx_index
            content,warnings,parse_info=xlsx_index(data)
        else:content,warnings=extract_document(data,extension)
        id=identifier(artifact_id) if artifact_id else str(uuid4())
        digest=hashlib.sha256(data).hexdigest()
        existing=self.db.execute('SELECT record FROM files WHERE id=?',(id,)).fetchone()
        if existing:
            record=json.loads(existing[0]);self.bytes(id)
            if record['sha256']!=digest:raise ValueError('生成文件标识已存在，未覆盖已有版本')
            return record
        record=dict(id=id,name=safe_name(name),mediaType=TYPES[extension],extension=extension,byteSize=len(data),sha256=digest,createdAt=instant(),source=source,parentIds=parents or [],warnings=warnings)
        record.update(parse_info)
        if source_mail:record['sourceMailId']=source_mail
        if archive_origin:record['archiveOrigin']=archive_origin
        path=self.root/id
        self.db.execute('BEGIN IMMEDIATE');created=False
        try:
            if self.db.execute('SELECT count(*) FROM files').fetchone()[0]>=MAX_FILES:raise ValueError('本地资料库已达 500 个文件上限')
            if self.db.execute("SELECT COALESCE(SUM(json_extract(record,'$.byteSize')),0) FROM files").fetchone()[0]+len(data)>512*1024*1024:raise ValueError('本地资料库已达 512 MB 上限')
            # Recover a file left by a crash before its manifest transaction committed.
            # Only deterministic generated IDs can be retried this way, never links.
            if artifact_id and source=='generated' and path.exists():
                if path.is_symlink() or not path.is_file():raise ValueError('输出路径异常，未覆盖')
                if self.db.execute('SELECT 1 FROM files WHERE id=?',(id,)).fetchone():raise ValueError('输出已由另一个任务保存，请重新读取')
                path.unlink()
            with path.open('xb') as f:created=True;os.chmod(path,0o600);f.write(data);f.flush();os.fsync(f.fileno())
            self.db.execute('INSERT INTO files VALUES(?,?,?,?,?)',(id,json.dumps(record,ensure_ascii=False),content,original_path,request_hash));self.db.execute('COMMIT')
        except BaseException:
            self.db.execute('ROLLBACK')
            if created and not self.db.execute('SELECT 1 FROM files WHERE id=?',(id,)).fetchone():path.unlink(missing_ok=True)
            raise
        return record
    def import_local(self,path):
        path=Path(text(str(path),4000,True))
        if path.is_symlink() or not path.is_file():raise ValueError('请选择普通文件，暂不支持文件夹或符号链接')
        before=path.stat()
        if before.st_size>MAX_BYTES:raise ValueError('单个文件不能超过 10 MB')
        with path.open('rb') as f:data=f.read(MAX_BYTES+1)
        after=path.stat()
        if (before.st_size,before.st_mtime_ns,before.st_ino)!=(after.st_size,after.st_mtime_ns,after.st_ino):raise ValueError('文件在读取期间发生变化，请重新选择')
        return self._save(path.name,data,'local',original_path=str(path.resolve()))
    def import_attachment(self,local_id,index):
        if type(index) is not int or index<0:raise ValueError('附件选择无效')
        mail=self.service.mail.store.get_local(local_id);attachments=mail.get('attachments',[])
        if index>=len(attachments):raise ValueError('附件已不存在，请重新导入邮件')
        selected=attachments[index]
        if Path(selected.get('name','')).suffix.lower().lstrip('.') not in TYPES:raise ValueError('支持 PDF、DOCX、CSV/XLSX、UTF-8 TXT/Markdown、PNG/JPEG/WebP 和 ZIP 附件')
        manager=self.service.mail.manager;manager.check_identity(mail['bindingId'],mail['mailboxIdentity'])
        reader=manager.reader(mail['bindingId'])
        if not hasattr(reader,'get_attachment'):raise ValueError('当前邮箱连接暂不支持附件下载')
        data=reader.get_attachment(mail['id'],index,selected,MAX_BYTES)
        manager.check_identity(mail['bindingId'],mail['mailboxIdentity'])
        fresh=self.service.mail.store.get_local(local_id)
        if fresh['mailboxIdentity']!=mail['mailboxIdentity'] or fresh.get('attachments',[])!=attachments:raise ValueError('邮件附件信息已变化，请重新选择')
        return self._save(selected['name'],data,'mail',source_mail=local_id)
    def create_document(self,name,format,title,content,parent_ids,artifact_id=None):
        if format not in ('pdf','docx','txt','md'):raise ValueError('此工具生成 PDF、Word 或文本；CSV 请使用表格工具')
        title=checked_text(text(title,200,True).strip());content=checked_text(text(content,MAX_TEXT,True))
        if not isinstance(parent_ids,list) or len(parent_ids)>12:raise ValueError('引用文件过多')
        for id in parent_ids:self.bytes(id)
        signature=hashlib.sha256(json.dumps([name,format,title,content,parent_ids],ensure_ascii=False).encode('utf-8')).hexdigest()
        # A completed deterministic artifact is the checkpoint across job retries.
        if artifact_id and self.db.execute('SELECT 1 FROM files WHERE id=?',(identifier(artifact_id),)).fetchone():
            record=self.get(artifact_id);self.bytes(artifact_id)
            if record['source']!='generated' or record['parentIds']!=parent_ids or self.db.execute('SELECT request_hash FROM files WHERE id=?',(artifact_id,)).fetchone()[0]!=signature:raise ValueError('已有输出版本与此任务不一致')
            return record
        stem=Path(safe_name(name)).stem or 'document';name=stem+'.'+format
        if format=='pdf':data=make_pdf(title,content)
        elif format=='docx':data=make_docx(title,content)
        else:data=((('# ' if format=='md' else '')+title+'\n\n'+content+'\n')).encode('utf-8')
        return self._save(name,data,'generated',parents=parent_ids,artifact_id=artifact_id,request_hash=signature)
    def business_spec(self,id):
        self.bytes(id)
        row=self.db.execute('SELECT spec,report FROM business_document_specs WHERE file_id=?',(id,)).fetchone()
        return dict(document=json.loads(row[0]),calculation=json.loads(row[1])) if row else None
    def extract_pdf_pages(self,file_id,name,pages,artifact_id=None):
        source=self.get(file_id);data=self.bytes(file_id)
        if source['extension']!='pdf':raise ValueError('页码提取只支持PDF；Word请先转换为PDF')
        output,count=selected_pdf_pages(data,pages)
        signature=hashlib.sha256(json.dumps(['pdf_pages_v1',file_id,source['sha256'],name,pages],ensure_ascii=False).encode()).hexdigest()
        record=self._save((Path(safe_name(name)).stem or 'selected-pages')+'.pdf',output,'generated',
            parents=[file_id],artifact_id=artifact_id,request_hash=signature)
        record['pageSelection']=dict(sourceFileId=file_id,sourceSha256=source['sha256'],sourcePageCount=count,
            pages=list(pages),pageCount=len(pages),method='pypdf PdfWriter.append')
        self.db.execute('UPDATE files SET record=? WHERE id=?',(json.dumps(record,ensure_ascii=False),record['id']))
        return self.get(record['id'])

    def convert_to_pdf(self,file_id,name,artifact_id=None):
        """Export the exact stored Word package using the preview's native layout.

        A PDF is a derivative, not a replacement Word revision. The input hash
        and managed parent survive restarts; never reconstruct Markdown here.
        """
        from .document_render import pdf_bytes
        source=self.get(file_id);data=self.bytes(file_id)
        if source['extension']!='docx':raise ValueError('保留版式的 PDF 转换仅支持 DOCX；请选择可编辑的 Word 原件')
        filename=(Path(safe_name(name)).stem or Path(source['name']).stem)+'.pdf'
        signature=hashlib.sha256(json.dumps(['word_to_pdf_v1',file_id,source['sha256'],filename],ensure_ascii=False).encode()).hexdigest()
        existing=self.db.execute('SELECT request_hash FROM files WHERE id=?',(identifier(artifact_id),)).fetchone() if artifact_id else None
        if existing:
            if existing[0]!=signature:raise ValueError('已有 PDF 与本次转换来源不一致')
            record=self.get(artifact_id);self.bytes(artifact_id)
        else:
            if source.get('lifecycleStatus','active')!='active':raise ValueError('Word 已被替代或归档，请选择当前版本转换')
            output=pdf_bytes(data,'docx',self.root.parent/'mail-file-previews')
            # Re-check bytes after a slow renderer invocation before publishing.
            self.bytes(file_id)
            if self.get(file_id).get('lifecycleStatus','active')!='active':raise ValueError('转换期间 Word 版本已变化，请选择当前版本')
            record=self._save(filename,output,'generated',parents=[file_id],artifact_id=artifact_id,request_hash=signature)
        record['conversion']=dict(sourceFileId=file_id,sourceSha256=source['sha256'],
            sourceRevision=source.get('revision',1),method='LibreOffice writer_pdf_Export',
            preservesSourceLayout=True,visualReviewed=False)
        self.db.execute('UPDATE files SET record=? WHERE id=?',(json.dumps(record,ensure_ascii=False),record['id']))
        # Deterministic business calculations remain attached to a native export.
        spec=self.db.execute('SELECT spec,report FROM business_document_specs WHERE file_id=?',(file_id,)).fetchone()
        if spec:self.db.execute('INSERT OR IGNORE INTO business_document_specs VALUES(?,?,?)',(record['id'],*spec))
        return self.get(record['id'])
    def save_business_document(self,name,format,spec,report,parent_ids,artifact_id=None):
        from .business_documents import render_pdf,render_docx
        if format not in ('pdf','docx'):raise ValueError('结构化业务文档支持 PDF 或 DOCX')
        if not isinstance(parent_ids,list) or len(parent_ids)>12:raise ValueError('引用文件过多')
        for id in parent_ids:self.bytes(id)
        signature=hashlib.sha256(json.dumps(['business_document_v1',name,format,spec,parent_ids],ensure_ascii=False,sort_keys=True).encode()).hexdigest()
        # Resume after manifest commit without re-rendering time-dependent PDF metadata.
        existing=self.db.execute('SELECT request_hash FROM files WHERE id=?',(artifact_id,)).fetchone() if artifact_id else None
        if existing:
            if existing[0]!=signature:raise ValueError('已有模板文档与本次请求不一致')
            record=self.get(artifact_id);self.bytes(artifact_id)
        else:
            data=render_pdf(spec,report) if format=='pdf' else render_docx(spec,report)
            record=self._save(Path(safe_name(name)).stem+'.'+format,data,'generated',parents=parent_ids,artifact_id=artifact_id,request_hash=signature)
        self.db.execute('INSERT OR IGNORE INTO business_document_specs VALUES(?,?,?)',(record['id'],json.dumps(spec,ensure_ascii=False),json.dumps(report,ensure_ascii=False)))
        return record
    def inspect(self,id,offset=0,limit=50,*,sheet_name=None,cell_range=None):
        record=self.get(id);data=self.bytes(id);extension=record['extension']
        if type(offset) is not int or offset<0 or type(limit) is not int or not 1<=limit<=100:raise ValueError('结构分页参数无效')
        info=dict(fileId=id,format=extension,textCharacters=len(self.read(id)),warnings=record['warnings'])
        if extension!='xlsx' and (sheet_name is not None or cell_range is not None):raise ValueError('工作表与单元格范围仅适用于 XLSX')
        template=self.business_spec(id)
        if template:
            rows=template['document']['rows'];template['document']['rows']=rows[offset:offset+limit]
            template['rowCount']=len(rows);template['nextRowOffset']=offset+limit if offset+limit<len(rows) else None
            info['businessDocument']=template
            info['revision']=record.get('revision',1)
            info['revisionTool']='revise_business_document'
        if extension=='pdf':
            from pypdf import PdfReader
            reader=PdfReader(BytesIO(data),strict=True)
            info.update(pageCount=len(reader.pages),pages=[dict(page=i+1,textCharacters=len(p.extract_text() or '')) for i,p in enumerate(reader.pages)][offset:offset+limit],nextOffset=offset+limit if offset+limit<len(reader.pages) else None)
        elif extension=='docx':
            from .document_formats import inspect_docx
            result=inspect_docx(data);nodes=[dict(kind='paragraph',**p) for p in result.pop('paragraphs')]+[dict(kind='table',**t) for t in result.pop('tables')]
            page=[];used=0
            # Bounded structural previews. Truncation is explicit: abbreviated
            # text is never an exact expected-old value for a replacement.
            for n in nodes[offset:offset+limit]:
                if n['kind']=='paragraph' and len(n['text'])>4000:n.update(text=n['text'][:4000],truncated=True)
                if n['kind']=='table':
                    rows=n['rows'];n['rowCount']=len(rows);n['rows']=[];n['truncated']=False;cell_budget=20000
                    for row in rows[:100]:
                        cells=[]
                        for cell in row[:20]:
                            if len(cell['text'])>1000:cell.update(text=cell['text'][:1000],truncated=True)
                            cell_budget-=len(cell['text'])+60;cells.append(cell)
                        n['rows'].append(cells)
                        if len(row)>20:n['truncated']=True
                        if cell_budget<=0:break
                    n['truncated']|=len(n['rows'])<len(rows)
                size=len(json.dumps(n,ensure_ascii=False))
                if page and used+size>48000:break
                page.append(n);used+=size
            info.update(result,paragraphs=[n for n in page if n['kind']=='paragraph'],tables=[n for n in page if n['kind']=='table'],totalNodes=len(nodes),offset=offset,nextOffset=offset+len(page) if offset+len(page)<len(nodes) else None)
        elif extension=='csv':
            from .csv_documents import inspect_csv
            info.update(inspect_csv(data,offset=offset,limit=limit))
        elif extension=='xlsx':
            from .spreadsheet_files import inspect_xlsx
            info.update(inspect_xlsx(data,offset=offset,limit=limit,sheet_name=sheet_name,cell_range=cell_range))
        elif extension in ('png','jpg','jpeg','webp'):
            from .bundle_files import inspect_image
            info.update(inspect_image(data,extension))
        elif extension=='zip':
            from .bundle_files import inspect_archive
            info.update(inspect_archive(self,id,offset=offset,limit=limit))
        return info
    def preview(self,id,page=1):
        from .document_render import pdf_bytes,render_page
        record=self.get(id);data=self.bytes(id)
        if record['extension'] in ('png','jpg','jpeg','webp'):
            from .bundle_files import preview_image
            return dict(fileId=id,sha256=record['sha256'],**preview_image(data,record['extension'],page))
        rendered=render_page(pdf_bytes(data,record['extension'],self.root.parent/'mail-file-previews'),page)
        return dict(fileId=id,sha256=record['sha256'],**rendered)
    def validate(self,id):
        record=self.get(id);data=self.bytes(id);extension=record['extension'];checks=['原文件哈希与字节数一致'];warnings=[]
        result=dict(sha256=record['sha256'],structuralPassed=False,rendered=False,visualReviewed=False,checks=checks,warnings=warnings)
        if extension=='docx':
            from .document_formats import inspect_docx
            info=inspect_docx(data);checks.extend(['Word 可重新打开','段落、表格和图片关系可读取']);result.update(paragraphCount=len(info['paragraphs']),tableCount=len(info['tables']),imageCount=len(info['images']))
        elif extension=='csv':
            from .csv_documents import inspect_csv
            info=inspect_csv(data);checks.append('CSV 可按行列重新解析');result.update(rowCount=info['rowCount'],columnCount=len(info['columns']));warnings.extend(info.get('warnings',[]))
        elif extension=='xlsx':
            from .spreadsheet_files import inspect_xlsx
            info=inspect_xlsx(data);checks.append('XLSX 原工作簿结构可读取；未计算公式');result.update(rowCount=info['rowCount'],columnCount=info['columnCount'],sheetNames=info['sheetNames'],formulasEvaluated=False);warnings.extend(info.get('warnings',[]))
        elif extension in ('png','jpg','jpeg','webp'):
            from .bundle_files import inspect_image,preview_image
            result.update(inspect_image(data,extension));preview_image(data,extension)
            result['rendered']=True;checks.extend(['图片可完整解码','图片预览可生成'])
            warnings.append('图片预览不代表已理解图片内容；视觉结果单独记录。')
        elif extension=='zip':
            from .bundle_files import archive_index
            info=archive_index(data);result.update(memberCount=info['memberCount'],expandedBytes=info['expandedBytes'])
            checks.append('ZIP 目录、路径及展开边界通过检查');warnings.append('未逐一读取或验证成员内容；需要按需展开。')
        else:
            extract_document(data,extension);checks.append('输出格式可重新解析')
        result['structuralPassed']=True
        if extension in ('pdf','docx'):
            try:
                from .document_render import pdf_bytes,render_page
                rendered=pdf_bytes(data,extension,self.root.parent/'mail-file-previews')
                first=render_page(rendered,1);pages=first['pageCount']
                for page in range(2,pages+1):render_page(rendered,page)
                result.update(rendered=True,pageCount=pages);checks.append('所有页面可渲染')
                warnings.append('渲染成功仅证明页面可打开；布局及内容仍需视觉检查。')
            except ValueError as exc:warnings.append(str(exc))
        template=self.business_spec(id)
        if template:
            from .business_documents import normalize_document
            _,recomputed=normalize_document(template['document'])
            if recomputed!=template['calculation']:raise ValueError('业务文档计算清单不一致')
            result['businessCalculation']=recomputed
            result['templateLayout']=template['document']['style']
            checks.append('保存的结构化数据经 Decimal 重新计算一致；不代表已验证资料语义或视觉外观')
        record['validation']=result
        self.db.execute('UPDATE files SET record=? WHERE id=?',(json.dumps(record,ensure_ascii=False),id))
        return result
    def edit_docx(self,file_id,name,edits,artifact_id=None):
        from .document_formats import edit_docx_bytes
        source=self.get(file_id);data=self.bytes(file_id)
        if source['extension']!='docx':raise ValueError('局部修改工具仅支持 DOCX')
        signature=hashlib.sha256(json.dumps(['edit_docx',file_id,name,edits],ensure_ascii=False,sort_keys=True).encode()).hexdigest()
        if artifact_id and self.db.execute('SELECT 1 FROM files WHERE id=?',(identifier(artifact_id),)).fetchone():
            record=self.get(artifact_id);self.bytes(artifact_id)
            if self.db.execute('SELECT request_hash FROM files WHERE id=?',(artifact_id,)).fetchone()[0]!=signature:raise ValueError('已有修改副本与本次请求不一致')
            return record
        output=edit_docx_bytes(data,edits);filename=(Path(safe_name(name)).stem or '修订')+'.docx'
        return self._save(filename,output,'generated',parents=[file_id],artifact_id=artifact_id,request_hash=signature)
    def export_file(self,id,path):
        data=self.bytes(id);target=Path(text(str(path),4000,True));resolved=target.resolve()
        if target.is_symlink() or resolved.is_relative_to(Path(self.service.directory).resolve()):raise ValueError('请另存到资料库之外的文件夹')
        if any(r[0] and resolved==Path(r[0]) for r in self.db.execute('SELECT original_path FROM files')):raise ValueError('请另存为新文件，不能覆盖已导入的原资料')
        temporary=target.with_name('.'+target.name+'.'+str(uuid4())+'.tmp')
        try:
            with temporary.open('xb') as f:os.chmod(temporary,0o600);f.write(data);f.flush();os.fsync(f.fileno())
            os.replace(temporary,target)
        finally:temporary.unlink(missing_ok=True)
        return True
    def call(self,method,*args):
        if method=='list':
            if args:raise ValueError('无效参数')
            return self.list_files()
        if method=='get':id,=args;return self.get(id)
        if method=='read':id,=args;return self.read(id)
        if method=='inspect':return self.inspect(*args)
        if method=='preview':return self.preview(*args)
        if method=='validate':id,=args;return self.validate(id)
        if method=='importLocalFile':path,=args;return self.import_local(path)
        if method=='importAttachment':local_id,index=args;return self.import_attachment(local_id,index)
        if method=='exportFile':id,path=args;return self.export_file(id,path)
        raise ValueError('未知资料接口')
