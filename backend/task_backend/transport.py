"""Plain-text MIME import and bounded HTTPS JSON calls."""
from email import policy
from email.parser import BytesParser
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser
import json
from pathlib import Path
from urllib import request,error
from .tree import parse_instant


class PlainHTML(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True);self.parts=[];self.skip=0
    def handle_starttag(self,tag,attrs):
        if tag in ('script','style'): self.skip+=1
        elif tag in ('br','p','div','li'): self.parts.append('\n')
    def handle_endtag(self,tag):
        if tag in ('script','style'): self.skip=max(0,self.skip-1)
        elif tag in ('p','div','li'): self.parts.append('\n')
    def handle_data(self,value):
        if not self.skip: self.parts.append(value)


def plain_html(value):
    parser=PlainHTML();parser.feed(value);return ''.join(parser.parts).strip()


def parse_mail(content):
    message=BytesParser(policy=policy.default).parsebytes(content)
    body=message.get_body(preferencelist=('plain','html'))
    value=body.get_content() if body else ''
    if body and body.get_content_type()=='text/html': value=plain_html(value)
    return message,str(value)


def import_text(path):
    path=Path(path)
    with path.open('rb') as stream: content=stream.read(2*1024*1024+1)
    if len(content)>2*1024*1024: raise ValueError('文件超过2MB')
    if path.suffix.lower()=='.txt': value=content.decode('utf-8-sig',errors='replace')
    elif path.suffix.lower()=='.eml':
        message,body=parse_mail(content)
        pieces=[]
        if message.get('Subject'): pieces.append('主题：'+str(message['Subject']))
        if message.get('Date'):
            try: pieces.append('邮件日期：'+parsedate_to_datetime(message['Date']).isoformat())
            except (ValueError,TypeError): pass
        if body: pieces.append(body)
        value='\n\n'.join(pieces)
    else: raise ValueError('只支持.eml或UTF-8 .txt文件')
    if len(value.encode('utf-16-le'))//2>20000: raise ValueError('正文超过20000字符，请选择需要处理的片段')
    if not value.strip(): raise ValueError('没有可读取的正文')
    return dict(name=path.name,text=value)


class NoRedirect(request.HTTPRedirectHandler):
    def redirect_request(self,*args,**kwargs): return None


def https_request(url,body,headers,limit,timeout):
    req=request.Request(url,data=body,headers=headers,method='POST' if body is not None else 'GET')
    try:
        with request.build_opener(NoRedirect()).open(req,timeout=timeout) as response:
            value=response.read(limit+1)
            if len(value)>limit: raise ValueError('服务回复超过资源上限')
            return value
    except error.HTTPError as exc: raise ValueError('服务返回HTTP '+str(exc.code)+'，请检查配置和授权') from None
    except (error.URLError,TimeoutError,OSError): raise ValueError('无法连接服务或请求超时，请检查网络和配置') from None
