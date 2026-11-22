"""Packaged Windows acceptance: real local business state, controlled model, no accounts.

Run with the shipped python.exe, not the development interpreter. The only
simulated boundary is the language-model response. No IMAP/SMTP is contacted.
"""
import argparse
import asyncio
import base64
from contextlib import ExitStack
from datetime import datetime, timezone
from email import policy
from email.parser import BytesParser
import json
import os
from pathlib import Path
import platform
import subprocess
import sys
import tempfile
import traceback
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
p = argparse.ArgumentParser(description=__doc__)
p.add_argument('--backend-dir', type=Path, required=True)
p.add_argument('--output', type=Path, default=ROOT/'handover/windows-release/workflow')
p.add_argument('--allow-non-windows', action='store_true', help='Local development only; DPAPI is explicitly untested.')
args = p.parse_args()
backend = args.backend_dir.resolve()
output = args.output.resolve(); output.mkdir(parents=True, exist_ok=True)
if sys.platform != 'win32' and not args.allow_non_windows:
    p.error('Run the acceptance with the packaged Windows python.exe.')
sys.path[:0] = [str(backend), str(ROOT/'backend/tests')]
import task_backend
assert Path(task_backend.__file__).resolve().is_relative_to(backend), 'Wrong backend imported'
from task_backend.service import BackendService
from task_backend.agent_tools import AgentTools
from task_backend.mail_files import extract_document
from task_backend.document_render import soffice_path
from task_backend.settings import Settings
from langchain_core.messages import AIMessage
from test_agent_runtime import ScriptedModel, tool_call
from network_guard import no_external_network

checks = []
report = dict(startedAt=datetime.now(timezone.utc).isoformat(), platform=platform.platform(),
              python=platform.python_version(), executable=str(Path(sys.executable).resolve()),
              backendDirectory=str(backend), scope='Packaged local workflow with scripted model decisions; real framework, SQLite, DOCX/PDF rendering, drafts, task changes and native Windows credential storage.',
              realModel=False, realMailServer=False, externalMessagesSent=0, smtpAttemptCount=0,
              humanVisualReview='not performed by this automated script', checks=checks)

def check(name, condition, details=None):
    checks.append(dict(name=name, passed=bool(condition), **({'details':details} if details is not None else {})))
    if not condition:
        raise AssertionError(name)

def write_report():
    report['finishedAt'] = datetime.now(timezone.utc).isoformat()
    report['passed'] = bool(checks) and all(x['passed'] for x in checks) and 'error' not in report
    (output/'workflow.json').write_text(json.dumps(report, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')

def block_smtp(*unused, **kw):
    report['smtpAttemptCount'] += 1
    raise AssertionError('SMTP must not be constructed by the draft-only acceptance')

service = None
ctx = None
try:
    with tempfile.TemporaryDirectory(prefix='tm-win-acceptance-') as temporary, ExitStack() as guards:
        home = Path(temporary)/'用户资料 with spaces'; home.mkdir()
        directory = home/'data'; directory.mkdir()
        def close_current():
            global ctx, service
            if ctx is not None:ctx.close();ctx=None
            if service is not None:service.close();service=None
        guards.callback(close_current)
        guards.enter_context(no_external_network())
        guards.enter_context(patch('smtplib.SMTP', side_effect=block_smtp))
        guards.enter_context(patch('smtplib.SMTP_SSL', side_effect=block_smtp))
        guards.enter_context(patch.dict(os.environ, {'LANGSMITH_TRACING':'false','LANGCHAIN_TRACING_V2':'false','LANGCHAIN_TRACING':'false'}))
        config = dict(baseUrl='https://offline.example.invalid/v1', model='scripted/acceptance', apiKey='synthetic-acceptance-key-not-a-credential')
        if sys.platform == 'win32':
            Settings(directory).save(config)
            serialized = (directory/'settings.json').read_bytes()
            check('native_dpapi_ciphertext_saved', config['apiKey'].encode() not in serialized)
            # A fresh shipped interpreter must decrypt the same Windows user's settings.
            code = ('import json,sys;sys.path.insert(0,sys.argv[1]);'
                    'from task_backend.settings import Settings;'
                    's=Settings(sys.argv[2]);'
                    'assert s.for_request()["apiKey"]=="synthetic-acceptance-key-not-a-credential";'
                    'print(json.dumps({"decrypted":True,"hasApiKey":s.get()["hasApiKey"]}))')
            result = subprocess.run([sys.executable,'-I','-c',code,str(backend),str(directory)], capture_output=True, text=True, timeout=45)
            check('native_dpapi_fresh_process_read', result.returncode == 0 and json.loads(result.stdout)['decrypted'], result.stderr[-1200:] if result.returncode else None)
            report['credentials'] = 'real Windows DPAPI encrypt and decrypt in a fresh packaged Python process'
        else:
            report['credentials'] = 'not tested: non-Windows development run'

        def open_service():
            value = BackendService(directory, worker=True)
            if sys.platform != 'win32':value.settings.for_request = lambda: dict(config)
            value.agent.schedule = lambda: None
            return value
        service = open_service()
        secretary = service.call('agent:getSecretary')
        check('fixed_secretary_created', secretary['kind']=='secretary')
        binding = service.mail.manager.add('imap')['id']
        eml = ('From: course-office@example.invalid\r\nTo: student@example.invalid\r\n'
               'Subject: Course project review\r\nMIME-Version: 1.0\r\nContent-Type: text/plain; charset=utf-8\r\n\r\n'
               'Review meeting is 4 December 2026 at 14:00 HKT. Please prepare the attached progress report.\r\n').encode()
        (output/'course-notice.eml').write_bytes(eml)
        parsed = BytesParser(policy=policy.default).parsebytes(eml)
        body = parsed.get_body(preferencelist=('plain',)).get_content().strip()
        mail = service.mail.store.put(binding,'acceptance-inbox','student@example.invalid',dict(
            id='synthetic-course-notice',subject=str(parsed['subject']),sender=str(parsed['from']),
            receivedAt='2026-11-22T00:00:00Z',body=body,preview='',attachments=[]))
        model = ScriptedModel(script=[
            tool_call('read_mail',dict(message_id=mail['localId']),'read-course'),
            tool_call('create_task_draft',dict(title='Course review meeting',due_at='2026-12-04T14:00:00+08:00',
                      source_mail_ids=[mail['localId']],source_quote='Review meeting is 4 December 2026 at 14:00 HKT.'),'draft-course'),
            AIMessage(content='The review meeting is prepared as a draft for your approval.')])
        service.agent.model_factory = lambda config,reasoning:model
        run = service.call('agent:startTurn',dict(text='Read this notice and propose the review meeting.',conversationId=secretary['id'],mailIds=[mail['localId']]))
        check('official_agent_executed', asyncio.run(service.agent.run_next()))
        state = service.call('agent:getRun',run['id'])
        check('official_agent_completed',state['status']=='completed',state.get('error'))
        (output/'agent-run.json').write_text(json.dumps(state,ensure_ascii=False,indent=2),encoding='utf-8')
        draft_id = next(x['id'] for x in state['artifacts'] if x['kind']=='task_draft')
        check('proposal_does_not_mutate_calendar',service.repo.call('listTodos')==[])
        analysis = service.mail.store.get_analysis(draft_id)
        saved = service.call('mail-workspace:saveAnalysis',draft_id,analysis['drafts'])
        tasks = service.repo.call('listTodos');check('user_approval_saves_one_task',saved['count']==1 and len(tasks)==1)
        task = tasks[0]
        check('calendar_exact_time',task['due']['value']['at']=='2026-12-04T06:00:00.000Z')
        correction = service.mail.store.put(binding,'acceptance-inbox','student@example.invalid',dict(
            id='synthetic-correction',subject='Review meeting time update',sender='course-office@example.invalid',
            receivedAt='2026-11-23T00:00:00Z',body='The same review meeting moves to 7 December at 15:00 HKT.',attachments=[]))
        proposal = service.task_changes.propose(dict(action='update',taskId=task['id'],expectedRevision=task['revision'],reason='Latest course correction',
            patch={'due':{'mode':'value','value':{'kind':'minute','at':'2026-12-07T15:00:00+08:00','timeZone':'Asia/Hong_Kong'}}},
            evidence=[{'mailId':correction['localId'],'quote':'The same review meeting moves to 7 December at 15:00 HKT.'}]))
        check('change_requires_separate_approval',service.repo.call('listTodos')[0]['revision']==task['revision'])
        approval=dict(id=proposal['id'],expectedHash=proposal['hash'])
        service.call('task-changes:approve',approval)
        updated=service.repo.call('listTodos')
        check('reschedule_keeps_task_identity',len(updated)==1 and updated[0]['id']==task['id'] and updated[0]['due']['value']['at']=='2026-12-07T07:00:00.000Z')
        revision=updated[0]['revision'];service.call('task-changes:approve',approval)
        check('repeat_approval_is_idempotent',service.repo.call('listTodos')[0]['revision']==revision)

        # Use the existing template and exact edits from the public teammate handover.
        original_path=ROOT/'examples/document-workflows/inputs/course-progress.docx'
        original_bytes=original_path.read_bytes()
        source=service.files.import_local(original_path)
        ctx=AgentTools(service,'windows-documents',conversation_id=secretary['id'])
        structure=ctx.inspect_file(source['id'])['structure']
        target=next(v for v in structure['paragraphs'] if v['text']=='Analysis is pending.')
        status_cell=next(row[-1] for row in structure['tables'][0]['rows'] if row[0]['text']=='Data review')
        revised=ctx.edit_word(source['id'],'course-progress-reviewed.docx',[
            dict(target=target['id'],expected=target['text'],text='Analysis is complete. Total eligible hours are 30.'),
            dict(target=status_cell['id'],expected='Pending',text='Reviewed')],call_id='edit-template')
        check('original_template_retained',original_path.read_bytes()==original_bytes and service.files.bytes(source['id'])==original_bytes)
        edited_structure=service.files.inspect(revised['id'])
        check('word_edits_applied',next(row[-1] for row in edited_structure['tables'][0]['rows'] if row[0]['text']=='Data review')['text']=='Reviewed')
        converted=ctx.convert_to_pdf(revised['id'],'course-progress-reviewed.pdf',call_id='convert-template')
        pdf_text=extract_document(service.files.bytes(converted['id']),'pdf')[0]
        check('real_word_pdf_content','Total eligible hours are 30.' in pdf_text and 'Reviewed' in pdf_text,pdf_text[:1600])
        report['renderer']=str(soffice_path())
        ids=[revised['id'],converted['id']]
        for file_id in ids:
            record=service.files.get(file_id)
            validation=service.files.validate(file_id)
            check('render_'+record['extension'],validation['structuralPassed'] and validation['rendered'],validation)
            page=service.files.preview(file_id,1)
            (output/(record['extension']+'-page-1.png')).write_bytes(base64.b64decode(page['dataUrl'].split(',',1)[1]))
            (output/('course-progress-reviewed.'+record['extension'])).write_bytes(service.files.bytes(file_id))
        reply=ctx.create_mail_draft('Re: Course project review','Please review the updated Word and PDF. This is a draft only.',
            reply_to_local_id=mail['localId'],attachment_ids=ids,call_id='reply-with-documents')
        delivery=ctx.validate_mail_draft(reply['id'],ids)
        check('exact_two_attachments',delivery['passed'] and delivery['actualAttachmentIds']==ids and len(reply['attachments'])==2,delivery)
        check('draft_reply_recipient',reply['to']=='course-office@example.invalid')
        check('no_send_receipt',service.compose.list_recent()==[] and report['smtpAttemptCount']==0)
        # A corrupt source must fail while the successful artifacts remain available.
        bad=home/'broken.pdf';bad.write_bytes(b'not a PDF')
        try:service.files.import_local(bad)
        except ValueError:check('corrupt_pdf_rejected',True)
        else:check('corrupt_pdf_rejected',False)
        ctx.close();ctx=None;service.close();service=None
        service=open_service();ctx=AgentTools(service,'windows-reopen',conversation_id=secretary['id'])
        check('secretary_survives_restart',service.call('agent:getSecretary')['id']==secretary['id'])
        check('approved_task_survives_restart',service.repo.call('listTodos')[0]['id']==task['id'] and service.repo.call('listTodos')[0]['revision']==revision)
        check('draft_attachments_survive_restart',ctx.validate_mail_draft(reply['id'],ids)['passed'])
        check('mail_history_survives_restart',service.mail.store.get_local(mail['localId'])['body']==body)
        report['artifacts']=[dict(id=i,sha256=service.files.get(i)['sha256'],extension=service.files.get(i)['extension']) for i in ids]
        ctx.close();ctx=None;service.close();service=None
except Exception as error:
    report['error']=str(error)
    report['traceback']=traceback.format_exc()
finally:
    if ctx is not None:ctx.close()
    if service is not None:service.close()
    write_report()
print(json.dumps(dict(passed=report['passed'],checks=len(checks),report=str(output/'workflow.json')),ensure_ascii=False))
raise SystemExit(0 if report['passed'] else 1)
