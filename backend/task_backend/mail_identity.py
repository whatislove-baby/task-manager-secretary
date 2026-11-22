"""Structured RFC mailbox/thread metadata. These headers do not authenticate a sender.

Only top-level protocol headers are parsed. Subject lines and quoted mail bodies
never establish identity, reply targets or thread membership.
"""
from email import policy
from email.parser import BytesParser
from email.utils import getaddresses
import re

ID_FIELDS = ('fromAddress','fromName','replyToAddresses','toAddresses','ccAddresses',
             'messageId','inReplyTo','references','identityWarnings','identitySource')
_ATOM = r"[A-Za-z0-9!#$%&'*+\-/=?^_`{|}~]+"
_DOT_ATOM = _ATOM+r'(?:\.'+_ATOM+r')*'
# Common RFC msg-id dot atoms and domain literals. Obsolete quoted id-left
# syntax is deliberately rejected rather than silently repaired for replies.
MESSAGE_ID = re.compile('<'+_DOT_ATOM+'@(?:'+_DOT_ATOM+r'|\[[A-Za-z0-9:.\-]+\])>')


def address(value):
    if not isinstance(value,str) or len(value)>254 or re.search(r'[\x00-\x20\x7f]',value):
        raise ValueError('邮箱地址无效或包含控制字符')
    if not re.fullmatch(r"[A-Za-z0-9!#$%&'*+/=?^_`{|}~.-]+@[A-Za-z0-9.-]+",value):
        raise ValueError('请输入单个完整邮箱地址')
    local,domain=value.rsplit('@',1)
    if len(local)>64 or local.startswith('.') or local.endswith('.') or '..' in local or '.' not in domain:
        raise ValueError('邮箱地址无效')
    if any(not p or len(p)>63 or p.startswith('-') or p.endswith('-') for p in domain.split('.')):
        raise ValueError('邮箱域名无效')
    return value.casefold()


def header_text(value):
    value=str(value or '')
    if len(value)>16000:raise ValueError('邮件头过长')
    # RFC folding is accepted; an inserted new header line is not.
    value=re.sub(r'\r?\n[ \t]+',' ',value)
    if re.search(r'[\x00-\x08\x0a-\x1f\x7f]',value):raise ValueError('邮件头含控制字符')
    return value.strip()


def addresses(values):
    if not isinstance(values,list) or len(values)>100:raise ValueError('邮件地址列表无效')
    result=[]
    for name,raw in getaddresses([header_text(v) for v in values]):
        if not raw:continue
        canonical=address(raw)
        if canonical not in [v['address'] for v in result]:
            result.append(dict(name=header_text(name)[:300],address=canonical))
    if len(result)>100:raise ValueError('邮件地址过多')
    return result


def message_ids(value,multiple=True):
    value=header_text(value)
    if not value:return []
    # Strict addr-like msg-id, deliberately no body text, display name or URL.
    found=re.findall(r'<[^<>\s@]+@[^<>\s@]+>',value)
    if not found or re.sub(r'<[^<>\s@]+@[^<>\s@]+>','',value).strip():
        raise ValueError('邮件 Message-ID 或线程标识格式无效')
    if any(not MESSAGE_ID.fullmatch(v) or len(v)>998 for v in found) or len(found)>50 or (not multiple and len(found)!=1):
        raise ValueError('邮件线程标识无效或过长')
    return list(dict.fromkeys(found))


def header_metadata(message,source='rfc-headers'):
    result=dict(fromAddress='',fromName='',replyToAddresses=[],toAddresses=[],ccAddresses=[],
                messageId='',inReplyTo='',references=[],identityWarnings=[],identitySource=source)
    for header,key in [('From','fromAddress'),('Reply-To','replyToAddresses'),('To','toAddresses'),('Cc','ccAddresses')]:
        try:
            values=message.get_all(header,[])
            if header in ('From','Reply-To') and len(values)>1:raise ValueError('duplicate identity header')
            parsed=addresses(values)
            if values and not parsed:raise ValueError('empty identity header')
            if header=='From':
                if len(parsed)!=1:raise ValueError('ambiguous or absent sender')
                result.update(fromAddress=parsed[0]['address'],fromName=parsed[0]['name'])
            else:result[key]=[v['address'] for v in parsed]
        except (ValueError,TypeError):result['identityWarnings'].append('invalid-'+header.lower())
    for header,key,multiple in [('Message-ID','messageId',False),('In-Reply-To','inReplyTo',True),('References','references',True)]:
        try:
            values=message.get_all(header,[])
            if len(values)>1:raise ValueError('duplicate thread header')
            parsed=message_ids(values[0] if values else '',multiple)
            result[key]=parsed if key=='references' else ' '.join(parsed)
        except (ValueError,TypeError):result['identityWarnings'].append('invalid-'+header.lower())
    return result


def metadata_from_bytes(value):
    if not isinstance(value,bytes) or len(value)>65536:raise ValueError('邮件身份头缺失或过大')
    return header_metadata(BytesParser(policy=policy.default).parsebytes(value,headersonly=True))


def normalize_mail(value):
    """Upgrade old records in memory; no guessed subject-based threading."""
    result=dict(value)
    if 'identitySource' not in value:
        # Existing local records and importer fixtures may carry structured
        # fields already. Validate these, otherwise fall back to sender text.
        from email.message import EmailMessage
        message=EmailMessage(policy=policy.default)
        warnings=[]
        for key,header in [('sender','From'),('messageId','Message-ID'),('inReplyTo','In-Reply-To')]:
            if value.get(key):
                try:message[header]=header_text(value[key])
                except ValueError:warnings.append('invalid-'+header.lower())
        if value.get('fromAddress'):
            try:
                del message['From'];message['From']=address(value['fromAddress'])
            except ValueError:warnings.append('invalid-from')
        for key,header in [('replyToAddresses','Reply-To'),('toAddresses','To'),('ccAddresses','Cc'),('references','References')]:
            if value.get(key):
                try:
                    if not isinstance(value[key],list):raise ValueError('list required')
                    message[header]=(' ' if header=='References' else ', ').join(header_text(v) for v in value[key])
                except ValueError:warnings.append('invalid-'+header.lower())
        parsed=header_metadata(message,'legacy-sender')
        parsed['identityWarnings']=list(dict.fromkeys(parsed['identityWarnings']+warnings))
        if value.get('fromName') and parsed['fromAddress']:
            try:parsed['fromName']=header_text(value['fromName'])[:300]
            except ValueError:parsed['identityWarnings'].append('invalid-from-name')
        result.update(parsed)
    else:
        # A stored protocol-derived field must not silently fall back to a
        # display string if its canonical identity was rejected at ingestion.
        result.setdefault('identityWarnings',[])
        for key,default in [('fromAddress',''),('fromName',''),('replyToAddresses',[]),('toAddresses',[]),('ccAddresses',[]),('messageId',''),('inReplyTo',''),('references',[])]:
            result.setdefault(key,default)
    return result


def sender_matches(mail,needle):
    """Address-like filters are exact; human-name filters keep fuzzy semantics."""
    if not needle:return True
    needle=header_text(needle)
    if '@' in needle:
        parsed=addresses([needle])
        if len(parsed)!=1:raise ValueError('发件人邮箱筛选需要单个有效地址')
        return normalize_mail(mail)['fromAddress']==parsed[0]['address']
    return needle.casefold() in str(mail.get('sender','')).casefold()


def reply_context(original,to=''):
    original=normalize_mail(original)
    warnings=original.get('identityWarnings',[])
    if any(v in warnings for v in ('invalid-from','invalid-reply-to','invalid-message-id','invalid-in-reply-to','invalid-references')):
        raise ValueError('原邮件身份或线程头无效，不能据此创建回复；请核对后另起新邮件')
    candidates=original['replyToAddresses'] or ([original['fromAddress']] if original['fromAddress'] else [])
    candidates=[address(v) for v in candidates]
    if not candidates:raise ValueError('原邮件缺少可核对的回复邮箱')
    if not isinstance(to,str) or re.search(r'[\x00-\x1f\x7f]',to):raise ValueError('回复收件人无效或含控制字符')
    chosen=address(to.strip()) if to.strip() else candidates[0] if len(candidates)==1 else ''
    if chosen not in candidates:
        raise ValueError('回复收件人须匹配原邮件 Reply-To（如有）或 From 地址；请核对收件人')
    current=message_ids(original['messageId'],False)
    refs=original['references']
    if not isinstance(refs,list):raise ValueError('原邮件 References 无效')
    refs=message_ids(' '.join(refs))
    if not refs:refs=message_ids(original['inReplyTo'])
    if current:refs=[v for v in refs if v!=current[0]]+current
    if len(refs)>50:raise ValueError('回复邮件线程过长，请核对后另起新邮件')
    return dict(to=chosen,replyToAddresses=candidates,inReplyTo=current[0] if current else '',
                references=refs if current else [],sourceMessageId=current[0] if current else '',
                threadAvailable=bool(current))


def validate_reply_snapshot(value,receipt):
    """Validate persisted preview fields without rereading a mutable source mail."""
    if not isinstance(value,dict):raise ValueError('回复预览无效')
    if value.get('bindingId')!=receipt['bindingId'] or address(value.get('accountEmail'))!=address(receipt['from']):
        raise ValueError('回复预览所属邮箱不一致')
    candidates=value.get('replyToAddresses')
    if not isinstance(candidates,list) or not candidates or len(candidates)>100:raise ValueError('回复地址快照无效')
    candidates=[address(v) for v in candidates]
    if address(value.get('to'))!=address(receipt['to']) or address(receipt['to']) not in candidates:
        raise ValueError('回复预览的收件人不一致')
    source=message_ids(value.get('sourceMessageId'),False)
    parent=message_ids(value.get('inReplyTo'),False)
    refs=value.get('references')
    if not isinstance(refs,list):raise ValueError('回复线程快照无效')
    refs=message_ids(' '.join(refs))
    if type(value.get('threadAvailable')) is not bool or value['threadAvailable']!=bool(source) or source!=parent:
        raise ValueError('回复线程快照不一致')
    if (source and (not refs or refs[-1]!=source[0])) or (not source and refs):raise ValueError('回复线程快照不一致')
    return dict(inReplyTo=parent[0] if parent else '',references=refs)
