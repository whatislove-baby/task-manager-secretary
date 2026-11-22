"""Local-mail evidence guard for the Deep Agents business tools.

The framework still owns planning, memory and tool dispatch. This adapter only
records what the model actually read, and refuses mail-related writes while
that bounded customer/account context has unseen changes. It neither imports
mail nor decides which conflicting fact is correct.
"""
from datetime import datetime
import json
import re
from uuid import NAMESPACE_URL, uuid5

from .agent_tools import AgentTools, LABELS
from .draft_lifecycle import DraftLifecycle
from .mail_identity import addresses
from .mail_store import compact, digest


PROTECTED_WRITES = frozenset(('create_mail_draft', 'replace_mail_draft',
    'create_task_draft', 'replace_task_draft', 'propose_task_change'))


def mail_fingerprint(mail):
    """Exclude UI read/analyzed flags; include every content/identity input."""
    return digest({key:mail.get(key) for key in ('bindingId','mailboxIdentity',
        'fromAddress','replyToAddresses','toAddresses','ccAddresses','messageId',
        'inReplyTo','references','subject','sender','receivedAt','body','preview',
        'bodyStatus','attachments')})


def _scope(mail):
    sender=mail.get('fromAddress')
    return compact([mail['bindingId'],mail.get('mailboxIdentity',''),sender]) if sender else None


def _time(value):
    try:return datetime.fromisoformat(value.replace('Z','+00:00')).timestamp()
    except (ValueError,TypeError,AttributeError):return None


def _ranges(values):
    merged=[]
    for start,end in sorted(values):
        if merged and start<=merged[-1][1]:merged[-1][1]=max(end,merged[-1][1])
        else:merged.append([start,end])
    return merged


class EvidenceAgentTools(AgentTools):
    def __init__(self,service,execution_id,conversation_id=None,on_event=None,selected_mail_ids=None):
        super().__init__(service,execution_id,conversation_id,on_event)
        self.evidence_conversation=('conversation:'+conversation_id if conversation_id else 'execution:'+execution_id)
        self.selected_mail_ids=list(dict.fromkeys(selected_mail_ids or []))
        self.db.executescript('''
          CREATE TABLE IF NOT EXISTS agent_mail_read_evidence(
            conversation_id TEXT NOT NULL,mail_id TEXT NOT NULL,fingerprint TEXT NOT NULL,
            scope TEXT,covered TEXT NOT NULL,complete INTEGER NOT NULL,
            PRIMARY KEY(conversation_id,mail_id));
          CREATE TABLE IF NOT EXISTS agent_mail_evidence_scopes(
            conversation_id TEXT NOT NULL,scope TEXT NOT NULL,payload TEXT NOT NULL,
            PRIMARY KEY(conversation_id,scope));
        ''')
        # Explicitly selected mail defines context, but never counts as read.
        for id in self.selected_mail_ids:
            try:self._remember_scope(self.service.mail.store.get_local(id))
            except ValueError:pass

    def _scope_mails(self,scope):
        binding,identity,sender=json.loads(scope)
        store=self.service.mail.store
        rows=store.db.execute('''SELECT local_id FROM local_mail WHERE binding_id=?
            AND identity=? AND mail_sender_address(payload)=?''',(binding,identity,sender)).fetchall()
        return [store.get_local(row[0]) for row in rows]

    def _remember_scope(self,mail):
        scope=_scope(mail)
        if not scope:return
        row=self.db.execute('SELECT payload FROM agent_mail_evidence_scopes WHERE conversation_id=? AND scope=?',
                            (self.evidence_conversation,scope)).fetchone()
        anchor=_time(mail.get('receivedAt'))
        if row:
            value=json.loads(row[0]);old=value.get('anchor')
            if anchor is not None and (old is None or anchor<old):
                value['anchor']=anchor
                self.db.execute('UPDATE agent_mail_evidence_scopes SET payload=? WHERE conversation_id=? AND scope=?',
                                (compact(value),self.evidence_conversation,scope))
        else:
            value=dict(anchor=anchor,initial={m['localId']:mail_fingerprint(m) for m in self._scope_mails(scope)})
            self.db.execute('INSERT INTO agent_mail_evidence_scopes VALUES(?,?,?)',
                            (self.evidence_conversation,scope,compact(value)))

    def read_mail(self,message_id,offset=0,limit=10000):
        # Capture before/after so a concurrently refreshed body cannot be credited
        # as read when the tool returned the previous body.
        before=self.service.mail.store.get_local(message_id)
        result=super().read_mail(message_id,offset,limit)
        mail=self.service.mail.store.get_local(message_id)
        fingerprint=mail_fingerprint(mail)
        if fingerprint!=mail_fingerprint(before):
            result.update(evidenceComplete=False,evidenceWarning='阅读期间邮件更新，请从 offset=0 重新读取。')
            return result
        self._remember_scope(mail)
        row=self.db.execute('SELECT * FROM agent_mail_read_evidence WHERE conversation_id=? AND mail_id=?',
                            (self.evidence_conversation,message_id)).fetchone()
        covered=json.loads(row['covered']) if row and row['fingerprint']==fingerprint else []
        covered=_ranges([*covered,[result['offset'],result['endOffset']]])
        complete=(result['bodyStatus']=='complete' and len(covered)==1
                  and covered[0]==[0,result['totalCharacters']])
        self.db.execute('INSERT INTO agent_mail_read_evidence VALUES(?,?,?,?,?,?) ON CONFLICT(conversation_id,mail_id) DO UPDATE SET fingerprint=excluded.fingerprint,scope=excluded.scope,covered=excluded.covered,complete=excluded.complete',
            (self.evidence_conversation,message_id,fingerprint,_scope(mail),compact(covered),int(complete)))
        result.update(evidenceComplete=complete)
        return result

    def _is_read(self,mail):
        row=self.db.execute('SELECT fingerprint,complete FROM agent_mail_read_evidence WHERE conversation_id=? AND mail_id=?',
                            (self.evidence_conversation,mail['localId'])).fetchone()
        return bool(row and row['complete'] and row['fingerprint']==mail_fingerprint(mail))

    def _write_sources(self,name,args):
        """Only bound sources activate a scope; search hits never participate."""
        ids=list(args.get('source_mail_ids') or [])
        if args.get('reply_to_local_id'):ids.append(args['reply_to_local_id'])
        ids.extend(v['mailId'] for v in args.get('evidence',[]) if isinstance(v,dict) and v.get('mailId'))
        if name.startswith('replace_'):
            old=self.read_draft(args['draft_id'])
            if old.get('replyToLocalId'):ids.append(old['replyToLocalId'])
            ids.extend(old.get('analysis',{}).get('messageIds',[]))
        if name=='propose_task_change':
            # Mail-created formal todos keep immutable origin links in notes.
            task=next((v for v in self.service.repo.call('listTodos') if v['id']==args.get('task_id')),None)
            if task:ids.extend(re.findall(r'本地邮件：([^\s]+)',task.get('notes','')))
        if not ids:ids.extend(self.selected_mail_ids)
        mails=[self.service.mail.store.get_local(id) for id in dict.fromkeys(ids)]
        scopes={s for m in mails if (s:=_scope(m))}
        if not scopes and name=='create_mail_draft' and args.get('to'):
            # An outgoing draft may cite no reply ID, but a recipient can match
            # a customer the model actually read in this conversation.
            recipients={v['address'] for v in addresses([args['to']])}
            for row in self.db.execute('SELECT DISTINCT scope FROM agent_mail_read_evidence WHERE conversation_id=? AND scope IS NOT NULL',(self.evidence_conversation,)):
                binding,identity,sender=json.loads(row[0])
                if sender in recipients and (not args.get('binding_id') or binding==args['binding_id']):scopes.add(row[0])
        return mails,scopes

    def check_mail_evidence(self,name,args):
        """Return a read request, never an inferred replacement business fact."""
        required,scopes=self._write_sources(name,args)
        unread={m['localId']:(m,'source_not_fully_read') for m in required if not self._is_read(m)}
        for scope in scopes:
            row=self.db.execute('SELECT payload FROM agent_mail_evidence_scopes WHERE conversation_id=? AND scope=?',
                                (self.evidence_conversation,scope)).fetchone()
            if not row:continue  # explicit unread source already blocks write
            state=json.loads(row[0]);initial=state['initial'];anchor=state.get('anchor')
            previously_read={r[0] for r in self.db.execute('SELECT mail_id FROM agent_mail_read_evidence WHERE conversation_id=? AND scope=?',
                                                         (self.evidence_conversation,scope))}
            for mail in self._scope_mails(scope):
                id=mail['localId']
                if self._is_read(mail):continue
                timestamp=_time(mail.get('receivedAt'))
                if id in previously_read:reason='read_source_changed_or_partial'
                elif id not in initial:reason='newly_imported_mail'
                elif initial[id]!=mail_fingerprint(mail):reason='mail_changed'
                elif anchor is None or timestamp is None or timestamp>=anchor:reason='newer_unread_mail'
                else:continue
                unread[id]=(mail,reason)
        if not unread:return None
        messages=[dict(id=id,subject=m.get('subject',''),bindingId=m['bindingId'],fromAddress=m.get('fromAddress',''),
                       receivedAt=m.get('receivedAt',''),bodyStatus=m.get('bodyStatus','pending'),reason=reason)
                  for id,(m,reason) in sorted(unread.items(),key=lambda item:(item[1][0].get('receivedAt',''),item[0]))]
        return dict(error='context_stale',message='相关邮件尚未完整阅读或有新内容。请 read_mail 并继续分页至 evidenceComplete=true，再核对冲突后重试；不要直接用最新邮件覆盖旧事实。',
                    requiredMailIds=[m['id'] for m in messages],messages=messages,
                    scope='仅检查已导入本地的同邮箱账号、同完整发件地址；未自动连接服务器。')

    def _has_durable_result(self,name,call_id):
        """Recovery of an already committed local write must not execute anew."""
        stable=str(uuid5(NAMESPACE_URL,'task-manager-agent:'+self.execution_id+':'+call_id))
        row=self.db.execute('SELECT operation,result FROM agent_tool_results WHERE id=?',(stable,)).fetchone()
        if not row or row['operation']!=name:return False
        if row['result'] is not None:return True
        if name=='propose_task_change':
            try:self.service.task_changes.get(stable);return True
            except ValueError:return False
        with DraftLifecycle(self.service.directory) as life:return life.exists(stable)

    def execute(self,name,arguments,call_id):
        if name in PROTECTED_WRITES and not self._has_durable_result(name,call_id):
            try:failure=self.check_mail_evidence(name,arguments)
            except (ValueError,KeyError,TypeError) as exc:
                failure=dict(error='context_stale',message='无法核对邮件来源，请重新读取当前来源或明确改为独立事项。',requiredMailIds=[])
            if failure:
                self.on_event(dict(type='tool',tool=name,status='failed',label=LABELS[name],callId=call_id,detail=failure['message']))
                return failure
        return super().execute(name,arguments,call_id)
