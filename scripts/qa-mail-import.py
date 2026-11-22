#!/usr/bin/env python3
"""Offline acceptance probe; exit 1 means a recorded acceptance case failed.

Run from the repository root with .venv/bin/python scripts/qa-mail-import.py.
No production data, real account, model service, or Keychain is used or changed.
"""
import base64
from contextlib import ExitStack, contextmanager
from datetime import datetime, timezone
from email.message import EmailMessage
from email.policy import SMTP
import hashlib
import json
from pathlib import Path
import platform
import re
import sys
import tempfile
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'backend'))

from cryptography.fernet import Fernet
from task_backend.mail_readers import ImapReader
from task_backend.mail_types import DEFAULT_FILTER
from task_backend.service import BackendService

OUTPUT = ROOT / 'handover/qa-2026-11-20-imap-fix/mail-import.json'
FILTER = dict(DEFAULT_FILTER, dateMode='range', since='2026-11-20', until='2026-11-20', folderIds=['INBOX'])
HTML = '<p>课程通知：周五完成中文报告。</p><p>提交格式：PDF &amp; 示例。</p><script>DO_NOT_DISPLAY_SCRIPT</script>'
AT = datetime(2026, 11, 20, 0, 0, tzinfo=timezone.utc)


def wire_message(uid, subject, body, html=False, attachment=False):
    message = EmailMessage(policy=SMTP)
    message['From'] = 'teacher@example.invalid'
    message['To'] = 'student@example.invalid'
    message['Subject'] = subject
    message.set_content(body, subtype='html' if html else 'plain', charset='utf-8')
    if attachment:
        message.add_attachment(b'ATTACHMENT_BYTES_NOT_BODY\n' + b'x' * (2 * 1024 * 1024),
                               maintype='application', subtype='pdf', filename='课程要求.pdf')
    text_part = message.get_payload(0) if attachment else message
    headers, encoded = text_part.as_bytes(policy=SMTP).split(b'\r\n\r\n', 1)
    structure = (b'TEXT', b'HTML' if html else b'PLAIN', (b'CHARSET', b'utf-8'), None, None,
                 text_part['Content-Transfer-Encoding'].encode(), len(encoded), 1, None, None)
    parts = {'1':(headers, encoded)}
    attachment_size = None
    if attachment:
        _, attachment_wire = message.get_payload(1).as_bytes(policy=SMTP).split(b'\r\n\r\n', 1)
        attachment_size = len(attachment_wire)
        pdf = (b'APPLICATION', b'PDF', (b'NAME', '课程要求.pdf'.encode()), None, None, b'BASE64',
               attachment_size, None, (b'ATTACHMENT', (b'FILENAME', '课程要求.pdf'.encode())))
        structure = ([structure, pdf], b'MIXED')
    return dict(uid=uid, subject=subject, raw=message.as_bytes(policy=SMTP), structure=structure,
                parts=parts, attachment_size=attachment_size)


class ProtocolMailbox:
    """Synthetic IMAP boundary; no parsing, filtering, persistence or dedup here."""
    def __init__(self, messages):
        self.messages = {item['uid']:item for item in messages}
        self.fail = False
        self.fetches = []

    def login(self, username, password):
        if self.fail:
            raise OSError('synthetic mailbox connection unavailable')
        if password != 'fixture-only':
            raise AssertionError('unexpected non-fixture credential')

    def logout(self):
        pass

    def list_folders(self):
        return [((), b'/', 'INBOX')]

    def select_folder(self, folder, readonly=True):
        if folder != 'INBOX' or readonly is not True:
            raise AssertionError('import should select INBOX read-only')
        return {b'UIDVALIDITY':71}

    def search(self, criteria):
        return list(self.messages)

    def fetch(self, uids, fields):
        self.fetches.append(dict(uids=list(uids), fields=list(fields)))
        output = {}
        for uid in uids:
            item = self.messages[uid]
            if fields == ['UID']:
                output[uid] = {b'UID':uid}
                continue
            match = re.fullmatch(r'BODY\.PEEK\[\]<([0-9]+)\.([0-9]+)>', fields[0])
            if match:
                start, take = map(int, match.groups())
                output[uid] = {f'BODY[]<{start}>'.encode():item['raw'][start:start+take]}
                continue
            match = re.fullmatch(r'BODY\.PEEK\[([0-9]+)\.MIME\]', fields[0])
            if match:
                section = match[1]
                output[uid] = {f'BODY[{section}.MIME]'.encode():item['parts'][section][0]}
                continue
            match = re.fullmatch(r'BODY\.PEEK\[([0-9]+)\]<([0-9]+)\.([0-9]+)>', fields[0])
            if match:
                section, start, take = match[1], int(match[2]), int(match[3])
                # Attachment part 2 is deliberately unavailable: requesting it fails.
                output[uid] = {f'BODY[{section}]<{start}>'.encode():item['parts'][section][1][start:start+take]}
                continue
            output[uid] = {
                b'ENVELOPE':SimpleNamespace(subject=item['subject'].encode(), from_=[
                    SimpleNamespace(name=None, mailbox=b'teacher', host=b'example.invalid')]),
                b'INTERNALDATE':AT, b'FLAGS':(), b'RFC822.SIZE':len(item['raw']),
                b'BODYSTRUCTURE':item['structure'],
            }
        return output


@contextmanager
def isolated_service():
    with tempfile.TemporaryDirectory(prefix='tm-qa-import-') as folder, ExitStack() as stack:
        cipher = Fernet(Fernet.generate_key())
        stack.enter_context(patch('task_backend.vault.encrypt',
                                  lambda value:base64.b64encode(cipher.encrypt(value.encode())).decode()))
        stack.enter_context(patch('task_backend.vault.decrypt',
                                  lambda value:cipher.decrypt(base64.b64decode(value)).decode()))
        stack.enter_context(patch('socket.create_connection', side_effect=AssertionError('real networking forbidden')))
        def no_http(*args):
            raise AssertionError('No model or external HTTP request belongs in this import probe')
        service = BackendService(Path(folder), request=no_http, worker=True)
        mailboxes = {}
        service.mail.manager.reader_factory = lambda manager, binding:ImapReader(
            manager, binding, client_factory=lambda *args, **kwargs:mailboxes[binding])
        try:
            yield service, mailboxes
        finally:
            service.close()


def add_account(service, mailboxes, label, messages):
    binding = service.call('mail-workspace:addAccount', 'imap')['id']
    mailboxes[binding] = ProtocolMailbox(messages)
    service.call('mail-workspace:configureImap', binding, dict(email=label+'@example.invalid', username='',
                 host='imap.example.invalid', port=993, security='tls', password='fixture-only'))
    service.call('mail-workspace:connect', binding)
    return binding


def import_mail(service, bindings):
    job = service.call('mail-operations:startImport', dict(bindingIds=bindings, filter=FILTER, onlyNew=True))
    if not service.operations.run_next():
        raise AssertionError('expected queued import job')
    job = service.call('mail-operations:getJob', job['id'])
    report = job.get('report', {})
    summary = {key:job.get(key) for key in ('state', 'done', 'total')}
    summary.update({key:report.get(key) for key in ('added', 'skipped', 'failed', 'bodyRead', 'bodyPending', 'limited')})
    summary['mailbox_error_codes'] = [error['code'] for error in report.get('mailboxErrors', [])]
    summary['mail_failure_codes'] = [error['code'] for error in report.get('failures', [])]
    return summary


def case(id, name, inputs, expected, actual, checks, **extra):
    return dict(id=id, name=name, status='PASS' if all(checks.values()) else 'FAIL',
                inputs=inputs, expected=expected, actual=actual, checks=checks, **extra)


def html_and_duplicate():
    with isolated_service() as (service, mailboxes):
        binding = add_account(service, mailboxes, 'html', [wire_message(1, 'QA-HTML 中文课程通知', HTML, html=True)])
        result = import_mail(service, [binding])
        local = service.call('mail-workspace:listLocal', {})
        mail = service.call('mail-workspace:getLocal', local['messages'][0]['localId'])
        first = case('IMPORT-01', '中文 HTML 正文导入', dict(message_count=1, body=HTML, mime='text/html; charset=utf-8'),
                     dict(added=1, body_status='complete', chinese_text_preserved=True, html_script_omitted=True),
                     dict(report=result, body=mail['body'], body_status=mail['bodyStatus']),
                     dict(import_completed=result['state']=='completed' and result['added']==1 and result['failed']==0,
                          complete=mail['bodyStatus']=='complete', chinese='课程通知：周五完成中文报告。' in mail['body'],
                          entity_decoded='PDF & 示例' in mail['body'], script_removed='DO_NOT_DISPLAY_SCRIPT' not in mail['body']))
        before = len(mailboxes[binding].fetches)
        repeated = import_mail(service, [binding])
        after = service.call('mail-workspace:listLocal', {})
        new_fetches = mailboxes[binding].fetches[before:]
        second = case('IMPORT-02', '重复导入完整邮件不新增及不重读正文', dict(reuse='IMPORT-01', onlyNew=True),
                      dict(added=0, skipped=1, local_total=1, body_download_requests=0),
                      dict(report=repeated, local_total=after['total'],
                           body_download_requests=sum(f['fields'][0].startswith('BODY.PEEK') for f in new_fetches)),
                      dict(no_new=repeated['added']==0, skipped=repeated['skipped']==1,
                           single_copy=after['total']==1,
                           no_body_reread=not any(f['fields'][0].startswith('BODY.PEEK') for f in new_fetches)))
        return [first, second]


def attachment_metadata():
    item = wire_message(11, 'QA-ATTACH 中文正文及 PDF', HTML, html=True, attachment=True)
    with isolated_service() as (service, mailboxes):
        binding = add_account(service, mailboxes, 'attachment', [item])
        result = import_mail(service, [binding])
        local = service.call('mail-workspace:listLocal', {})
        mail = service.call('mail-workspace:getLocal', local['messages'][0]['localId'])
        requested_parts = [f['fields'][0] for f in mailboxes[binding].fetches if f['fields'][0].startswith('BODY.PEEK')]
        return [case('IMPORT-03', '大附件邮件仅读取中文 HTML 正文并保留附件元数据',
                     dict(message_count=1, raw_bytes=len(item['raw']), attachment='课程要求.pdf', attachment_mime='application/pdf'),
                     dict(body_status='complete', attachment_metadata_count=1, attachment_content_parsed=False,
                          whole_message_downloaded=False),
                     dict(report=result, body=mail['body'], body_status=mail['bodyStatus'], attachments=mail['attachments'],
                          requested_parts=requested_parts, diagnostics=mail.get('diagnostics', [])),
                     dict(imported=result['added']==1 and result['failed']==0,
                          complete=mail['bodyStatus']=='complete', chinese='课程通知：周五完成中文报告。' in mail['body'],
                          attachment_metadata=mail['attachments']==[dict(name='课程要求.pdf', type='application/pdf', size=item['attachment_size'], inline=False)],
                          no_attachment_content='ATTACHMENT_BYTES_NOT_BODY' not in mail['body'],
                          only_text_part=bool(requested_parts) and all(re.match(r'BODY\.PEEK\[1(?:\]|\.MIME\])', value) for value in requested_parts)),
                     limitation='本产品不解析附件内容；本用例将仅保留元数据视为预期，不将未解析 PDF 判为缺陷。')]


def partial_account_failure():
    with isolated_service() as (service, mailboxes):
        bad = add_account(service, mailboxes, 'unavailable', [wire_message(1, 'QA-BAD', '不可用账号的课程通知。')])
        good = add_account(service, mailboxes, 'healthy', [wire_message(1, 'QA-GOOD', '健康账号仍需导入这封课程通知。')])
        mailboxes[bad].fail = True
        result = import_mail(service, [bad, good])
        local = service.call('mail-workspace:listLocal', {})
        return [case('IMPORT-04', '两个邮箱中第一个失败时继续导入第二个',
                     dict(account_count=2, processing_order=['unavailable', 'healthy'], fault='first mailbox login raises OSError'),
                     dict(added=1, failed=1, mailbox_error_codes=['LIST_FAILED'], successful_account_retained=True),
                     dict(report=result, local_total=local['total'], subjects=[mail['subject'] for mail in local['messages']]),
                     dict(completed_with_report=result['state']=='completed', imported=result['added']==1 and result['bodyRead']==1,
                          failure_reported=result['failed']==1 and result['mailbox_error_codes']==['LIST_FAILED'],
                          healthy_survives=local['total']==1 and local['messages'][0]['bindingId']==good))]


def over_capacity():
    messages = [wire_message(uid, f'QA-MASS-{uid:03d}', f'合成课程邮件 {uid:03d}：整理学习记录。') for uid in range(1, 202)]
    with isolated_service() as (service, mailboxes):
        binding = add_account(service, mailboxes, 'capacity', messages)
        first = import_mail(service, [binding])
        after_first = service.call('mail-workspace:listLocal', dict(limit=1000))
        one = case('IMPORT-05', '201 封匹配邮件的首批上限及提示',
                   dict(message_count=201, same_date='2026-11-20', onlyNew=True, uid_range=[1, 201]),
                   dict(added=200, limited=True, local_total=200),
                   dict(report=first, local_total=after_first['total']),
                   dict(batch_cap=first['added']==200, explicit_limit=first['limited'] is True,
                        local_total=after_first['total']==200, no_read_failure=first['failed']==0),
                   limitation='当前清单硬上限为 200 封；该用例仅验收上限提示，不代表已读完 201 封。')
        second = import_mail(service, [binding])
        after_second = service.call('mail-workspace:listLocal', dict(limit=1000))
        subjects = {mail['subject'] for mail in after_second['messages']}
        missing = [item['subject'] for item in messages if item['subject'] not in subjects]
        two = case('IMPORT-06', '同一范围重复 onlyNew 导入能否取得第 201 封',
                   dict(reuse='IMPORT-05', second_import_filter_unchanged=True, onlyNew=True),
                   dict(new_remaining_mail=1, local_total=201, missing_subjects=[]),
                   dict(report=second, local_total=after_second['total'], missing_subjects=missing),
                   dict(remaining_imported=second['added']==1, all_selected_mail_reachable=after_second['total']==201,
                        no_missing=missing==[]),
                   severity='P1',
                   acceptance_basis='用户重复导入同一筛选范围时应可取得尚未保存的余下邮件，或有明确的可继续读取入口。',
                   diagnosis=('已处理远端身份在 200 封候选上限之前被排除，第二批可读取剩余邮件。'
                              if second['added']==1 and not missing else
                              '重复导入仍未取得剩余邮件，请检查候选上限前的本地状态排除逻辑。'),
                   source_evidence=['backend/task_backend/mail_readers.py:149-175', 'backend/task_backend/mail_workspace.py:71', 'backend/task_backend/operations.py:136-148'],
                   historical_failure_report='handover/qa-2026-11-20/mail-import.json')
        return [one, two]


def main():
    cases = []
    for group in (html_and_duplicate, attachment_metadata, partial_account_failure, over_capacity):
        try:
            cases.extend(group())
        except Exception as error:
            cases.append(dict(id='HARNESS-'+group.__name__, name=group.__name__, status='FAIL',
                              expected='scenario completes and records observations',
                              actual=dict(error_type=type(error).__name__, message=str(error)),
                              inputs='generated synthetic fixtures in this script', harness_error=True))
    counts = {status:sum(item['status']==status for item in cases) for status in ('PASS', 'FAIL', 'LIMITATION')}
    files = ['backend/task_backend/mail_readers.py', 'backend/task_backend/operations.py', 'backend/task_backend/mail_store.py', 'backend/task_backend/mail_workspace.py', 'scripts/qa-mail-import.py']
    report = dict(recorded_at=datetime.now(timezone.utc).isoformat(), title='IMAP 续导修复后的邮件导入/MIME/容量复测',
                  historical_failure_report='handover/qa-2026-11-20/mail-import.json',
                  reproduction_command='.venv/bin/python scripts/qa-mail-import.py',
                  command_working_directory='task-manager-mac repository root',
                  exit_code_contract='0 = all acceptance checks pass; 1 = report contains FAIL, including retained product failures',
                  python_version=platform.python_version(), case_count=len(cases), summary=counts,
                  evidence_scope=dict(real=['production BackendService, AccountManager, ImapReader, MailOperations import/checkpoints, MIME parsing, SQLite'],
                                      substituted=['synthetic IMAP protocol responses', 'temporary Fernet credential boundary'],
                                      not_exercised=['real IMAP/SMTP providers', 'AI model', 'OS Keychain', 'Electron UI', 'separate worker process'],
                                      isolation='fresh temporary SQLite/account directories per group, deleted after run; no user data or real credentials',
                                      job_execution='worker=True and run_next() exercise production job code synchronously in the test process'),
                  source_sha256={file:hashlib.sha256((ROOT/file).read_bytes()).hexdigest() for file in files}, cases=cases)
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(report, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    print(json.dumps(dict(report=str(OUTPUT.relative_to(ROOT)), cases=len(cases), **counts), ensure_ascii=False))
    return 1 if counts['FAIL'] else 0


if __name__ == '__main__':
    raise SystemExit(main())
