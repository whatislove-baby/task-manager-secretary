"""Reviewable changes to existing tasks; approval and task write are atomic."""
from copy import deepcopy
import json
from uuid import uuid4

from .archive import text
from .mail_store import digest
from .tree import identifier, instant, input_value, strict


class TaskChangeService:
    METHODS = {'task-changes:'+name for name in ('list','get','approve','reject')}

    def __init__(self, service):
        self.service=service
        # Keep the receipt in the same SQLite transaction as the task mutation.
        self.db=service.repo.db
        self.db.execute('ATTACH DATABASE ? AS task_changes', (str(service.directory/'task-changes.sqlite'),))
        self.db.execute('CREATE TABLE IF NOT EXISTS task_changes.proposals(id TEXT PRIMARY KEY,payload TEXT NOT NULL)')

    def close(self):
        self.db.execute('DETACH DATABASE task_changes')

    def get(self, id):
        identifier(id)
        row=self.db.execute('SELECT payload FROM task_changes.proposals WHERE id=?',(id,)).fetchone()
        if not row:raise ValueError('找不到此待办变更建议')
        result=json.loads(row[0])
        if result['status']=='pending':
            current=self.db.execute('SELECT revision,completed,deleted_at FROM todos WHERE id=?',(result['taskId'],)).fetchone()
            if not current or current[0]!=result['expectedRevision'] or current[1] or current[2]:
                result['status']='stale'
        return result

    def list(self):
        return [self.get(r[0]) for r in self.db.execute('SELECT id FROM task_changes.proposals ORDER BY rowid DESC LIMIT 200')]

    def _save(self, value):
        self.db.execute('INSERT INTO task_changes.proposals VALUES(?,?) ON CONFLICT(id) DO UPDATE SET payload=excluded.payload',
                        (value['id'],json.dumps(value,ensure_ascii=False)))

    def propose(self, value, artifact_id=None):
        strict(value,('action','taskId','expectedRevision','patch','reason','evidence'),('action','taskId','expectedRevision','reason'))
        request_hash=digest(value)
        id=identifier(artifact_id) if artifact_id else str(uuid4())
        existing=self.db.execute('SELECT payload FROM task_changes.proposals WHERE id=?',(id,)).fetchone()
        if existing:
            saved=json.loads(existing[0])
            if saved.get('requestHash')!=request_hash:raise ValueError('该变更标识已用于其他内容')
            return saved
        action=value['action']
        if action not in ('update','cancel'):raise ValueError('只支持更新或取消已有待办')
        task=self.service.repo._tree().get(identifier(value['taskId']))
        if task['deletedAt'] or task['completed']:raise ValueError('此待办已完成或已取消，请先核对当前状态')
        revision=value['expectedRevision']
        if type(revision) is not int or revision!=task['revision']:raise ValueError('待办版本已变化，请重新读取后提出变更')
        patch=deepcopy(value.get('patch',{}))
        if action=='update':
            patch=input_value(patch,True)
            if not patch:raise ValueError('没有需要变更的内容')
        elif patch:raise ValueError('取消建议不能同时修改其他字段')
        if action=='cancel' and any(t['parentId']==task['id'] for t in self.service.repo.call('listTodos')):
            raise ValueError('此待办包含子事项，请在日历中核对整个分支后操作')
        reason=text(value['reason'],2000,True).strip()
        evidence=value.get('evidence',[])
        if not isinstance(evidence,list) or len(evidence)>10:raise ValueError('变更依据过多或格式无效')
        verified=[]
        for item in evidence:
            strict(item,('mailId','quote'),('mailId','quote'))
            mail=self.service.mail.store.get_local(item['mailId'])
            quote=text(item['quote'],2000,True)
            if not quote.strip() or quote not in (mail.get('body') or ''):raise ValueError('变更依据必须是来源邮件中的逐字原文')
            verified.append(dict(mailId=mail['localId'],quote=quote,subject=mail.get('subject',''),bindingId=mail['bindingId']))
        proposal=dict(action=action,taskId=task['id'],expectedRevision=revision,patch=patch,reason=reason,evidence=verified)
        fingerprint=digest(proposal)
        result=dict(id=id,kind='task_change',title=('取消：' if action=='cancel' else '更新：')+task['title'],
                    **proposal,hash=fingerprint,requestHash=request_hash,before=task,status='pending',createdAt=instant())
        self._save(result)
        return result

    def approve(self, value):
        strict(value,('id','expectedHash'),('id','expectedHash'))
        def apply(tree):
            proposal=self.get(value['id'])
            if proposal['hash']!=value['expectedHash']:raise ValueError('变更内容已变化，请重新打开预览')
            if proposal['status']=='approved':return proposal
            if proposal['status']=='stale':raise ValueError('待办在预览后发生变化，未覆盖；请重新读取并提出建议')
            if proposal['status']!='pending':raise ValueError('该变更建议已处理')
            current=tree.get(proposal['taskId'])
            if current['revision']!=proposal['expectedRevision'] or current['deletedAt'] or current['completed']:
                raise ValueError('待办在预览后发生变化，未覆盖；请重新读取并提出建议')
            if proposal['action']=='cancel':
                if any(t['parentId']==current['id'] for t in tree.list()):raise ValueError('待办新增了子事项，请先人工核对')
                batch=tree.trash(current['id'],current['revision'])
                proposal['trashBatchId']=batch
            else:
                tree.update(current['id'],proposal['patch'],current['revision'])
            proposal.update(status='approved',approvedAt=instant(),after=tree.get(current['id']))
            self._save(proposal)
            return proposal
        return self.service.repo._write(apply)['value']

    def reject(self, value):
        strict(value,('id','expectedHash'),('id','expectedHash'))
        def reject_in_transaction(tree):
            proposal=self.get(value['id'])
            if proposal['hash']!=value['expectedHash']:raise ValueError('变更内容已变化，请重新打开预览')
            if proposal['status']=='approved':raise ValueError('该建议已确认，请针对当前待办提出新变更')
            proposal.update(status='rejected',rejectedAt=instant())
            self._save(proposal)
            return proposal
        return self.service.repo._write(reject_in_transaction)['value']

    def call(self, name, *args):
        if name=='list' and not args:return self.list()
        if name in ('get','approve','reject') and len(args)==1:return getattr(self,name)(args[0])
        raise ValueError('无效待办变更接口')
