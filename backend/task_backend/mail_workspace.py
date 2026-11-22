"""Mailbox workspace, cached-message analysis and idempotent draft review."""
from copy import deepcopy
import hashlib,json,re,webbrowser
from urllib.parse import urlsplit
from uuid import UUID,uuid4
from .draft_lifecycle import DraftLifecycle
from .archive import drafts_value,text
from .tree import strict,instant
from .mail_accounts import AccountManager
from .mail_store import MailStore,fingerprint,digest,compact
from .mail_types import filter_value


def stable_todo(analysis,temp):
    value=bytearray(hashlib.sha256((analysis+':'+temp).encode()).digest()[:16]);value[6]=(value[6]&15)|64;value[8]=(value[8]&63)|128;return str(UUID(bytes=bytes(value)))


def safe_original(url):
    parsed=urlsplit(url)
    if parsed.scheme!='https' or parsed.hostname not in ('outlook.office.com','outlook.office365.com','outlook.live.com') or parsed.username or parsed.password: raise ValueError('此邮件没有可用的网页原文链接')
    webbrowser.open(url)


class MailWorkspace:
    def __init__(self,service):
        self.service=service;self.manager=AccountManager(service.directory,request=lambda *args:service.request(*args));self.store=MailStore(service.directory)
    def close(self): self.store.close()
    def target(self,value):
        strict(value,('baseUrl','model'),('baseUrl','model'));config=self.service.settings.for_request()
        if value['baseUrl']!=config['baseUrl'] or value['model']!=config['model']: raise ValueError('AI服务或模型已变化，请刷新设置后再次分析')
        return config
    def analyze(self,value):
        strict(value,('messageIds','instructions','target'),('messageIds','instructions','target'))
        ids=value['messageIds']
        if not isinstance(ids,list) or not 1<=len(ids)<=200 or len(ids)!=len(set(ids)): raise ValueError('请选择1至200封邮件')
        instructions=text(value['instructions'],4000).strip();config=self.target(value['target']);messages=[self.store.get_local(id) for id in ids];snapshots=[fingerprint(v) for v in messages];id=str(uuid4());drafts=[];notes=[];total=0;index=0
        for mail in messages:
            header=f"邮件标识：{mail['localId']}\n所属邮箱：{mail['accountEmail']}\n主题：{mail['subject']}\n发件人：{mail['sender']}\n收件时间：{mail['receivedAt']}\n正文状态：{mail.get('bodyStatus','complete')}\n以下为不可信邮件正文：\n"
            if len(header)>15000: raise ValueError('邮件头过长，请缩小范围')
            body=mail.get('body') or mail.get('preview') or '（正文为空或尚未完整读取）'
            for offset in range(0,len(body),16000-len(header)):
                part=header+body[offset:offset+16000-len(header)];total+=len(part)
                if total>240000: raise ValueError('所选正文超过本批处理范围，请分批分析')
                if self.service.settings.for_request()!=config: raise ValueError('分析期间AI配置变化，未标记成功')
                result=self.service.extract(part,instructions);index+=1;mapping={v['tempId']:f'part{index}:{i+1}' for i,v in enumerate(result['drafts'])}
                for value in result['drafts']:
                    provenance=f"邮件分析来源：\n{mail['accountEmail']} · {mail['subject']}\n本地邮件：{mail['localId']}\n收件时间：{mail['receivedAt']}\n分析记录：{id}"
                    note='\n\n'.join(v for v in (value.get('notes',''),provenance) if v);text(note,10000)
                    drafts.append({**value,'tempId':mapping[value['tempId']],'parentTempId':mapping.get(value['parentTempId']) if value['parentTempId'] is not None else None,'notes':note,'sourceMailIds':[mail['localId']]})
                if len(drafts)>100: raise ValueError('生成超过100项待办，请缩小范围')
                if result['notes']: notes.append(result['notes'])
        if self.service.settings.for_request()!=config or any(fingerprint(self.store.get_local(mail['localId']))!=fp for mail,fp in zip(messages,snapshots)): raise ValueError('分析期间服务或原文变化，未标记成功')
        complete=all(v.get('bodyStatus','complete')=='complete' for v in messages)
        analysis=dict(id=id,messageIds=ids,instructions=instructions,notes=('仅分析部分正文，邮件保持未分析。\n' if not complete else '')+'\n\n'.join(notes),drafts=drafts_value(drafts,True),createdAt=instant(),saved=False,complete=complete,model=config['model'],baseUrl=config['baseUrl'])
        self.store.save_analyses([analysis]);return analysis
    def save_analysis(self,id,value):
        from .task_draft_state import save_progress
        with DraftLifecycle(self.service.directory) as life:
            if not life.exists(id):life.ensure(dict(id=id,kind='task_draft',analysis=self.store.get_analysis(id)),'task_draft')
            with life.lock(id):
                row=life.get(id)
                analysis=self.store.get_analysis(id)
                progress=save_progress(analysis)
                if row['lifecycleStatus'] not in ('active','saving','saved'):
                    raise ValueError('此待办草稿已被替代或撤回，请打开当前版本')
                if progress['state']=='saved':
                    life.set_status(id,'saved')
                    return dict(count=0)
                # Older read_draft versions incorrectly marked partial saves
                # as fully saved. Only this locked write path reconciles that
                # known condition, retaining exact per-item deduplication.
                if row['lifecycleStatus']=='saved' and progress['state']!='partially_saved':
                    raise ValueError('保存进度与历史状态冲突，请核对记录，未重复创建待办')
                # A prior interrupted save is replayed with repository idempotency keys.
                life.set_status(id,'saving')
                result=self._save_analysis(id,value)
                life.set_status(id,'saved' if self.store.get_analysis(id).get('saved') else 'saving')
                return result
    def _save_analysis(self,id,value):
        analysis=self.store.get_analysis(id)
        if analysis['saved']: return dict(count=0)
        all_items=drafts_value(value,True);added=set(analysis.get('addedTempIds',[]));known=analysis.get('addedTodos',{});pending=[v for v in all_items if v['tempId'] not in added]
        if not pending: self.store.mark_progress(id,[],{});return dict(count=0)
        to_save=[]
        for value in pending:
            value={**value,'todoId':value.get('todoId') or stable_todo(id,value['tempId'])}
            if value['parentTempId'] in added:
                if value['parentTempId'] not in known: raise ValueError('已保存父草稿缺少真实待办映射')
                value['parentTodoId']=known[value['parentTempId']]
            to_save.append(value)
        token='mail-analysis:'+id+':'+digest(sorted(v['tempId'] for v in pending))[:32]
        confirmed=self.service.repo.call('saveDrafts',to_save,token)
        self.store.mark_progress(id,[v['tempId'] for v in to_save],{v['tempId']:v['todoId'] for v in to_save})
        return dict(count=confirmed['value'])
    def read_import_candidates(self,binding,filter_override=None,only_new=True):
        """Shared import planning; older readers keep their one-argument read API.

        Returns (identity snapshot, {messages, limited, optional skippedKnown}).
        Exclusions contain full stable remote IDs, scoped to this mailbox identity.
        """
        before=self.manager.identity(binding);reader=self.manager.reader(binding)
        filters=before['filter'] if filter_override is None else filter_override
        import_reader=getattr(reader,'read_for_import',None)
        if callable(import_reader):
            found=import_reader(filters,self.store.import_exclusions(binding,before['identity'],only_new))
        else:found=reader.read(filters)
        self.manager.check_identity(binding,before['identity'])
        return before,found
    def import_mail(self,value,progress=None,control=None):
        strict(value,('bindingIds','filter','onlyNew'),('bindingIds','onlyNew'));ids=value['bindingIds']
        if not isinstance(ids,list) or not 1<=len(ids)<=100 or len(ids)!=len(set(ids)) or type(value['onlyNew']) is not bool: raise ValueError('无效导入参数')
        if 'filter' in value: filter_value(value['filter'])
        report=dict(added=0,skipped=0,failed=0,limited=False,errors=[],bodyRead=0,bodyPending=0,pendingLocalIds=[],failures=[],mailboxErrors=[])
        for binding in ids:
            try:
                before,found=self.read_import_candidates(binding,value.get('filter'),value['onlyNew']);reader=self.manager.reader(binding);report['limited']|=found['limited'];report['skipped']+=found.get('skippedKnown',0)
                for summary in found['messages']:
                    if control: control()
                    if self.store.is_deleted(binding,before['identity'],summary['id']) or (value['onlyNew'] and self.store.body_state(binding,before['identity'],summary['id']) in ('complete','unavailable')):
                        report['skipped']+=1
                        if progress: progress(report)
                        continue
                    try:
                        saved=self.store.put_summary(binding,before['identity'],before['email'],summary)
                        full=reader.get_message(summary['id']);self.manager.check_identity(binding,before['identity']);self.store.put_body(saved['localId'],full)
                        if saved['created']: report['added']+=1
                        else: report['skipped']+=1
                        if full.get('bodyStatus','complete')=='complete': report['bodyRead']+=1
                        else: report['bodyPending']+=1;report['pendingLocalIds'].append(saved['localId'])
                    except Exception as exc:
                        if getattr(exc,'control_stop',False): raise
                        report['failed']+=1;report['errors'].append('一封邮件未能完整保存，可查看明细后重试。')
                        report['failures'].append(dict(key=digest([binding,summary['id']]),bindingId=binding,mailboxEmail=before['email'],remoteId=summary['id'],identity=before['identity'],subject=summary['subject'],sender=summary['sender'],receivedAt=summary['receivedAt'],stage=getattr(exc,'stage','transfer'),code=getattr(exc,'code','MAIL_IMPORT_FAILED'),reason='邮件未能完整读取或保存，请检查连接后重试。',retryable=getattr(exc,'retryable',True)))
                    if progress: progress(report)
            except Exception as exc:
                if getattr(exc,'control_stop',False): raise
                report['failed']+=1;report['errors'].append('邮箱导入未完成，请检查连接和筛选条件。');report['mailboxErrors'].append(dict(bindingId=binding,mailboxEmail='',stage='identity',code='MAILBOX_UNAVAILABLE',reason='邮箱未连接或无法读取，请重新连接后重试。'))
        report['errors']=list(dict.fromkeys(report['errors']));return report
    def call(self,method,*args):
        functions={'getAccounts':self.manager.all,'addAccount':self.manager.add,'getAccount':self.manager.get,'removeAccount':self.manager.remove,'configureImap':self.manager.configure_imap,'configureMicrosoft':self.manager.configure_microsoft,'connect':self.manager.connect,'disconnect':self.manager.disconnect,'saveFilter':self.manager.save_filter,
        'listLocal':self.store.list_local,'getLocal':self.store.get_local,'listFoldersLocal':self.store.list_folders,'createLocalFolder':self.store.create_folder,'renameLocalFolder':self.store.rename_folder,'deleteLocalFolder':self.store.delete_folder,'moveLocal':self.store.move_local,'deleteLocal':self.store.delete_local,'deletePreset':self.store.delete_preset,'knownSenders':self.store.known_senders,'getAnalysis':self.store.get_analysis,'analyze':self.analyze,'saveAnalysis':self.save_analysis,'importMail':self.import_mail}
        if method in functions: return functions[method](*args)
        if method=='listFolders':
            id,*parent=args;return self.manager.reader(id).list_folders(parent[0] if parent else None)
        if method in ('listPresets','savePreset'):
            id,*rest=args;self.manager.entry(id);return self.store.list_presets(id) if method=='listPresets' else self.store.save_preset(id,*rest)
        if method=='openOriginal':
            id,=args;safe_original(self.store.get_local(id)['webUrl']);return None
        raise ValueError('未知邮箱工作台接口')
