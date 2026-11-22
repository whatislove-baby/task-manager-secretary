"""Read-only IMAP and Microsoft Graph transport adapters."""
from contextlib import contextmanager
from datetime import date,datetime,timedelta,timezone
from email.header import decode_header,make_header
from email.utils import formataddr,decode_params,collapse_rfc2231_value,unquote
import json,re,ssl
from urllib.parse import quote,urlencode,urlsplit
from imapclient import IMAPClient
from .archive import text
from .tree import parse_instant
from .mail_types import filter_value,matches_filter
from .mail_store import compact
from .transport import parse_mail,plain_html
from .mail_identity import header_metadata,metadata_from_bytes,normalize_mail


class MailReadError(ValueError):
    def __init__(self,message,stage='transfer',code='MAIL_READ_FAILED',retryable=True):
        super().__init__(message);self.stage=stage;self.code=code;self.retryable=retryable


def boundaries(value):
    today=date.today();start=today+timedelta(days=1-value['recentDays']) if value['dateMode']=='recent' else date.fromisoformat(value['since'])
    end=date.fromisoformat(value['until'])+timedelta(days=1) if value['dateMode']=='range' else today+timedelta(days=1) if value['dateMode']=='recent' else None
    return datetime.combine(start,datetime.min.time()).astimezone(),datetime.combine(end,datetime.min.time()).astimezone() if end else None


def decoded(value):
    if isinstance(value,bytes): value=value.decode('utf-8',errors='replace')
    return str(make_header(decode_header(str(value or ''))))


def nil_eof(value,origin,offset,expected_size):
    # QQ returns IMAP NIL (Python None) when a selected MIME section is read
    # at EOF. NIL can also mean a missing section: accept it only at the exact
    # size declared by RFC822.SIZE/BODYSTRUCTURE and the requested offset.
    return value is None and type(expected_size) is int and expected_size>=0 and origin==offset==expected_size


def read_segments(client,uid,limit=10*1024*1024,expected_size=None):
    result=[];offset=0
    while offset<limit:
        take=min(256*1024,limit-offset)
        row=client.fetch([uid],[f'BODY.PEEK[]<{offset}.{take}>']).get(uid)
        if not row: raise MailReadError('邮件在读取期间已不存在','identity','MAIL_GONE')
        data=None
        for key,value in row.items():
            match=re.fullmatch(rb'BODY\[\](?:<(\d+)>)?',key if isinstance(key,bytes) else str(key).encode(),re.I)
            if match:
                origin=int(match[1]) if match[1] else 0
                if nil_eof(value,origin,offset,expected_size):value=b''
                if origin!=offset or not isinstance(value,bytes) or len(value)>take: raise MailReadError('服务器返回的正文分段位置不一致','transfer','SEGMENT_MISMATCH')
                data=value;break
        if data is None: raise MailReadError('服务器未返回请求的邮件分段','transfer','MISSING_SEGMENT')
        if not data:
            if not offset: raise MailReadError('服务器返回空正文','transfer','EMPTY_SOURCE')
            return b''.join(result),True
        result.append(data);offset+=len(data)
        # Even short chunks require a subsequent EOF request.
    return b''.join(result),False


def structure_parts(root,detail=False):
    result=dict(text=None,html=None,attachments=[]);stack=[(root,'')];count=0
    def params(raw):
        pairs=[(decoded(raw[i]).lower(),decoded(raw[i+1])) for i in range(0,len(raw or ())-1,2)]
        try:return {key:unquote(collapse_rfc2231_value(value)) for key,value in decode_params([('', '')]+pairs)[1:]}
        except (ValueError,TypeError,UnicodeError,LookupError):raise MailReadError('附件文件名编码无效','structure','ATTACHMENT_NAME_INVALID',False) from None
    while stack:
        node,path=stack.pop();count+=1
        if count>200:raise MailReadError('邮件结构过于复杂','structure','STRUCTURE_LIMIT',False)
        if not isinstance(node,(tuple,list)) or not node:continue
        if isinstance(node[0],list):
            for index,child in reversed(list(enumerate(node[0],1))):stack.append((child,(path+'.' if path else '')+str(index)))
            continue
        if len(node)<7:continue
        kind=decoded(node[0]).lower()+'/'+decoded(node[1]).lower();properties=params(node[2]);disposition='';disposition_params={}
        for field in node[7:]:
            if isinstance(field,(tuple,list)) and field and isinstance(field[0],bytes) and decoded(field[0]).lower() in ('attachment','inline'):
                disposition=decoded(field[0]).lower();disposition_params=params(field[1] if len(field)>1 else None);break
        filename=disposition_params.get('filename') or properties.get('name');size_known=type(node[6]) is int and node[6]>=0;size=node[6] if size_known else 0
        if kind in ('text/plain','text/html') and disposition!='attachment' and not filename:
            key='text' if kind=='text/plain' else 'html'
            if result[key] is None:result[key]=dict(key=path or '1',type=kind,size=size,sizeKnown=size_known)
        elif filename or disposition=='attachment' or kind.split('/')[0] in ('application','image','audio','video'):
            attachment=dict(name=re.sub(r'[\x00-\x1f]','',filename or '附件')[:300],type=kind,size=size,inline=disposition=='inline' and bool(node[3]))
            if detail:attachment.update(key=path or '1',sizeKnown=size_known)
            result['attachments'].append(attachment)
    return result


def read_text_part(client,uid,part,limit):
    section=part['key'];header_row=client.fetch([uid],[f'BODY.PEEK[{section}.MIME]']).get(uid,{})
    headers=next((v for k,v in header_row.items() if isinstance(k,bytes) and k.upper()==('BODY['+section+'.MIME]').encode() and isinstance(v,bytes)),None)
    if headers is None:return None
    pieces=[];offset=0;complete=False
    while offset<limit:
        take=min(256*1024,limit-offset);row=client.fetch([uid],[f'BODY.PEEK[{section}]<{offset}.{take}>']).get(uid,{})
        found=None
        for key,value in row.items():
            match=re.fullmatch(rb'BODY\['+re.escape(section.encode())+rb'\](?:<(\d+)>)?',key if isinstance(key,bytes) else str(key).encode(),re.I)
            if match:
                if nil_eof(value,int(match[1]) if match[1] else 0,offset,part.get('size') if part.get('sizeKnown',True) else None):value=b''
                if (int(match[1]) if match[1] else 0)!=offset or not isinstance(value,bytes) or len(value)>take:raise MailReadError('正文分段位置不一致','transfer','SEGMENT_MISMATCH')
                found=value;break
        if found is None:return None
        if not found:complete=True;break
        pieces.append(found);offset+=len(found)
    from email.parser import BytesParser
    from email import policy
    message=BytesParser(policy=policy.default).parsebytes(headers.rstrip(b'\r\n')+b'\r\n\r\n'+b''.join(pieces))
    value=message.get_content()
    if not isinstance(value,str):return None
    if part['type']=='text/html':value=plain_html(value)
    return dict(body=value.strip(),complete=complete,bytes=offset)


def attachment_matches(actual,expected):
    return all(actual.get(k)==expected.get(k) for k in ('name','type','size','inline'))


def read_attachment_part(client,uid,section,max_bytes,expected_size=None):
    """Fetch only an explicitly selected MIME leaf using read-only BODY.PEEK."""
    if not re.fullmatch(r'[1-9][0-9]*(?:\.[1-9][0-9]*)*',section):raise ValueError('附件分段标识无效')
    row=client.fetch([uid],[f'BODY.PEEK[{section}.MIME]']).get(uid,{})
    headers=next((v for k,v in row.items() if isinstance(k,bytes) and k.upper()==('BODY['+section+'.MIME]').encode() and isinstance(v,bytes)),None)
    if headers is None or len(headers)>65536:raise MailReadError('附件 MIME 信息缺失或过大','structure','ATTACHMENT_MIME_INVALID',False)
    # Encoded base64 needs 4/3 plus line breaks; quoted-printable up to 3x.
    limit=max_bytes*3+65536;pieces=[];offset=0
    while offset<limit:
        take=min(256*1024,limit-offset);row=client.fetch([uid],[f'BODY.PEEK[{section}]<{offset}.{take}>']).get(uid,{})
        found=None
        for key,value in row.items():
            match=re.fullmatch(rb'BODY\['+re.escape(section.encode())+rb'\](?:<(\d+)>)?',key if isinstance(key,bytes) else str(key).encode(),re.I)
            if match:
                if nil_eof(value,int(match[1]) if match[1] else 0,offset,expected_size):value=b''
                if (int(match[1]) if match[1] else 0)!=offset or not isinstance(value,bytes) or len(value)>take:raise MailReadError('附件分段位置不一致','transfer','SEGMENT_MISMATCH')
                found=value;break
        if found is None:raise MailReadError('服务器没有返回选中的附件','transfer','ATTACHMENT_MISSING')
        if not found:break
        pieces.append(found);offset+=len(found)
    else:raise ValueError('附件超过下载上限，未保存不完整文件')
    from email import policy
    from email.parser import BytesParser
    message=BytesParser(policy=policy.default).parsebytes(headers.rstrip(b'\r\n')+b'\r\n\r\n'+b''.join(pieces))
    if message.is_multipart():raise ValueError('暂不支持嵌套邮件附件')
    encoding=str(message.get('Content-Transfer-Encoding','7bit')).strip().lower()
    if encoding not in ('base64','quoted-printable','7bit','8bit','binary'):raise ValueError('附件编码暂不支持')
    data=message.get_payload(decode=True)
    if message.defects or not isinstance(data,bytes):raise ValueError('附件编码损坏，未保存')
    if len(data)>max_bytes:raise ValueError('单个附件不能超过 10 MB')
    return data


class ImapReader:
    def __init__(self,manager,id,client_factory=IMAPClient): self.manager=manager;self.id=id;self.factory=client_factory
    @contextmanager
    def session(self):
        config=self.manager.read(self.id)['config']
        if not config or not config['password']: raise ValueError('请先配置并连接IMAP邮箱')
        client=None
        try:
            client=self.factory(config['host'],port=config['port'],ssl=config['security']=='tls',ssl_context=ssl.create_default_context() if config['security']=='tls' else None,timeout=20)
            if config['security']=='starttls': client.starttls(ssl_context=ssl.create_default_context())
            client.login(config['username'],config['password']);yield client
        except MailReadError: raise
        except Exception as exc:
            if isinstance(exc,ValueError): raise
            raise MailReadError('IMAP连接或读取失败，请检查服务器、授权码、网络及邮箱认证方式') from None
        finally:
            if client:
                try: client.logout()
                except Exception:
                    try: client.shutdown()
                    except Exception: pass
    def test_connection(self):
        with self.session() as client: client.list_folders()
    def list_folders(self,parent=None):
        self.manager.identity(self.id)
        with self.session() as client:
            rows=client.list_folders();result=[]
            if len(rows)>500: raise ValueError('邮箱文件夹超过500个，请缩小范围')
            for flags,delimiter,name in rows:
                name=decoded(name);delimiter=decoded(delimiter) or '/';root=name.rsplit(delimiter,1)[0] if delimiter in name else None
                if (parent is None and root is not None) or (parent is not None and root!=parent): continue
                if b'\\Noselect' in flags: continue
                result.append(dict(id=name,name=name.rsplit(delimiter,1)[-1],parentId=root,childCount=sum(decoded(n).startswith(name+delimiter) and decoded(n).count(delimiter)==name.count(delimiter)+1 for _,_,n in rows),unreadCount=0))
            return result
    def summary(self,uid,row,folder,validity):
        envelope=row.get(b'ENVELOPE');at=row.get(b'INTERNALDATE')
        if not envelope or not isinstance(at,datetime): raise MailReadError('邮件摘要或收件时间无效','parse','INVALID_SUMMARY')
        subject=decoded(envelope.subject) or '（无主题）';senders=[]
        for item in envelope.from_ or []:
            name=decoded(item.name);mailbox=decoded(item.mailbox);host=decoded(item.host);address=mailbox+'@'+host if host else mailbox
            senders.append(f'{name} <{address}>' if name and address else address or name)
        sender=', '.join(senders);text(subject,10000);text(sender,10000)
        from email.message import EmailMessage
        from email import policy
        headers=EmailMessage(policy=policy.default)
        for field,header in [('from_','From'),('reply_to','Reply-To'),('to','To'),('cc','Cc')]:
            values=[]
            for item in getattr(envelope,field,None) or []:
                host=decoded(item.host);mailbox=decoded(item.mailbox)
                # IMAP group delimiters have no mailbox/host and are not senders.
                if not mailbox:continue
                values.append(formataddr((decoded(item.name),mailbox+'@'+host if host else mailbox)))
            if values:headers[header]=', '.join(values)
        for field,header in [('message_id','Message-ID'),('in_reply_to','In-Reply-To')]:
            value=getattr(envelope,field,None)
            if value:
                try:headers[header]=decoded(value)
                except ValueError:headers[header]='invalid'
        return dict(id=compact([folder,str(validity),uid]),subject=subject,sender=sender,receivedAt=at.astimezone(timezone.utc).isoformat(timespec='milliseconds').replace('+00:00','Z'),preview='',unread=b'\\Seen' not in row.get(b'FLAGS',()),webUrl='',imported=False,**header_metadata(headers,'imap-envelope'))
    def read(self,value):
        result=self.read_for_import(value,set());result.pop('skippedKnown',None);result.pop('skippedRemoteIds',None);return result
    def read_for_import(self,value,excluded_remote_ids):
        """Apply local exclusions before the 200-candidate cap, preserving UIDVALIDITY."""
        value=filter_value(value);before=self.manager.identity(self.id);start,end=boundaries(value);folders=value['folderIds'] or ['INBOX'];result={};limited=False;skipped=set()
        with self.session() as client:
            if value['includeChildren']:
                all_folders=[(decoded(delimiter) or '/',decoded(name)) for flags,delimiter,name in client.list_folders() if b'\\Noselect' not in flags]
                folders=list(dict.fromkeys(folders+[name for delimiter,name in all_folders if any(name.startswith(root+delimiter) for root in folders)]))
            if len(folders)>500: raise ValueError('邮箱文件夹超过500个')
            for folder in folders:
                text(folder,1500,True);state=client.select_folder(folder,readonly=True);validity=int(state[b'UIDVALIDITY'])
                criteria=['SINCE',(start-timedelta(days=1)).date()]
                if end: criteria.extend(['BEFORE',(end+timedelta(days=1)).date()])
                found=sorted(client.search(criteria),reverse=True)
                for offset in range(0,len(found),100):
                    rows=client.fetch(found[offset:offset+100],['ENVELOPE','INTERNALDATE','FLAGS','RFC822.SIZE'])
                    for uid in found[offset:offset+100]:
                        if uid not in rows: continue
                        item=self.summary(uid,rows[uid],folder,validity);at=parse_instant(item['receivedAt'])
                        if at<start or (end and at>=end) or not matches_filter(item,value): continue
                        if item['id'] in excluded_remote_ids:
                            skipped.add(item['id']);continue
                        result[item['id']]=item
                        if len(result)>=200: limited=True;break
                    if limited: break
                if limited: break
        self.manager.check_identity(self.id,before['identity'])
        return dict(messages=sorted(result.values(),key=lambda v:v['receivedAt'],reverse=True),limited=limited,skippedKnown=len(skipped),skippedRemoteIds=sorted(skipped))
    def get_attachment(self,id,index,expected,max_bytes=10*1024*1024):
        text(id,2048,True)
        if type(index) is not int or index<0 or not isinstance(expected,dict):raise ValueError('附件选择无效')
        try:
            folder,validity,uid=json.loads(id);text(folder,1500,True)
            if re.search(r'[\x00-\x1f\x7f]',folder) or type(uid) is not int or not 1<=uid<=0xffffffff or not re.fullmatch(r'\d{1,10}',validity):raise ValueError()
        except Exception:raise ValueError('无效 IMAP 邮件标识') from None
        before=self.manager.identity(self.id)
        with self.session() as client:
            state=client.select_folder(folder,readonly=True)
            if str(state[b'UIDVALIDITY'])!=validity:raise MailReadError('邮箱标识已变化，请重新导入','identity','UIDVALIDITY_CHANGED',False)
            metadata=client.fetch([uid],['BODYSTRUCTURE']).get(uid)
            if not metadata:raise ValueError('邮件已不存在，请重新导入')
            attachments=structure_parts(metadata.get(b'BODYSTRUCTURE'),detail=True)['attachments']
            if index>=len(attachments) or not attachment_matches(attachments[index],expected):raise ValueError('附件信息已变化，请重新导入邮件后选择')
            part=attachments[index]
            if part['size']>max_bytes*3+65536:raise ValueError('附件超过下载上限')
            data=read_attachment_part(client,uid,part['key'],max_bytes,part['size'] if part['sizeKnown'] else None)
            after=client.fetch([uid],['BODYSTRUCTURE']).get(uid)
            if not after or structure_parts(after.get(b'BODYSTRUCTURE'),detail=True)['attachments']!=attachments:raise ValueError('附件在下载期间已变化，请重新导入')
        self.manager.check_identity(self.id,before['identity']);return data
    def get_message(self,id,max_bytes=10*1024*1024):
        text(id,2048,True)
        try:
            folder,validity,uid=json.loads(id);text(folder,1500,True)
            if re.search(r'[\x00-\x1f\x7f]',folder) or type(uid) is not int or not 1<=uid<=0xffffffff or not re.fullmatch(r'\d{1,10}',validity):raise ValueError()
        except Exception:raise ValueError('无效IMAP邮件标识') from None
        before=self.manager.identity(self.id);limit=min(max(10*1024*1024,max_bytes),25*1024*1024)
        with self.session() as client:
            selected=client.select_folder(folder,readonly=True)
            if str(selected[b'UIDVALIDITY'])!=validity:raise MailReadError('邮箱标识已变化，请重新导入','identity','UIDVALIDITY_CHANGED',False)
            metadata=client.fetch([uid],['ENVELOPE','INTERNALDATE','FLAGS','RFC822.SIZE','BODYSTRUCTURE']).get(uid)
            if not metadata:raise MailReadError('邮件已不存在','identity','MAIL_GONE',False)
            item=self.summary(uid,metadata,folder,validity);info=structure_parts(metadata.get(b'BODYSTRUCTURE'));reported=metadata.get(b'RFC822.SIZE');large=type(reported) is int and reported>2*1024*1024;part=info['text'] or info['html'];body='';status='complete';received=0;note='';diagnostics=[]
            if large:
                section='HEADER.FIELDS (FROM REPLY-TO TO CC MESSAGE-ID IN-REPLY-TO REFERENCES)'
                headrow=client.fetch([uid],['BODY.PEEK['+section+']']).get(uid,{})
                rawhead=next((v for k,v in headrow.items() if isinstance(k,bytes) and k.upper().startswith(b'BODY[HEADER.FIELDS') and isinstance(v,bytes)),None)
                if rawhead is not None:item.update(metadata_from_bytes(rawhead))
                else:diagnostics.append('服务器未返回完整身份头，仅保留 IMAP 摘要字段。')
                value=read_text_part(client,uid,part,limit) if part else None
                if value:body=value['body'];status='complete' if value['complete'] else 'partial';received=value['bytes'];diagnostics.append('大邮件仅读取文字正文，附件未下载。')
                else:status='unavailable';note='服务器无法单独提供文字正文，摘要和附件信息已保留，可在原邮箱查看。'
            else:
                raw,complete=read_segments(client,uid,limit,reported);received=len(raw)
                try:
                    message,body=parse_mail(raw)
                    item.update(header_metadata(message))
                    if complete and any(type(v).__name__ in ('CloseBoundaryNotFoundDefect','StartBoundaryNotFoundDefect') for v in message.defects):raise ValueError('Invalid MIME boundary')
                    status='complete' if complete else 'partial'
                except Exception:
                    repeated,_=read_segments(client,uid,limit,reported)
                    if raw!=repeated:raise MailReadError('两次读取的正文内容不一致，未保存正文','transfer','CONTENT_UNSTABLE')
                    value=read_text_part(client,uid,part,limit) if part else None
                    if value:body=value['body'];status='complete' if value['complete'] else 'partial';received=value['bytes'];note='原邮件结构异常，已单独保留文字正文。'
                    else:status='unavailable';note='原邮件格式异常，摘要已保存，可在原邮箱查看。'
                if type(reported) is int and received!=reported:diagnostics.append('报告大小与实际传输大小不同，以实际传输和结构校验为准。')
            if status=='partial':note='本次只取得部分正文，可补读后重新分析。'
            if not client.fetch([uid],['UID']).get(uid):raise MailReadError('邮件在读取期间被移动或删除','identity','MAIL_GONE',False)
        self.manager.check_identity(self.id,before['identity'])
        return {**item,'body':body,'preview':body[:300],'bodyStatus':status,'bodyBytes':len(body.encode('utf-8')),'receivedBytes':received,'reportedSize':reported,'attachments':info['attachments'],'hasHtml':info['html'] is not None,'bodyNote':note,'diagnostics':diagnostics}


def validate_graph_url(value):
    url=urlsplit(value)
    if url.scheme!='https' or url.hostname!='graph.microsoft.com' or url.username or url.password or url.fragment or not url.path.startswith('/v1.0/me/') or url.port not in (None,443): raise ValueError('拒绝未知Microsoft邮件地址')
    return value


class GraphReader:
    ROOT='https://graph.microsoft.com/v1.0/me/'
    def __init__(self,manager,id): self.manager=manager;self.id=id
    def get(self,url,limit=5*1024*1024):
        validate_graph_url(url);token=self.manager.get_token(self.id)
        return json.loads(self.manager.request(url,None,{'Authorization':'Bearer '+token,'Accept':'application/json','Prefer':'IdType="ImmutableId", outlook.body-content-type="text"'},limit,20))
    def pages(self,url):
        seen=set()
        while url:
            validate_graph_url(url)
            if url in seen or len(seen)>=100: raise ValueError('邮箱分页循环或范围过大')
            seen.add(url);value=self.get(url)
            if not isinstance(value.get('value'),list) or len(value['value'])>1000: raise ValueError('邮箱分页响应格式无效')
            yield value['value'];url=value.get('@odata.nextLink')
    def list_folders(self,parent=None):
        before=self.manager.identity(self.id);path='mailFolders' if parent is None else 'mailFolders/'+quote(text(parent,2048,True),safe='')+'/childFolders';result=[]
        for page in self.pages(self.ROOT+path+'?$top=100'):
            for v in page: result.append(dict(id=v['id'],name=v['displayName'],childCount=v.get('childFolderCount',0),unreadCount=v.get('unreadItemCount',0),parentId=parent))
        self.manager.check_identity(self.id,before['identity']);return result
    def summary(self,value):
        id=text(value['id'],2048,True);subject=text(value.get('subject') or '（无主题）',10000);sender=value.get('from',{}).get('emailAddress',{});at=parse_instant(value['receivedDateTime'])
        address=sender.get('address','');name=sender.get('name','');url=value.get('webLink','')
        parsed=urlsplit(url)
        if parsed.scheme!='https' or parsed.hostname not in ('outlook.office.com','outlook.office365.com','outlook.live.com') or parsed.username or parsed.password: url=''
        result=dict(id=id,subject=subject,sender=f'{name} <{address}>' if name and address else name or address,receivedAt=at.astimezone(timezone.utc).isoformat(timespec='milliseconds').replace('+00:00','Z'),preview=str(value.get('bodyPreview',''))[:20000],unread=not value.get('isRead',False),webUrl=url,imported=False,
                    fromAddress=address,fromName=name,messageId=value.get('internetMessageId',''),
                    replyToAddresses=[v.get('emailAddress',{}).get('address','') for v in value.get('replyTo',[])],
                    toAddresses=[v.get('emailAddress',{}).get('address','') for v in value.get('toRecipients',[])],
                    ccAddresses=[v.get('emailAddress',{}).get('address','') for v in value.get('ccRecipients',[])])
        result=normalize_mail(result);result['identitySource']='graph'
        if 'internetMessageHeaders' in value:
            from email.message import EmailMessage
            from email import policy
            headers=EmailMessage(policy=policy.default)
            for item in value['internetMessageHeaders']:
                key=str(item.get('name',''))
                if key.lower() not in ('from','reply-to','to','cc','message-id','in-reply-to','references'):continue
                try:headers[key]=str(item.get('value',''))
                except ValueError:
                    result['identityWarnings'].append('invalid-'+key.lower())
            parsed=header_metadata(headers,'graph-headers')
            # Graph resource recipient fields remain usable when optional raw
            # header fields were omitted from its response.
            for key in ('From','Reply-To','To','Cc','Message-ID','In-Reply-To','References'):
                if headers.get(key) is not None:
                    fields={'From':['fromAddress','fromName'],'Reply-To':['replyToAddresses'],'To':['toAddresses'],
                            'Cc':['ccAddresses'],'Message-ID':['messageId'],'In-Reply-To':['inReplyTo'],'References':['references']}[key]
                    for field in fields:result[field]=parsed[field]
                    result['identityWarnings'].extend(w for w in parsed['identityWarnings'] if w=='invalid-'+key.lower())
            result['identitySource']='graph-headers'
        return result
    def read(self,value):
        result=self.read_for_import(value,set());result.pop('skippedKnown',None);result.pop('skippedRemoteIds',None);return result
    def read_for_import(self,value,excluded_remote_ids):
        value=filter_value(value);before=self.manager.identity(self.id);start,end=boundaries(value);folders=list(value['folderIds'] or ['inbox']);visited=set();index=0
        while index<len(folders):
            folder=folders[index];index+=1
            if folder in visited: continue
            visited.add(folder)
            if len(visited)>500: raise ValueError('邮箱文件夹超过500个')
            if value['includeChildren']:
                children=self.list_folders(folder)
                if any(v['id']==folder for v in children): raise ValueError('邮箱文件夹循环')
                folders.extend(v['id'] for v in children if v['id'] not in visited)
        result={};limited=False;skipped=set()
        for folder in dict.fromkeys(folders):
            condition='receivedDateTime ge '+start.isoformat()
            if end: condition+=' and receivedDateTime lt '+end.isoformat()
            query=urlencode({'$select':'id,subject,from,replyTo,toRecipients,ccRecipients,internetMessageId,receivedDateTime,bodyPreview,isRead,webLink','$top':'100','$orderby':'receivedDateTime desc','$filter':condition})
            for page in self.pages(self.ROOT+'mailFolders/'+quote(folder,safe='')+'/messages?'+query):
                for raw in page:
                    item=self.summary(raw);at=parse_instant(item['receivedAt'])
                    if at<start or (end and at>=end) or not matches_filter(item,value): continue
                    if item['id'] in excluded_remote_ids:
                        skipped.add(item['id']);continue
                    result[item['id']]=item
                    if len(result)>=200: limited=True;break
                if limited: break
            if limited: break
        self.manager.check_identity(self.id,before['identity']);return dict(messages=sorted(result.values(),key=lambda v:v['receivedAt'],reverse=True),limited=limited,skippedKnown=len(skipped),skippedRemoteIds=sorted(skipped))
    def get_attachment(self,id,index,expected,max_bytes=10*1024*1024):
        import base64
        text(id,2048,True)
        if type(index) is not int or index<0 or not isinstance(expected,dict):raise ValueError('附件选择无效')
        before=self.manager.identity(self.id);base=self.ROOT+'messages/'+quote(id,safe='')+'/attachments'
        found=[]
        for page in self.pages(base+'?'+urlencode({'$select':'id,name,contentType,size,isInline','$top':100})):
            found.extend(page)
            if len(found)>200:raise ValueError('邮件附件数量超过 200 个')
        if index>=len(found):raise ValueError('附件已不存在，请重新导入')
        def meta(v):return dict(name=v.get('name','附件'),type=v.get('contentType','application/octet-stream'),size=max(0,v.get('size',0)),inline=bool(v.get('isInline')))
        selected=found[index]
        if not attachment_matches(meta(selected),expected):raise ValueError('附件信息已变化，请重新导入邮件后选择')
        if sum(attachment_matches(meta(v),expected) for v in found)>1:raise ValueError('附件名称和大小重复，无法确认所选附件，请在原邮箱另存后导入')
        if selected.get('size',0)>max_bytes:raise ValueError('单个附件不能超过 10 MB')
        raw=self.get(base+'/'+quote(text(selected['id'],2048,True),safe=''),max_bytes*2+65536)
        if raw.get('id')!=selected['id'] or not attachment_matches(meta(raw),expected):raise ValueError('附件在下载期间发生变化')
        if raw.get('@odata.type')!='#microsoft.graph.fileAttachment':raise ValueError('暂不支持云链接或嵌套邮件附件')
        try:data=base64.b64decode(raw['contentBytes'],validate=True)
        except Exception:raise ValueError('附件编码损坏，未保存') from None
        if len(data)>max_bytes:raise ValueError('单个附件不能超过 10 MB')
        self.manager.check_identity(self.id,before['identity']);return data
    def get_message(self,id,max_bytes=10*1024*1024):
        text(id,2048,True);before=self.manager.identity(self.id)
        query=urlencode({'$select':'id,subject,from,replyTo,toRecipients,ccRecipients,internetMessageId,internetMessageHeaders,receivedDateTime,bodyPreview,isRead,webLink,body,hasAttachments','$expand':'attachments($select=name,contentType,size,isInline)'})
        raw=self.get(self.ROOT+'messages/'+quote(id,safe='')+'?'+query,16*1024*1024);item=self.summary(raw)
        if item['id']!=id: raise MailReadError('邮件正文标识不匹配','identity','MESSAGE_ID_MISMATCH',False)
        body=raw.get('body',{}).get('content','');html=raw.get('body',{}).get('contentType','').lower()=='html'
        if html: body=plain_html(body)
        encoded=body.encode('utf-8');limit=min(max(max_bytes,10*1024*1024),25*1024*1024);complete=len(encoded)<=limit
        if not complete: body=encoded[:limit].decode('utf-8',errors='ignore')
        attachments=[dict(name=v.get('name','附件'),type=v.get('contentType','application/octet-stream'),size=max(0,v.get('size',0)),inline=bool(v.get('isInline'))) for v in raw.get('attachments',[]) if isinstance(v,dict)]
        self.manager.check_identity(self.id,before['identity'])
        return {**item,'body':body,'bodyStatus':'complete' if complete else 'partial','bodyBytes':len(body.encode('utf-8')),'attachments':attachments,'hasHtml':html,'bodyNote':'' if complete else '仅取得部分正文，可补读后再分析。'}
