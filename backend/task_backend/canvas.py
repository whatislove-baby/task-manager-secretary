"""Canvas calendar parsing, encrypted subscription and transactional sync."""
from datetime import date,datetime,timedelta,timezone
from pathlib import Path
import json,re,secrets
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo,ZoneInfoNotFoundError
from tzlocal import get_localzone_name
from icalendar import Calendar
from dateutil.rrule import rruleset,rrulestr
from .archive import text,source_value
from .settings import atomic_json
from .transport import plain_html
from .tree import due_value,deadline_day
from . import vault
from .locks import file_lock


class FeedLimit(ValueError): pass


def validate_timezones(component):
    """Do not turn an unresolved explicit TZID into an OS-local time.

    icalendar resolves IANA names and embedded VTIMEZONE definitions. Custom
    definitions are valid even when ZoneInfo cannot provide a display label.
    Recurrence identities and exclusions need the same check as DTSTART.
    """
    for name in ('DTSTART','RECURRENCE-ID','RDATE','EXDATE'):
        properties=component.get(name,[])
        for prop in properties if isinstance(properties,list) else [properties]:
            if not prop.params.get('TZID'): continue
            for entry in getattr(prop,'dts',[prop]):
                value=getattr(entry,'dt',None)
                if isinstance(value,datetime) and (value.tzinfo is None or value.utcoffset() is None):
                    raise ValueError('事项包含无法识别的时区，未导入；请检查日历时区设置')


def absolute(value):
    if not isinstance(value,datetime): return datetime.combine(value,datetime.min.time()).astimezone()
    if value.tzinfo is None: value=value.astimezone()
    elif value.astimezone(timezone.utc).astimezone(value.tzinfo).replace(tzinfo=None)!=value.replace(tzinfo=None): raise ValueError('事项的时区或夏令时时间无效')
    return value


def identity(value):
    if isinstance(value,datetime): return absolute(value).astimezone(timezone.utc).isoformat(timespec='milliseconds').replace('+00:00','Z')
    if isinstance(value,date): return value.isoformat()
    raise ValueError('缺少有效日期或时间')


def deadline(component,value):
    if not isinstance(value,datetime): return due_value(dict(mode='value',value=dict(kind='date',date=identity(value))))
    at=absolute(value).astimezone(timezone.utc).replace(second=0,microsecond=0)
    zone=str(component['DTSTART'].params.get('TZID',''))
    if not zone:
        # A floating time has been converted with the OS local timezone above.
        # Keep a portable UTC label unless an IANA zone was explicitly provided.
        zone='UTC' if value.tzinfo and value.utcoffset()==timedelta(0) else get_localzone_name()
    try: ZoneInfo(zone)
    except ZoneInfoNotFoundError: zone='UTC'
    return due_value(dict(mode='value',value=dict(kind='minute',at=at.isoformat(timespec='milliseconds').replace('+00:00','Z'),timeZone=zone)))


def safe_url(value):
    value=str(value or '').strip()
    try:
        url=urlsplit(value)
        if url.scheme in ('https','http') and url.hostname and not url.username and not url.password and len(value)<=8000: return value
    except ValueError: pass
    return ''


def parse_feed(raw,now=None):
    now=now or datetime.now().astimezone()
    if len(raw.encode('utf-8'))>5*1024*1024: raise FeedLimit('Canvas日历超过5MB，未同步')
    raw=raw.lstrip('\ufeff')
    if not re.search(r'^BEGIN:VCALENDAR\s*$',raw,re.M|re.I) or not re.search(r'^END:VCALENDAR\s*$',raw,re.M|re.I): raise ValueError('Canvas日历格式无效，未保存任何事项')
    try: calendar=Calendar.from_ical(raw)
    except Exception: raise ValueError('Canvas日历格式无效，未保存任何事项') from None
    groups={};items=[];issues=[];expanded=0
    lower=now-timedelta(days=30);upper=now+timedelta(days=366)
    def issue(component,reason):
        title=re.sub(r'(?:https?|webcal)://\S+','［链接］',plain_html(str(component.get('SUMMARY',''))))[:200] or '未命名事项'
        issues.append(dict(title=title,reason=reason))
        if len(items)+len(issues)>5000: raise FeedLimit('Canvas事项超过5000项')
    for component in calendar.walk('VEVENT'):
        uid=str(component.get('UID','')).strip()
        if not uid or len(uid)>850: issue(component,'缺少有效事项标识');continue
        groups.setdefault(uid,[]).append(component)
    for uid,group in groups.items():
        pending={};pending_sizes={}
        try:
            versions={}
            for component in group:
                if component.errors: raise ValueError('事项格式无效')
                validate_timezones(component)
                rid=component.get('RECURRENCE-ID')
                if rid and rid.params.get('RANGE'): raise ValueError('暂不支持批量修改后续重复事项')
                key=identity(rid.dt) if rid else ''
                previous=versions.get(key)
                def rank(c):
                    changed=c.get('LAST-MODIFIED',c.get('DTSTAMP'))
                    return int(c.get('SEQUENCE',0)),absolute(changed.dt).timestamp() if changed else 0
                if previous is None or rank(component)>rank(previous): versions[key]=component
                elif rank(component)==rank(previous) and component.to_ical()!=previous.to_ical(): raise ValueError('同一事项存在冲突版本')
            def add(component,start,rid=None):
                nonlocal expanded
                if str(component.get('STATUS','')).upper()=='CANCELLED': return
                title=plain_html(str(component.get('SUMMARY','')));text(title,200,True)
                description=plain_html(str(component.get('DESCRIPTION','')))
                key=json.dumps([uid,rid] if rid else [uid],ensure_ascii=False,separators=(',',':'));text(key,1000,True)
                item=dict(key=key,title=title,description=description,url=safe_url(component.get('URL')),due=deadline(component,start))
                pending[key]=item;pending_sizes[key]=len(description.encode('utf-8'))
                if expanded+sum(pending_sizes.values())>40*1024*1024: raise FeedLimit('Canvas重复展开的说明总量超过40MiB')
                if len(items)+len(pending)+len(issues)>5000: raise FeedLimit('Canvas事项超过5000项')
            master=versions.get('')
            if master is None:
                for rid,c in versions.items():
                    if str(c.get('STATUS','')).upper()!='CANCELLED': add(c,c.decoded('DTSTART'),rid)
            elif str(master.get('STATUS','')).upper()!='CANCELLED':
                start=master.decoded('DTSTART')
                if not master.get('RRULE') and not master.get('RDATE'):
                    if len(versions)>1: raise ValueError('重复事项缺少规则')
                    add(master,start)
                else:
                    rules=rruleset();is_date=not isinstance(start,datetime)
                    seed=datetime.combine(start,datetime.min.time()) if is_date else start
                    rule=master.get('RRULE')
                    if rule:
                        if str(rule.get('FREQ',[''])[0]) not in ('DAILY','WEEKLY','MONTHLY','YEARLY'): raise ValueError('暂不支持按小时或分钟重复的事项')
                        rules.rrule(rrulestr(rule.to_ical().decode(),dtstart=seed))
                    rules.rdate(seed)
                    for name,method in (('RDATE',rules.rdate),('EXDATE',rules.exdate)):
                        props=master.get(name,[])
                        for prop in props if isinstance(props,list) else [props]:
                            for v in prop.dts: method(datetime.combine(v.dt,datetime.min.time()) if is_date else v.dt)
                    seen=set()
                    for index,occurrence in enumerate(rules):
                        if index>=20000: raise ValueError('重复事项的计算范围过大')
                        occurrence=occurrence.date() if is_date else occurrence
                        rid=identity(occurrence);seen.add(rid)
                        c=versions.get(rid,master)
                        actual=c.decoded('DTSTART') if c is not master and c.get('DTSTART') else occurrence
                        if str(c.get('STATUS','')).upper()!='CANCELLED' and lower<=absolute(actual)<=upper: add(c,actual,rid)
                        if absolute(occurrence)>upper: break
                    for rid,c in versions.items():
                        if rid and rid not in seen and str(c.get('STATUS','')).upper()!='CANCELLED':
                            actual=c.decoded('DTSTART')
                            if lower<=absolute(actual)<=upper: add(c,actual,rid)
            items.extend(pending.values());expanded+=sum(pending_sizes.values())
        except FeedLimit: raise
        except Exception as exc:
            reason=str(exc) if isinstance(exc,ValueError) else '事项格式无效，未导入；其他有效事项仍可同步'
            # Never include arbitrary source text or URLs in errors.
            if re.search(r'https?://',reason) or len(reason)>200: reason='事项格式无效，未导入'
            issue(group[0],reason)
    return dict(items=items,issues=issues)


class CanvasService:
    METHODS={'getCanvasConfig','saveCanvasFeed','clearCanvasFeed','syncCanvas'}
    def __init__(self,service):
        self.service=service;self.file=service.directory/'canvas-feed.json'
        self.sources_file=service.directory/'canvas-source-hosts.json'

    def sources(self):
        if not self.sources_file.exists(): return {}
        try:
            value=json.loads(self.sources_file.read_text(encoding='utf-8-sig'))
            if not isinstance(value,dict) or any(not isinstance(host,str) or not host or not isinstance(source,str) or not re.fullmatch('[a-f0-9]{64}',source) for host,source in value.items()): raise ValueError()
            return value
        except Exception: raise ValueError('Canvas来源登记无法读取，请先备份数据目录并检查配置文件') from None

    def read(self):
        if not self.file.exists(): return None
        try:
            value=json.loads(self.file.read_text(encoding='utf-8-sig'))
            if set(value)!={'sourceId','host','encryptedUrl'} or not re.fullmatch('[a-f0-9]{64}',value['sourceId']) or not value['host'] or not value['encryptedUrl']: raise ValueError()
            return value
        except Exception: raise ValueError('Canvas配置无法读取，请先备份数据目录并检查配置文件') from None

    def call(self,method,*args):
        old=self.read()
        if method=='getCanvasConfig':
            if args: raise ValueError('无效参数')
            return dict(configured=bool(old),host=old['host'] if old else '')
        if method=='saveCanvasFeed':
            raw,=args;text(raw,16000,True)
            raw=re.sub(r'^webcal:','https:',raw.strip(),flags=re.I);url=urlsplit(raw)
            if url.scheme!='https' or not url.hostname or url.username or url.password or url.fragment: raise ValueError('请输入有效的HTTPS Canvas日历订阅链接')
            with file_lock(self.file.with_suffix('.lock')):
                old=self.read();sources=self.sources()
                # Persist only public host identity, never the private feed URL.
                # A sole database source is not evidence of a matching school.
                if old: sources[old['host']]=old['sourceId']
                source=sources.get(url.netloc) or secrets.token_hex(32)
                encrypted=vault.encrypt(raw)
                sources[url.netloc]=source;atomic_json(self.sources_file,sources)
                atomic_json(self.file,dict(sourceId=source,host=url.netloc,encryptedUrl=encrypted))
            return self.call('getCanvasConfig')
        if method=='clearCanvasFeed':
            if args: raise ValueError('无效参数')
            with file_lock(self.file.with_suffix('.lock')):
                old=self.read()
                if old:
                    sources=self.sources();sources[old['host']]=old['sourceId'];atomic_json(self.sources_file,sources)
                self.file.unlink(missing_ok=True)
            return None
        if method!='syncCanvas' or args: raise ValueError('无效Canvas接口')
        if not old: raise ValueError('请先保存Canvas日历订阅链接')
        url=vault.decrypt(old['encryptedUrl'])
        if urlsplit(url).netloc!=old['host']: raise ValueError('Canvas链接无法解密，请重新保存')
        raw=self.service.request(url,None,{'Accept':'text/calendar'},5*1024*1024,20)
        parsed=parse_feed(raw.decode('utf-8-sig'),self.service.clock())
        with file_lock(self.file.with_suffix('.lock')):
            if self.read()!=old: raise ValueError('同步期间订阅配置已变化，请重新同步')
            today=self.service.clock().astimezone().date().isoformat()
            upcoming=[v for v in parsed['items'] if deadline_day(v['due']['value'])>=today]
            repo=self.service.repo
            def action(tree):
                report=dict(added=0,updated=0,unchanged=0,skipped=0,changes=[])
                for value in upcoming:
                    source_value(dict(sourceId=old['sourceId'],key=value['key'],todoId='00000000-0000-4000-8000-000000000001',description=value['description'],url=value['url']))
                    row=repo.db.execute('SELECT * FROM canvas_items WHERE source_id=? AND item_key=?',(old['sourceId'],value['key'])).fetchone()
                    if row is None:
                        node=tree.create(dict(title=value['title'],notes='',due=value['due']))
                        repo.db.execute('INSERT INTO canvas_items VALUES (?,?,?,?,?)',(old['sourceId'],value['key'],node['id'],value['description'],value['url']))
                        report['added']+=1;report['changes'].append(dict(id=node['id'],title=node['title'],kind='added',due=node['due']));continue
                    previous=tree.get(row['todo_id'])
                    if previous['deletedAt']: report['skipped']+=1;continue
                    changed_description=row['description']!=value['description'];changed_url=row['url']!=value['url']
                    changed=previous['title']!=value['title'] or previous['due']!=value['due']
                    if not changed and not changed_description and not changed_url: report['unchanged']+=1;continue
                    node=tree.update(previous['id'],dict(title=value['title'],due=value['due']),previous['revision']) if changed else previous
                    repo.db.execute('UPDATE canvas_items SET description=?,url=? WHERE source_id=? AND item_key=?',(value['description'],value['url'],old['sourceId'],value['key']))
                    report['updated']+=1;report['changes'].append(dict(id=node['id'],title=node['title'],kind='updated',previousTitle=previous['title'],previousDue=previous['due'],due=node['due'],descriptionChanged=changed_description,urlChanged=changed_url))
                return report
            report=repo._write(action)['value'];report['expired']=len(parsed['items'])-len(upcoming);report['issues']=parsed['issues'];report['skipped']+=report['expired']+len(parsed['issues']);return report
