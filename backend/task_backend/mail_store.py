"""Compatible offline mail storage, receipts, analysis and local organization."""
from contextlib import contextmanager
from copy import deepcopy
import hashlib,json
from pathlib import Path
import sqlite3
from uuid import uuid4
from .archive import text
from .tree import instant,identifier,strict
from .mail_types import DEFAULT_FILTER,filter_value
from .mail_identity import ID_FIELDS,normalize_mail,addresses


def compact(value): return json.dumps(value,ensure_ascii=False,separators=(',',':'))
def digest(value): return hashlib.sha256(compact(value).encode('utf-8')).hexdigest()
def local_key(binding,identity,remote): return digest([binding,identity,remote])
def fingerprint(value): return digest([value.get(k,'') for k in ('subject','sender','receivedAt','body')])


class MailStore:
    def __init__(self,directory):
        self.file=Path(directory)/'mail.sqlite';self.db=sqlite3.connect(self.file,isolation_level=None,timeout=5);self.db.row_factory=sqlite3.Row
        # Legacy cached records gain safe exact address matching without a
        # destructive migration or reliance on display-name substrings.
        self.db.create_function('mail_sender_address',1,lambda raw:normalize_mail(json.loads(raw))['fromAddress'],deterministic=True)
        try:
            self.db.executescript('''PRAGMA foreign_keys=ON;PRAGMA secure_delete=ON;
CREATE TABLE IF NOT EXISTS local_mail(local_id TEXT PRIMARY KEY,binding_id TEXT NOT NULL,identity TEXT NOT NULL,remote_id TEXT NOT NULL,payload TEXT NOT NULL,fingerprint TEXT NOT NULL,analysis_id TEXT,analyzed_at TEXT,saved_at TEXT,UNIQUE(binding_id,identity,remote_id));
CREATE TABLE IF NOT EXISTS mail_analyses(id TEXT PRIMARY KEY,payload TEXT NOT NULL,saved INTEGER NOT NULL DEFAULT 0);
CREATE INDEX IF NOT EXISTS local_mail_binding ON local_mail(binding_id);''')
            with self.transaction():
                if 'folder_id' not in [r['name'] for r in self.db.execute('PRAGMA table_info(local_mail)')]: self.db.execute('ALTER TABLE local_mail ADD COLUMN folder_id TEXT')
                for sql in [
                    'CREATE TABLE IF NOT EXISTS mail_receipts(local_id TEXT PRIMARY KEY,binding_id TEXT NOT NULL,identity TEXT NOT NULL,remote_id TEXT NOT NULL,imported_at TEXT NOT NULL,analysis_id TEXT,analyzed_at TEXT,deleted INTEGER NOT NULL DEFAULT 0)',
                    'CREATE TABLE IF NOT EXISTS mail_known_senders(binding_id TEXT NOT NULL,sender TEXT NOT NULL,PRIMARY KEY(binding_id,sender))',
                    'CREATE TABLE IF NOT EXISTS mail_presets(id TEXT PRIMARY KEY,binding_id TEXT NOT NULL,name TEXT NOT NULL,filter TEXT NOT NULL,UNIQUE(binding_id,name))',
                    'CREATE TABLE IF NOT EXISTS mail_preset_meta(binding_id TEXT PRIMARY KEY,seeded_at TEXT NOT NULL)',
                    'CREATE TABLE IF NOT EXISTS mail_local_folders(id TEXT PRIMARY KEY,name TEXT NOT NULL UNIQUE)',
                    "INSERT OR IGNORE INTO mail_receipts SELECT local_id,binding_id,identity,remote_id,json_extract(payload,'$.cachedAt'),analysis_id,analyzed_at,0 FROM local_mail",
                    "INSERT OR IGNORE INTO mail_known_senders SELECT binding_id,json_extract(payload,'$.sender') FROM local_mail WHERE json_extract(payload,'$.sender')<>''"]: self.db.execute(sql)
        except BaseException: self.db.close();raise

    def close(self): self.db.close()

    @contextmanager
    def transaction(self):
        self.db.execute('BEGIN IMMEDIATE')
        try: yield;self.db.execute('COMMIT')
        except BaseException: self.db.execute('ROLLBACK');raise

    def _check_size(self,payload):
        if self.file.stat().st_size>512*1024*1024: raise ValueError('本地邮件库超过512MB，本次未保存更多邮件')
        if len(compact(payload).encode('utf-8'))>16*1024*1024: raise ValueError('邮件正文超过本地保存上限')

    def has(self,binding,identity,remote): return bool(self.db.execute('SELECT 1 FROM mail_receipts WHERE local_id=?',(local_key(binding,identity,remote),)).fetchone())
    def is_deleted(self,binding,identity,remote): return self._deleted(local_key(binding,identity,remote))
    def _deleted(self,id): return bool(self.db.execute('SELECT 1 FROM mail_receipts WHERE local_id=? AND deleted=1',(id,)).fetchone())

    def body_state(self,binding,identity,remote):
        id=local_key(binding,identity,remote)
        if self._deleted(id): return 'deleted'
        try: return self.get_local(id)['bodyStatus']
        except ValueError: return 'missing'

    def import_exclusions(self,binding,identity,only_new=True):
        """Remote IDs safe to skip before the reader's per-batch candidate cap."""
        excluded={row[0] for row in self.db.execute(
            'SELECT remote_id FROM mail_receipts WHERE binding_id=? AND identity=? AND deleted=1',
            (binding,identity))}
        if only_new:
            # Match _decode's legacy status fallback without loading full bodies.
            rows=self.db.execute('''SELECT remote_id FROM local_mail WHERE binding_id=? AND identity=?
                AND COALESCE(json_extract(payload,'$.bodyStatus'),
                    CASE WHEN length(json_extract(payload,'$.body'))>0 THEN 'complete' ELSE 'pending' END)
                    IN ('complete','unavailable')''',(binding,identity))
            excluded.update(row[0] for row in rows)
        return excluded

    def put_summary(self,binding,identity,email,item,extra=None):
        item=normalize_mail(item);extra=extra or {};id=local_key(binding,identity,item['id'])
        if self._deleted(id): return dict(created=False,deleted=True,localId=id)
        row=self.db.execute('SELECT payload FROM local_mail WHERE local_id=?',(id,)).fetchone()
        if row:
            previous=json.loads(row['payload']);payload={**previous,**item}
            # ENVELOPE/Graph list results do not contain all thread headers.
            # Refreshing the inbox must not erase a complete header snapshot.
            if previous.get('identitySource') in ('rfc-headers','graph-headers') and item.get('identitySource') not in ('rfc-headers','graph-headers'):
                if not item.get('messageId') or item['messageId']==previous.get('messageId'):
                    payload.update({k:previous[k] for k in ID_FIELDS if k in previous})
            payload.update(preview=item.get('preview') or extra.get('preview') or previous.get('preview',''),attachments=extra.get('attachments',previous.get('attachments',[])),hasHtml=extra.get('hasHtml',previous.get('hasHtml',False)),bodyStatus=previous.get('bodyStatus','complete' if previous.get('body') else 'pending'))
            self._check_size(payload);self.db.execute('UPDATE local_mail SET payload=? WHERE local_id=?',(compact(payload),id))
            return dict(created=False,deleted=False,localId=id)
        payload={**item,**extra,'preview':item.get('preview') or extra.get('preview',''),'body':'','bodyStatus':'pending','bodyBytes':0,'attachments':extra.get('attachments',[]),'hasHtml':extra.get('hasHtml',False),'localId':id,'bindingId':binding,'mailboxIdentity':identity,'accountEmail':email,'cachedAt':instant(),'analyzedAt':None,'analysisId':None,'status':'unanalyzed'}
        self._check_size(payload)
        with self.transaction():
            self.db.execute('INSERT INTO local_mail(local_id,binding_id,identity,remote_id,payload,fingerprint) VALUES(?,?,?,?,?,?)',(id,binding,identity,item['id'],compact(payload),fingerprint(payload)))
            self.db.execute('INSERT OR IGNORE INTO mail_receipts(local_id,binding_id,identity,remote_id,imported_at) VALUES(?,?,?,?,?)',(id,binding,identity,item['id'],payload['cachedAt']))
            if item.get('sender'): self.db.execute('INSERT OR IGNORE INTO mail_known_senders VALUES(?,?)',(binding,item['sender']))
        return dict(created=True,deleted=False,localId=id)

    def put(self,binding,identity,email,item):
        item=normalize_mail(item);id=local_key(binding,identity,item['id'])
        if self._deleted(id): return dict(added=False,localId=id)
        old=self.db.execute('SELECT fingerprint FROM local_mail WHERE local_id=?',(id,)).fetchone()
        payload={**item,'bodyStatus':item.get('bodyStatus','complete'),'attachments':item.get('attachments',[]),'localId':id,'bindingId':binding,'mailboxIdentity':identity,'accountEmail':email,'cachedAt':instant(),'analyzedAt':None,'analysisId':None,'status':'unanalyzed'}
        self._check_size(payload);fp=fingerprint(payload)
        with self.transaction():
            self.db.execute('''INSERT INTO local_mail(local_id,binding_id,identity,remote_id,payload,fingerprint) VALUES(?,?,?,?,?,?) ON CONFLICT(local_id) DO UPDATE SET payload=excluded.payload,fingerprint=excluded.fingerprint,analysis_id=CASE WHEN fingerprint=excluded.fingerprint THEN analysis_id ELSE NULL END,analyzed_at=CASE WHEN fingerprint=excluded.fingerprint THEN analyzed_at ELSE NULL END''',(id,binding,identity,item['id'],compact(payload),fp))
            self._receipt(id)
            if item.get('sender'): self.db.execute('INSERT OR IGNORE INTO mail_known_senders VALUES(?,?)',(binding,item['sender']))
        return dict(added=not bool(old),localId=id)

    def _receipt(self,id):
        self.db.execute("INSERT INTO mail_receipts(local_id,binding_id,identity,remote_id,imported_at,analysis_id,analyzed_at) SELECT local_id,binding_id,identity,remote_id,json_extract(payload,'$.cachedAt'),analysis_id,analyzed_at FROM local_mail WHERE local_id=? ON CONFLICT(local_id) DO UPDATE SET analysis_id=excluded.analysis_id,analyzed_at=excluded.analyzed_at",(id,))

    def put_body(self,id,item):
        previous=self.get_local(id)
        if self._deleted(id): return dict(updated=False,added=False,localId=id)
        payload={**previous,**item,**{k:previous[k] for k in ('localId','bindingId','mailboxIdentity','accountEmail','cachedAt')},'bodyStatus':item.get('bodyStatus','complete'),'attachments':item.get('attachments',previous.get('attachments',[]))}
        if 'sender' in item or 'identitySource' in item:
            payload.update({k:v for k,v in normalize_mail(item).items() if k in ID_FIELDS})
        self._check_size(payload);fp=fingerprint(payload);old=fingerprint(previous)
        with self.transaction():
            self.db.execute('UPDATE local_mail SET payload=?,fingerprint=?,analysis_id=CASE WHEN fingerprint=? THEN analysis_id ELSE NULL END,analyzed_at=CASE WHEN fingerprint=? THEN analyzed_at ELSE NULL END WHERE local_id=?',(compact(payload),fp,fp,fp,id));self._receipt(id)
            if item.get('sender'): self.db.execute('INSERT OR IGNORE INTO mail_known_senders VALUES(?,?)',(previous['bindingId'],item['sender']))
        return dict(updated=old!=fp,added=False,localId=id)

    def _decode(self,row):
        value=normalize_mail(json.loads(row['payload']));value.update(analysisId=row['analysis_id'],analyzedAt=row['analyzed_at'],folderId=row['folder_id'],status='analyzed' if row['analyzed_at'] else 'unanalyzed')
        value.setdefault('bodyStatus','complete' if value.get('body') else 'pending');value.setdefault('attachments',[]);return value

    def get_local(self,id):
        text(id,2048,True);row=self.db.execute('SELECT * FROM local_mail WHERE local_id=?',(id,)).fetchone()
        if not row: raise ValueError('本地未保存此邮件，请先导入')
        return self._decode(row)

    def list_local(self,query):
        strict(query,('bindingIds','folderId','status','search','offset','limit'))
        clauses=[];args=[]
        if 'bindingIds' in query:
            ids=query['bindingIds']
            if not isinstance(ids,list) or len(ids)>100: raise ValueError('无效邮箱选择')
            if not ids: return dict(messages=[],total=0)
            for id in ids: text(id,2048,True)
            clauses.append('binding_id IN ('+','.join('?' for _ in ids)+')');args.extend(ids)
        if 'status' in query:
            if query['status'] not in ('analyzed','unanalyzed'): raise ValueError('无效分析状态')
            clauses.append('analyzed_at IS NOT NULL' if query['status']=='analyzed' else 'analyzed_at IS NULL')
        if 'folderId' in query:
            if query['folderId'] is None: clauses.append('folder_id IS NULL')
            else: identifier(query['folderId']);clauses.append('folder_id=?');args.append(query['folderId'])
        if query.get('search','').strip():
            search=text(query['search'],1000).strip();clauses.append("(instr(lower(json_extract(payload,'$.subject')),lower(?))>0 OR instr(lower(json_extract(payload,'$.sender')),lower(?))>0)");args.extend([search,search])
        limit=query.get('limit',200);offset=query.get('offset',0)
        if type(limit) is not int or not 1<=limit<=1000 or type(offset) is not int or offset<0: raise ValueError('无效分页参数')
        sql=' FROM local_mail'+(' WHERE '+' AND '.join(clauses) if clauses else '')
        total=self.db.execute('SELECT count(*)'+sql,args).fetchone()[0]
        rows=self.db.execute("SELECT *"+sql+" ORDER BY json_extract(payload,'$.receivedAt') DESC,local_id LIMIT ? OFFSET ?",(*args,limit,offset))
        return dict(messages=[{**self._decode(r),'body':''} for r in rows],total=total)

    def search_agent_mail(self,query='',sender='',binding_id='',offset=0,limit=20):
        """Return account-aware sources; address sender filters are exact.

        Free text stays a fuzzy AND search over subject/sender/body/preview.
        Callers should bound the excerpts they expose to an LLM.
        """
        query=text(query,1000).strip();sender=text(sender,320).strip();binding_id=text(binding_id,2048).strip()
        if type(limit) is not int or not 1<=limit<=100 or type(offset) is not int or not 0<=offset<=100000:
            raise ValueError('无效邮件搜索分页')
        clauses=[];args=[]
        if binding_id:clauses.append('binding_id=?');args.append(binding_id)
        if sender:
            if '@' in sender:
                parsed=addresses([sender])
                if len(parsed)!=1:raise ValueError('发件人邮箱筛选需要单个有效地址')
                clauses.append('mail_sender_address(payload)=?');args.append(parsed[0]['address'])
            else:
                clauses.append("instr(lower(json_extract(payload,'$.sender')),lower(?))>0");args.append(sender)
        for term in query.split():
            clauses.append('('+' OR '.join("instr(lower(COALESCE(json_extract(payload,'$."+key+"'),'')),lower(?))>0" for key in ('subject','sender','body','preview'))+')')
            args.extend([term]*4)
        sql=' FROM local_mail'+(' WHERE '+' AND '.join(clauses) if clauses else '')
        total=self.db.execute('SELECT count(*)'+sql,args).fetchone()[0]
        rows=self.db.execute('SELECT *'+sql+" ORDER BY json_extract(payload,'$.receivedAt') DESC,local_id LIMIT ? OFFSET ?",(*args,limit,offset)).fetchall()
        return dict(messages=[self._decode(row) for row in rows],total=total,nextOffset=offset+len(rows) if offset+len(rows)<total else None)

    def list_presets(self,binding):
        text(binding,2048,True)
        if not self.db.execute('SELECT 1 FROM mail_preset_meta WHERE binding_id=?',(binding,)).fetchone():
            with self.transaction():
                self.db.execute('INSERT OR IGNORE INTO mail_presets VALUES(?,?,?,?)',(str(uuid4()),binding,'默认预设',compact(DEFAULT_FILTER)))
                self.db.execute('INSERT OR IGNORE INTO mail_preset_meta VALUES(?,?)',(binding,instant()))
        return [dict(id=r['id'],bindingId=r['binding_id'],name=r['name'],filter=filter_value(json.loads(r['filter']))) for r in self.db.execute('SELECT * FROM mail_presets WHERE binding_id=? ORDER BY name',(binding,))]

    def save_preset(self,binding,value):
        text(binding,2048,True);strict(value,('id','name','filter'),('name','filter'));name=text(value['name'],100,True).strip();filter=filter_value(value['filter']);id=value.get('id',str(uuid4()))
        identifier(id)
        if not name: raise ValueError('预设名称不能为空')
        if 'id' in value and not self.db.execute('SELECT 1 FROM mail_presets WHERE id=? AND binding_id=?',(id,binding)).fetchone(): raise ValueError('找不到此邮箱预设')
        try: self.db.execute('INSERT INTO mail_presets VALUES(?,?,?,?) ON CONFLICT(id) DO UPDATE SET name=excluded.name,filter=excluded.filter',(id,binding,name,compact(filter)))
        except sqlite3.IntegrityError: raise ValueError('预设名称已存在或无法保存') from None
        return dict(id=id,bindingId=binding,name=name,filter=filter)

    def delete_preset(self,id): identifier(id);self.db.execute('DELETE FROM mail_presets WHERE id=?',(id,))
    def known_senders(self,binding): text(binding,2048,True);return [r[0] for r in self.db.execute('SELECT sender FROM mail_known_senders WHERE binding_id=? ORDER BY sender COLLATE NOCASE',(binding,))]
    def list_folders(self): return [dict(r) for r in self.db.execute('SELECT id,name FROM mail_local_folders ORDER BY name')]
    def create_folder(self,name):
        name=text(name,100,True).strip();id=str(uuid4())
        if not name: raise ValueError('文件夹名称不能为空')
        try: self.db.execute('INSERT INTO mail_local_folders VALUES(?,?)',(id,name))
        except sqlite3.IntegrityError: raise ValueError('文件夹名称已存在') from None
        return dict(id=id,name=name)
    def rename_folder(self,id,name):
        identifier(id);name=text(name,100,True).strip()
        if not name: raise ValueError('文件夹名称不能为空')
        try:
            if self.db.execute('UPDATE mail_local_folders SET name=? WHERE id=?',(name,id)).rowcount!=1: raise ValueError('找不到文件夹')
        except sqlite3.IntegrityError: raise ValueError('文件夹名称已存在') from None
        return dict(id=id,name=name)
    def delete_folder(self,id):
        identifier(id)
        with self.transaction(): self.db.execute('UPDATE local_mail SET folder_id=NULL WHERE folder_id=?',(id,));self.db.execute('DELETE FROM mail_local_folders WHERE id=?',(id,))
    def _ids(self,ids):
        if not isinstance(ids,list) or not 1<=len(ids)<=1000 or any(not isinstance(v,str) for v in ids) or len(ids)!=len(set(ids)): raise ValueError('无效邮件选择')
        for id in ids: text(id,2048,True)
    def move_local(self,ids,folder):
        self._ids(ids)
        if folder is not None:
            identifier(folder)
            if not self.db.execute('SELECT 1 FROM mail_local_folders WHERE id=?',(folder,)).fetchone(): raise ValueError('找不到本地文件夹')
        with self.transaction():
            for id in ids: self.get_local(id)
            for id in ids: self.db.execute('UPDATE local_mail SET folder_id=? WHERE local_id=?',(folder,id))
        return dict(count=len(ids))
    def delete_local(self,ids):
        self._ids(ids);count=0
        with self.transaction():
            for id in ids:
                self.db.execute('UPDATE mail_receipts SET deleted=1 WHERE local_id=?',(id,));count+=self.db.execute('DELETE FROM local_mail WHERE local_id=?',(id,)).rowcount
        return dict(count=count)
    def get_analysis(self,id):
        text(id,2048,True);row=self.db.execute('SELECT payload,saved FROM mail_analyses WHERE id=?',(id,)).fetchone()
        if not row: raise ValueError('找不到此分析记录')
        return {**json.loads(row['payload']),'saved':bool(row['saved'])}
    def list_analyses(self): return [{**json.loads(r['payload']),'saved':bool(r['saved'])} for r in self.db.execute('SELECT payload,saved FROM mail_analyses ORDER BY rowid DESC')]
    def save_analyses(self,analyses):
        if not isinstance(analyses,list) or len(analyses)>2000: raise ValueError('分析批次数量无效')
        with self.transaction():
            for value in analyses:
                text(value['id'],2048,True);ids=value['messageIds']
                if not isinstance(ids,list) or len(ids)>200 or len(ids)!=len(set(ids)): raise ValueError('分析邮件数量无效')
                old=self.db.execute('SELECT payload FROM mail_analyses WHERE id=?',(value['id'],)).fetchone()
                if old:
                    if json.loads(old['payload'])!=value: raise ValueError('分析标识已用于其他结果')
                    continue
                for id in ids: self.get_local(id)
                self.db.execute('INSERT INTO mail_analyses VALUES(?,?,?)',(value['id'],compact(value),int(value['saved'])))
                if value.get('complete') is not False: self._mark_analyzed(value)
    def _mark_analyzed(self,value):
        for id in value['messageIds']:
            self.db.execute('UPDATE local_mail SET analysis_id=?,analyzed_at=? WHERE local_id=?',(value['id'],value['createdAt'],id));self.db.execute('UPDATE mail_receipts SET analysis_id=?,analyzed_at=? WHERE local_id=?',(value['id'],value['createdAt'],id))
    def complete_analysis(self,id):
        value=self.get_analysis(id);value['complete']=True
        with self.transaction(): self.db.execute('UPDATE mail_analyses SET payload=? WHERE id=?',(compact(value),id));self._mark_analyzed(value)
    def mark_progress(self,id,added_ids,added_todos):
        value=self.get_analysis(id);value['addedTempIds']=list(dict.fromkeys(value.get('addedTempIds',[])+added_ids));value['addedTodos']={**value.get('addedTodos',{}),**added_todos};value['saved']=all(v['tempId'] in value['addedTempIds'] for v in value['drafts'])
        with self.transaction():
            self.db.execute('UPDATE mail_analyses SET payload=?,saved=? WHERE id=?',(compact(value),int(value['saved']),id))
            if value['saved']:
                for mail in value['messageIds']: self.db.execute('UPDATE local_mail SET saved_at=COALESCE(saved_at,?) WHERE local_id=?',(instant(),mail))
        return value
