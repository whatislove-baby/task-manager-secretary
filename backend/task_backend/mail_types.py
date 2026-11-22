from copy import deepcopy
from .tree import strict,calendar
from .archive import text
from .mail_identity import sender_matches

DEFAULT_FILTER=dict(dateMode='recent',recentDays=7,since='',until='',folderIds=[],includeChildren=False,sender='',subject='',unreadOnly=False)


def filter_value(value):
    strict(value,set(DEFAULT_FILTER)|{'senderExclude','subjectExclude','senders','excludeSenders'},DEFAULT_FILTER)
    result=deepcopy(value)
    if result['dateMode'] not in ('recent','since','range') or type(result['recentDays']) is not int or not 1<=result['recentDays']<=3650: raise ValueError('无效邮件日期范围')
    for key in ('since','until'):
        if result[key]: calendar(result[key])
    if result['dateMode']!='recent' and not result['since']: raise ValueError('请选择开始日期')
    if result['dateMode']=='range' and (not result['until'] or result['until']<result['since']): raise ValueError('结束日期不能早于开始日期')
    for key in ('includeChildren','unreadOnly'):
        if type(result[key]) is not bool: raise ValueError('无效筛选开关')
    ids=result['folderIds']
    if not isinstance(ids,list) or len(ids)>100 or any(not isinstance(v,str) for v in ids) or len(ids)!=len(set(ids)): raise ValueError('无效文件夹选择')
    for value in ids: text(value,2048,True)
    for key,limit in (('sender',320),('subject',300),('senderExclude',320),('subjectExclude',300)):
        if key in result: result[key]=text(result[key],limit).strip()
    for key in ('senders','excludeSenders'):
        if key in result:
            if not isinstance(result[key],list) or len(result[key])>500: raise ValueError('无效发件人筛选')
            result[key]=[text(v,1000,True).strip() for v in result[key]]
            if any(not v for v in result[key]): raise ValueError('发件人不能为空')
    return result


def matches_filter(item,value):
    subject=item['subject'].lower()
    def has(haystack,needle): return bool(needle) and needle.lower() in haystack
    return not (value['unreadOnly'] and not item['unread']) and sender_matches(item,value['sender']) and (not value['subject'] or has(subject,value['subject'])) and not (value.get('senderExclude') and sender_matches(item,value['senderExclude'])) and not has(subject,value.get('subjectExclude')) and (not value.get('senders') or any(sender_matches(item,v) for v in value['senders'])) and not any(sender_matches(item,v) for v in value.get('excludeSenders',[]))
