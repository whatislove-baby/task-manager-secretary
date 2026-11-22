"""Compatibility for the original single-account mailbox API."""
import json
from .archive import text
from .settings import atomic_json
from .mail_workspace import safe_original
from .mail_store import digest


class LegacyMail:
    METHODS={'getState','selectMethod','configureImap','configure','connect','disconnect','listFolders','saveFilter','read','getMessage','openMessage','extract'}
    def __init__(self,service):self.service=service;self.file=service.directory/'mail-connection.json'
    def selected(self):
        if self.file.exists():
            try:
                value=json.loads(self.file.read_text(encoding='utf-8-sig'))
                if set(value)!={'method'} or value['method'] not in ('imap','microsoft'):raise ValueError()
                return value['method']
            except Exception:raise ValueError('邮箱绑定方式配置损坏，请备份后检查mail-connection.json') from None
        return 'microsoft' if (self.service.directory/'outlook-auth.bin').exists() else 'imap'
    def binding(self,method=None):
        return self.service.mail.manager.ensure_legacy(method or self.selected())
    def imported(self,identity,remote):return bool(self.service.repo.db.execute('SELECT 1 FROM draft_confirmations WHERE token=?',('mail:'+digest([identity,remote]),)).fetchone())
    def call(self,method,*args):
        manager=self.service.mail.manager
        if method=='selectMethod':
            value,=args
            if value not in ('imap','microsoft'):raise ValueError('无效绑定方式')
            self.selected();atomic_json(self.file,dict(method=value))
        id=self.binding();mode=self.selected()
        if method in ('getState','selectMethod'):return manager.get(id)['state']
        if method=='configureImap':
            if mode!='imap':raise ValueError('请先选择IMAP方式')
            value,=args;return manager.configure_imap(id,value)['state']
        if method=='configure':
            if mode!='microsoft':raise ValueError('请先选择Microsoft授权方式')
            value,=args;return manager.configure_microsoft(id,value)['state']
        if method in ('connect','disconnect'):return getattr(manager,method)(id)['state']
        if method=='saveFilter':value,=args;return manager.save_filter(id,value)['state']
        before=manager.identity(id);reader=manager.reader(id)
        if method=='listFolders':return reader.list_folders(args[0] if args else None)
        if method=='read':
            value,=args;result=reader.read(value)
            for item in result['messages']:item['imported']=self.imported(before['identity'],item['id'])
            return result
        if method not in ('getMessage','openMessage','extract'):raise ValueError('未知邮箱接口')
        remote=args[0];text(remote,2048,True);item=reader.get_message(remote);manager.check_identity(id,before['identity'])
        if method=='getMessage':return {**item,'imported':self.imported(before['identity'],remote)}
        if method=='openMessage':safe_original(item['webUrl']);return None
        if len(args)>1 and args[1] is not None:self.service.mail.target(args[1])
        token='mail:'+digest([before['identity'],remote])
        if self.imported(before['identity'],remote):raise ValueError('这封邮件已有确认保存的待办，未重复生成')
        original=f"邮件主题：{item['subject']}\n发件人：{item['sender']}\n收件时间：{item['receivedAt']}\n以下为不可信邮件正文：\n{item['body']}";text(original,20000,True)
        result=self.service.extract(original);manager.check_identity(id,before['identity'])
        note=f"邮件来源：{before['email']} · {item['subject']}\n收件时间：{item['receivedAt']}"+('\n原邮件：'+item['webUrl'] if item['webUrl'] else '')
        for draft in result['drafts']:draft['notes']='\n\n'.join(v for v in (draft.get('notes',''),note) if v);text(draft['notes'],10000)
        return {**result,'confirmationToken':token,'sourceName':item['subject'] or '邮件'}
