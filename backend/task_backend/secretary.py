"""Secretary inbox refresh: existing import pipeline, bounded triage, explicit delivery.

All work stays on the service's owning thread. Neither classification nor an
import/analysis receipt means the user has been briefed or a task is completed.
"""
from copy import deepcopy
from contextlib import contextmanager
from datetime import date, timedelta
import json
import sqlite3
from uuid import NAMESPACE_URL, uuid5
from zoneinfo import ZoneInfo

from .archive import text
from .mail_automation import CATEGORIES, date_time, iso, signature
from .mail_compose import receipt_lock
from .mail_store import compact, fingerprint
from .mail_types import DEFAULT_FILTER
from .operations import JobStop, MailOperations


def content_version(mail):
    # Same content contract as automatic triage, allowing verified cache reuse.
    return signature([fingerprint(mail), mail.get('bodyStatus', 'complete')])


class _InboxImport(MailOperations):
    """Reuse the pagination/resume engine without spawning a second worker."""
    def __init__(self, operations, check):
        self.service, self.db = operations.service, operations.db
        self.external_check, self.interrupted = check, None

    def check(self, job):
        if self.external_check:
            try:
                self.external_check()
            except Exception as exc:
                # The importer already propagates JobStop without treating it
                # as a failed mail; preserve the caller's actual control signal.
                self.interrupted = exc
                raise JobStop('paused') from exc
        super().check(job)


class SecretaryService:
    MAX_CLASSIFICATIONS = 20

    def __init__(self, service):
        self.service = service
        self.db = sqlite3.connect(service.directory / 'secretary.sqlite', timeout=5, isolation_level=None)
        self.db.row_factory = sqlite3.Row
        self.db.executescript('''PRAGMA secure_delete=ON;
CREATE TABLE IF NOT EXISTS secretary_runs(id TEXT PRIMARY KEY,payload TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS secretary_items(local_id TEXT NOT NULL,fingerprint TEXT NOT NULL,payload TEXT NOT NULL,PRIMARY KEY(local_id,fingerprint));
CREATE TABLE IF NOT EXISTS secretary_accounts(binding_id TEXT NOT NULL,identity TEXT NOT NULL,payload TEXT NOT NULL,PRIMARY KEY(binding_id,identity));
''')

    def close(self):
        self.db.close()

    def now(self):
        return iso(date_time(self.service.clock()))

    @contextmanager
    def transaction(self):
        self.db.execute('BEGIN IMMEDIATE')
        try:
            yield
            self.db.execute('COMMIT')
        except BaseException:
            self.db.execute('ROLLBACK')
            raise

    def _run(self, execution_id):
        text(execution_id, 200, True)
        row = self.db.execute('SELECT payload FROM secretary_runs WHERE id=?', (execution_id,)).fetchone()
        return json.loads(row[0]) if row else None

    def _save_run(self, run):
        self.db.execute('INSERT INTO secretary_runs VALUES(?,?) ON CONFLICT(id) DO UPDATE SET payload=excluded.payload',
                        (run['id'], compact(run)))

    def _item(self, local_id, version):
        row = self.db.execute('SELECT payload FROM secretary_items WHERE local_id=? AND fingerprint=?', (local_id, version)).fetchone()
        return json.loads(row[0]) if row else None

    def _save_item(self, item):
        self.db.execute('INSERT INTO secretary_items VALUES(?,?,?) ON CONFLICT(local_id,fingerprint) DO UPDATE SET payload=excluded.payload',
                        (item['localId'], item['fingerprint'], compact(item)))

    def _account(self, binding, identity, today):
        row = self.db.execute('SELECT payload FROM secretary_accounts WHERE binding_id=? AND identity=?', (binding, identity)).fetchone()
        if row:
            return json.loads(row[0])
        start = (today - timedelta(days=6)).isoformat()
        value = dict(bindingId=binding, identity=identity, scopeStart=start, since=start)
        self._save_account(value)
        return value

    def _save_account(self, value):
        self.db.execute('INSERT INTO secretary_accounts VALUES(?,?,?) ON CONFLICT(binding_id,identity) DO UPDATE SET payload=excluded.payload',
                        (value['bindingId'], value['identity'], compact(value)))

    def _new_run(self, execution_id):
        today = self.service.clock().astimezone(ZoneInfo('Asia/Hong_Kong')).date()
        accounts = []
        for binding in self.service.mail.manager.all():
            account = binding['state'].get('account')
            if not account:
                continue
            progress = self._account(binding['id'], account['id'], today)
            accounts.append(dict(bindingId=binding['id'], mailboxIdentity=account['id'], accountEmail=account['email'],
                                 method=binding['method'], since=progress['since'], scopeStart=progress['scopeStart'], until=today.isoformat()))
        return dict(id=execution_id, state='running', createdAt=self.now(), accounts=accounts, imports={}, plan=None, completed=[], errors=[])

    def _import(self, run, check):
        operations = _InboxImport(self.service.operations, check)
        for account in run['accounts']:
            binding = account['bindingId']
            record = run['imports'].get(binding)
            if record and record.get('finished'):
                continue
            job_id = str(uuid5(NAMESPACE_URL, 'secretary-inbox:' + compact([run['id'], binding])))
            try:
                job = operations.job(job_id)
            except ValueError:
                filters = {**deepcopy(DEFAULT_FILTER), 'dateMode': 'range', 'since': account['since'], 'until': account['until']}
                job = self.service.operations.create('import', '秘书收取最近邮件', dict(bindingIds=[binding], filter=filters, onlyNew=True))
                job['id'] = job_id
                job['work'].update(inboxSync=True, accountIndex=0, inboxAccounts=[dict(binding=binding,
                    identity=account['mailboxIdentity'], email=account['accountEmail'], attempted=[], skippedIds=[])])
            job['state'] = 'running'
            operations.save_job(job, force=True)
            run['imports'][binding] = dict(jobId=job_id, finished=False)
            self._save_run(run)
            try:
                operations.run_inbox_sync(job)
            except JobStop:
                job['state'] = 'paused'; operations.save_job(job, force=True)
                raise operations.interrupted or JobStop('paused')
            job['state'] = 'completed'; operations.save_job(job, force=True)
            run['imports'][binding] = dict(jobId=job_id, finished=True, report=deepcopy(job['report']))
            if not job['report']['failed'] and not job['report']['limited'] and not job['report']['bodyPending']:
                progress = self._account(binding, account['mailboxIdentity'], date_time(self.service.clock()).astimezone(ZoneInfo('Asia/Hong_Kong')).date())
                progress['since'] = max(progress['scopeStart'], (date.fromisoformat(account['until']) - timedelta(days=1)).isoformat())
                self._save_account(progress)
            self._save_run(run)

    def _discover(self, run):
        """Register every matching local message, including unclassified backlog."""
        for account in run['accounts']:
            offset = 0
            while True:
                page = self.service.mail.store.list_local(dict(bindingIds=[account['bindingId']], offset=offset, limit=200))
                for summary in page['messages']:
                    if summary['mailboxIdentity'] != account['mailboxIdentity'] or summary['accountEmail'] != account['accountEmail']:
                        continue
                    try:
                        received = date_time(summary['receivedAt']).astimezone(ZoneInfo('Asia/Hong_Kong')).date().isoformat()
                        if not account['scopeStart'] <= received <= account['until']:
                            continue
                        if account['method'] == 'imap':
                            remote = json.loads(summary['id'])
                            if not isinstance(remote, list) or not remote or str(remote[0]).upper() != 'INBOX':
                                continue
                        mail = self.service.mail.store.get_local(summary['localId'])
                        version = content_version(mail)
                        if self._item(mail['localId'], version):
                            continue
                        self._save_item(dict(localId=mail['localId'], fingerprint=version, version=1,
                            bindingId=mail['bindingId'], mailboxIdentity=mail['mailboxIdentity'], accountEmail=mail['accountEmail'],
                            subject=mail['subject'], sender=mail['sender'], fromAddress=mail.get('fromAddress', ''), receivedAt=mail['receivedAt'],
                            unread=mail.get('unread', False), bodyStatus=mail.get('bodyStatus', 'complete'), category='review',
                            status='pending', summary='', reason='尚未分类，未计入已处理', firstSeenAt=self.now(),
                            classifiedAt=None, briefedAt=None, corrected=False, attempts=0, error=None))
                    except (ValueError, TypeError, KeyError):
                        run['errors'].append(dict(stage='discover', localId=summary.get('localId'), bindingId=account['bindingId'],
                                                  code='LOCAL_MAIL_INVALID', message='本地邮件元数据无效，未将它当作已分类或忽略。'))
                offset += len(page['messages'])
                if offset >= page['total'] or not page['messages']:
                    break
        self._save_run(run)

    def _current_items(self):
        items = []
        for row in self.db.execute('SELECT payload FROM secretary_items'):
            item = json.loads(row[0])
            try:
                mail = self.service.mail.store.get_local(item['localId'])
                if content_version(mail) == item['fingerprint']:
                    items.append(item)
            except ValueError:
                pass  # Deleted originals remain audit records, not navigable sources.
        return items

    def _classify(self, item, check):
        mail = self.service.mail.store.get_local(item['localId'])
        def guard():
            if check:
                check()
            self.service.mail.manager.check_identity(mail['bindingId'], mail['mailboxIdentity'])
            if content_version(self.service.mail.store.get_local(mail['localId'])) != item['fingerprint']:
                raise ValueError('邮件内容已变化，请在下一轮刷新重新分类')
        guard()
        item['attempts'] += 1
        try:
            cached = self.service.automation.item(item['localId'])
            rules = self.service.automation.settings()['config']
            if cached and cached.get('fingerprint') == item['fingerprint'] and cached.get('status') == 'done':
                classified = {k: cached[k] for k in ('category', 'summary', 'reason')}
                item.update(classificationSource='automation', corrected=bool(cached.get('corrected')), analysisId=cached.get('analysisId'))
            elif mail.get('fromAddress', '').lower() in rules['mutedSenders']:
                classified = dict(category='spam', summary='按用户发件人规则折叠，原邮件保留。', reason='匹配用户屏蔽发件人规则')
                item['classificationSource'] = 'sender-rule'
            elif mail.get('bodyStatus', 'complete') != 'complete':
                raise ValueError('邮件正文未完整读取，未发送部分正文给模型')
            elif len(compact({k: mail.get(k, '') for k in ('subject', 'sender', 'receivedAt', 'body')}).encode('utf-16-le')) // 2 > 14000:
                raise ValueError('邮件超过自动分类长度，请分段阅读原文')
            else:
                ai_signature = signature(self.service.settings.for_request())
                classified = self.service.automation.classify_message(mail, guard)
                if signature(self.service.settings.for_request()) != ai_signature:
                    raise ValueError('模型配置变化，未使用旧配置的迟到分类')
                item['classificationSource'] = 'model'
                if mail.get('fromAddress', '').lower() in rules['importantSenders'] and classified['category'] in ('normal', 'spam'):
                    classified.update(category='important', reason='用户重要发件人规则优先；' + classified['reason'][:400])
            guard()
            item.update(classified, originalClassification=deepcopy(classified), status='classified', error=None, classifiedAt=self.now())
        except Exception as exc:
            if getattr(exc, 'control_stop', False):
                raise
            item.update(status='failed', category='review', summary='', reason='分类未完成，需要重试或查看原文',
                        error='正文不完整、内容变化、模型配置或分类请求失败；本轮未认定其优先级。')
        # Compare and commit together: even a failed late model reply must not
        # replace a correction made while its request was in flight.
        with self.transaction():
            current = self._item(item['localId'], item['fingerprint'])
            if current and current.get('corrected'):
                return
            item['version'] = (current or item)['version'] + 1
            self._save_item(item)

    def get_briefing(self, include_briefed=False, limit=100, offset=0):
        if type(include_briefed) is not bool or type(limit) is not int or not 1 <= limit <= 200 or type(offset) is not int or offset < 0:
            raise ValueError('秘书摘要分页参数无效')
        current = self._current_items()
        items = [i for i in current if include_briefed or not i['briefedAt']]
        order = {'action': 0, 'important': 1, 'review': 2, 'normal': 3, 'spam': 4}
        # Show actual classifications first; a large not-yet-classified backlog
        # must not push this round's useful summaries out of the first page.
        items.sort(key=lambda i: (5 if i['status'] == 'pending' else order[i['category']], i['receivedAt'], i['localId']))
        selected = deepcopy(items[offset:offset + limit])
        return dict(items=selected, counts={c: sum(i['category'] == c for i in items) for c in CATEGORIES},
                    total=len(items), unbriefedTotal=sum(not i['briefedAt'] for i in current),
                    pendingClassification=sum(i['status'] != 'classified' for i in current),
                    groups={c: [i['localId'] for i in selected if i['category'] == c] for c in CATEGORIES},
                    collapsedCategories=['normal', 'spam'], offset=offset, limit=limit,
                    hasMore=offset + limit < len(items), nextOffset=offset + limit if offset + limit < len(items) else None,
                    stateNotice='briefedAt只表示已展示汇报；unread是服务器快照。分类、分析和已汇报均不表示事项已办完。')

    def get_refresh(self, execution_id):
        run = self._run(execution_id)
        if not run or 'result' not in run:
            raise ValueError('此秘书刷新尚无完整结果')
        return deepcopy(run['result'])

    def refresh(self, execution_id, check=None):
        text(execution_id, 200, True)
        if not execution_id.strip():
            raise ValueError('刷新执行标识不能为空')
        existing = self._run(execution_id)
        if existing and 'result' in existing:
            return deepcopy(existing['result'])
        with receipt_lock(self.service.directory / 'secretary-refresh.lock') as acquired:
            if not acquired:
                return dict(executionId=execution_id, status='busy', **self.get_briefing(), errors=[dict(stage='refresh', code='REFRESH_BUSY', message='已有刷新正在执行，请稍后重试；没有发起第二批分类。')])
            run = self._run(execution_id)
            if run and 'result' in run:
                return deepcopy(run['result'])
            run = run or self._new_run(execution_id)
            run['state'] = 'running'; self._save_run(run)
            try:
                if check:
                    check()
                self._import(run, check)
                self._discover(run)
                if run['plan'] is None:
                    identities = {(a['bindingId'], a['mailboxIdentity']) for a in run['accounts']}
                    candidates = [i for i in self._current_items() if i['status'] != 'classified' and (i['bindingId'], i['mailboxIdentity']) in identities]
                    candidates.sort(key=lambda i: (i['attempts'], i['receivedAt'], i['localId']))
                    run['plan'] = [dict(localId=i['localId'], fingerprint=i['fingerprint']) for i in candidates[:self.MAX_CLASSIFICATIONS]]
                    self._save_run(run)
                for planned in run['plan']:
                    key = compact(planned)
                    if key in run['completed']:
                        continue
                    if check:
                        check()
                    item = self._item(planned['localId'], planned['fingerprint'])
                    if item and item['status'] != 'classified':
                        try:
                            self._classify(item, check)
                        except ValueError as exc:
                            if getattr(exc, 'control_stop', False):
                                raise
                            item.update(status='failed', category='review', error='原文或邮箱身份变化；需重新刷新核对。')
                            self._save_item(item)
                    run['completed'].append(key); self._save_run(run)
                briefing = self.get_briefing()
                report = dict(added=0, skipped=0, failed=0, bodyRead=0, bodyPending=0, limited=False, failures=[], mailboxErrors=[])
                for record in run['imports'].values():
                    saved = record.get('report', {})
                    for field in ('added', 'skipped', 'failed', 'bodyRead', 'bodyPending'):
                        report[field] += saved.get(field, 0)
                    report['limited'] |= saved.get('limited', False)
                    for field in ('failures', 'mailboxErrors'):
                        report[field].extend(saved.get(field, []))
                errors = run['errors'] + report['failures'] + report['mailboxErrors']
                errors += [dict(stage='classification', localId=i['localId'], message=i['error']) for i in self._current_items() if i.get('error')]
                if not run['accounts']:
                    errors.append(dict(stage='accounts', code='NO_CONNECTED_ACCOUNT', message='尚无已连接邮箱，未执行远端收取；不是收件箱为空。'))
                status = 'needs_setup' if not run['accounts'] else 'partial' if errors or briefing['pendingClassification'] or report['limited'] else 'completed'
                run['state'] = status
                run['result'] = dict(executionId=execution_id, status=status, createdAt=run['createdAt'], finishedAt=self.now(),
                    accounts=run['accounts'], importReport=report, classificationBudget=self.MAX_CLASSIFICATIONS,
                    attemptedClassifications=len(run['completed']), errors=errors,
                    hasMoreClassification=bool(briefing['pendingClassification']), **briefing)
                self._save_run(run)
                return deepcopy(run['result'])
            except BaseException:
                run['state'] = 'paused'; self._save_run(run)
                raise

    def mark_briefed(self, execution_id, items=None):
        result = self.get_refresh(execution_id)
        allowed = {(i['localId'], i['fingerprint'], i['version']): i for i in result['items']}
        selected = result['items'] if items is None else items
        if not isinstance(selected, list) or len(selected) > 200:
            raise ValueError('汇报范围无效')
        keys = []
        for item in selected:
            if not isinstance(item, dict):
                raise ValueError('汇报来源必须包含邮件ID、内容版本和分类版本')
            key = (item.get('localId'), item.get('fingerprint'), item.get('version'))
            if key not in allowed:
                raise ValueError('不能把未包含在本次结果中的邮件标为已汇报')
            keys.append(key)
        marked = []
        with self.transaction():
            for local_id, version, revision in keys:
                item = self._item(local_id, version)
                if not item or item['version'] != revision or item['status'] != 'classified':
                    continue
                try:
                    if content_version(self.service.mail.store.get_local(local_id)) != version:
                        continue
                except ValueError:
                    continue
                item['briefedAt'] = item['briefedAt'] or self.now()
                item['briefedExecutionId'] = execution_id
                self._save_item(item); marked.append(local_id)
        return dict(executionId=execution_id, marked=marked, notice='仅记录汇报展示，不代表邮件已读、已回复或事项完成。')

    def correct_category(self, local_id, category, reason=''):
        if category not in CATEGORIES:
            raise ValueError('分类无效')
        reason = text(reason, 500).strip()
        mail = self.service.mail.store.get_local(text(local_id, 2048, True))
        with self.transaction():
            item = self._item(local_id, content_version(mail))
            if not item:
                raise ValueError('此邮件尚无当前版本的秘书分类，请先刷新')
            item.setdefault('originalClassification', {k: item[k] for k in ('category', 'summary', 'reason')})
            item.update(category=category, reason=reason or '用户更正分类', corrected=True, status='classified', error=None,
                        correctedAt=self.now(), briefedAt=None, version=item['version'] + 1)
            self._save_item(item)
        return deepcopy(item)
