"""Bounded page rendering using PDFium and an optional local LibreOffice.

The renderer receives immutable file bytes, not user-controlled filesystem paths
or shell commands. LibreOffice gets a private profile and only self-contained,
macro-free DOCX documents; dynamic external fields/objects are refused.
"""
from io import BytesIO
from pathlib import Path
import base64
import hashlib
import json
import os
import subprocess
import tempfile
import threading

from .document_formats import check_docx

MAX_PDF_BYTES = 25 * 1024 * 1024
# PDFium has process-global native state, even for different documents.
# Keep construction, bitmap access and native cleanup on one thread at a time.
# https://pypdfium2.readthedocs.io/en/stable/python_api.html#incompatibility-with-threading
_PDFIUM_LOCK = threading.RLock()


def soffice_path():
    root=Path(__file__).resolve().parents[2]
    candidates=[os.environ.get('TM_SOFFICE',''),
        str(root/'libreoffice/LibreOffice.app/Contents/MacOS/soffice'),
        str(root/'libreoffice/LibreOfficeDev.app/Contents/MacOS/soffice'),
        str(root/'.libreoffice-runtime-mac/LibreOffice.app/Contents/MacOS/soffice'),
        '/Applications/LibreOffice.app/Contents/MacOS/soffice']
    for candidate in candidates:
        if candidate and Path(candidate).is_file() and os.access(candidate,os.X_OK):return candidate
    raise ValueError('Word 页面预览需要 LibreOffice 渲染器；请使用包含渲染器的 Mac 版本，或安装 LibreOffice')


def _cached(cache,key):
    if cache is None:return None
    target=cache/(key+'.pdf');manifest=cache/(key+'.json')
    try:
        if target.is_symlink() or manifest.is_symlink() or target.stat().st_size>MAX_PDF_BYTES:return None
        info=json.loads(manifest.read_text());data=target.read_bytes()
        if info.get('sha256')==hashlib.sha256(data).hexdigest():return data
    except (OSError,ValueError):pass
    return None


def pdf_bytes(data,extension,cache=None):
    if extension=='pdf':return data
    if extension!='docx':raise ValueError('页面预览支持 PDF 和 Word；CSV 请查看结构化表格')
    check_docx(data,rendering=True)
    cache=Path(cache) if cache is not None else None
    key=hashlib.sha256(data).hexdigest()
    old=_cached(cache,key)
    if old is not None:return old
    executable=soffice_path()
    with tempfile.TemporaryDirectory(prefix='tm-document-render-') as name:
        work=Path(name);work.chmod(0o700);profile=work/'profile';(profile/'user').mkdir(parents=True)
        (profile/'user/registrymodifications.xcu').write_text('''<?xml version="1.0" encoding="UTF-8"?>
<oor:items xmlns:oor="http://openoffice.org/2001/registry">
<item oor:path="/org.openoffice.Office.Common/Security/Scripting"><prop oor:name="MacroSecurityLevel" oor:op="fuse"><value>3</value></prop></item>
<item oor:path="/org.openoffice.Office.Writer/Content/Update"><prop oor:name="Link" oor:op="fuse"><value>0</value></prop></item>
</oor:items>''',encoding='utf8')
        source=work/'source.docx';source.write_bytes(data)
        env={**os.environ,'SAL_USE_VCLPLUGIN':'svp','SAL_DISABLE_OPENCL':'1'}
        try:
            completed=subprocess.run([executable,'-env:UserInstallation='+profile.as_uri(),'--headless','--nologo','--nodefault','--nolockcheck','--norestore','--convert-to','pdf:writer_pdf_Export','--outdir',str(work),str(source)],cwd=work,env=env,stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=60,check=False)
        except subprocess.TimeoutExpired:raise ValueError('Word 页面转换超时；原文件和已完成修改保留') from None
        except OSError:raise ValueError('Word 页面渲染器无法启动') from None
        output=work/'source.pdf'
        if completed.returncode!=0 or not output.is_file() or output.stat().st_size>MAX_PDF_BYTES:
            raise ValueError('Word 页面转换未完成；不能声称已检查排版')
        result=output.read_bytes()
        if not result.startswith(b'%PDF-'):raise ValueError('Word 渲染未返回有效 PDF')
    if cache is not None:
        cache.mkdir(mode=0o700,parents=True,exist_ok=True)
        # Never follow cache links. Both cache entries are disposable derivatives.
        for suffix in ('.pdf','.json'):
            if (cache/(key+suffix)).is_symlink():raise ValueError('页面缓存路径异常')
        with tempfile.NamedTemporaryFile(dir=cache,delete=False) as f:
            temp=Path(f.name);f.write(result)
        os.replace(temp,cache/(key+'.pdf'))
        (cache/(key+'.json')).write_text(json.dumps({'sha256':hashlib.sha256(result).hexdigest()}))
    return result


def render_page(data,page=1):
    with _PDFIUM_LOCK:
        return _render_page(data,page)


def _render_page(data,page):
    import pypdfium2 as pdfium
    if type(page) is not int or page<1:raise ValueError('页码须为从 1 开始的整数')
    document=None
    try:
        document=pdfium.PdfDocument(data)
        count=len(document)
        if not 1<=count<=80:raise ValueError('页面数量无效或超过 80 页')
        if page>count:raise ValueError('页码超出文件范围')
        sheet=document[page-1]
        try:
            width,height=sheet.get_size()
            if min(width,height)<=0:raise ValueError('页面尺寸无效')
            scale=min(2.0,1600/max(width,height))
            bitmap=sheet.render(scale=scale)
            try:
                picture=bitmap.to_pil().convert('RGB');output=BytesIO();picture.save(output,format='PNG')
                payload=output.getvalue()
                if len(payload)>8*1024*1024:raise ValueError('页面图像过大，请拆分文件')
                return dict(page=page,pageCount=count,width=picture.width,height=picture.height,
                            dataUrl='data:image/png;base64,'+base64.b64encode(payload).decode(),
                            mediaType='image/png',renderer='PDFium',rendered=True)
            finally:bitmap.close()
        finally:sheet.close()
    except ValueError:raise
    except Exception:raise ValueError('页面无法渲染，文件可能损坏或包含不支持的内容') from None
    finally:
        if document is not None:document.close()
