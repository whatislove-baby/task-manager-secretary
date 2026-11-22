"""Real Windows packaged-Python -> Docker Skill -> new chat -> reply draft acceptance.

Only model decisions and the synthetic MIME transport are controlled. The
importer, Deep Agents graph, Docker runner, approval gates and stores are real.
No credentials, provider APIs, SMTP or live mailboxes are used.
"""
import argparse
import asyncio
from contextlib import ExitStack
import csv
from datetime import datetime, timezone
import hashlib
import importlib.util
from io import BytesIO, StringIO
import json
import os
from pathlib import Path
import platform
import re
import sys
import tempfile
import traceback
from unittest.mock import patch
import zipfile

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT/'scripts/fixtures/windows-skill-workflow'
EXAMPLES = ROOT/'examples/script-skills'
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--backend-dir', type=Path, required=True)
parser.add_argument('--output', type=Path, default=ROOT/'handover/windows-release/skill-workflow')
parser.add_argument('--allow-source-runtime', action='store_true', help='Windows source CI only; does not count as packaged-runtime acceptance.')
args = parser.parse_args()
backend = args.backend_dir.resolve()
output = args.output.resolve(); output.mkdir(parents=True, exist_ok=True)
report = dict(startedAt=datetime.now(timezone.utc).isoformat(), platform=platform.platform(),
    python=platform.python_version(), executable=str(Path(sys.executable).resolve()),
    backendDirectory=str(backend), realModel=False, realMailServer=False,
    realDockerRequired=True, externalMessagesSent=0, smtpAttemptCount=0,
    fixture='Fixed public synthetic inventory allocation; not model-authored business code.',
    limitations=['Controlled model decisions do not measure live-model reasoning.',
                 'Offline MIME transport does not validate IMAP or SMTP authentication.'],
    checks=[], dockerExecutions=[], conversations=[])
service = None
ctx = None


def check(name, condition, details=None):
    report['checks'].append(dict(name=name, passed=bool(condition), **({'details':details} if details is not None else {})))
    if not condition:
        raise AssertionError(name)


def save_json(name, value):
    (output/name).write_text(json.dumps(value, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')


def sha(data):
    return hashlib.sha256(data).hexdigest()


def table(data):
    return list(csv.reader(StringIO(data.decode('utf-8-sig'), newline='')))


def build_package(no_output=False):
    """Package fixed source and existing independent examples, never the new oracle."""
    cases = []
    members = {'main.py':(FIXTURE/'main.py').read_bytes(),
               'app/__init__.py':b'', 'app/allocate.py':(FIXTURE/'app/allocate.py').read_bytes(),
               # The package protocol uses LF frontmatter, regardless of Git's checkout EOL.
               'SKILL.md':(FIXTURE/'SKILL.md').read_text(encoding='utf-8').encode('utf-8')}
    for key in ('a', 'b', 'missing-price'):
        for name in ('orders', 'stock'):
            members[f'examples/{key}/{name}.csv'] = (EXAMPLES/f'attachments/{name}-{key}.csv').read_bytes()
        case = dict(name=key, kind='failure' if key=='missing-price' else 'success',
                    inputFiles={name+'.csv':f'examples/{key}/{name}.csv' for name in ('orders', 'stock')})
        if key=='missing-price':
            case['expectedError']='MISSING_PRICE'
        else:
            members[f'examples/{key}/expected.csv']=(EXAMPLES/f'attachments/completed-{key}.csv').read_bytes()
            case['expectedFiles']={'allocation.csv':f'examples/{key}/expected.csv'}
        cases.append(case)
    manifest=dict(schemaVersion=1, title='Synthetic operations allocation' + (' missing-output negative fixture' if no_output else ''),
        kind='script', entrypoint='main.py', runtimeFiles=['app/__init__.py','app/allocate.py'],
        inputs=[dict(name='orders.csv',description='Original order rows'),dict(name='stock.csv',description='Available stock by SKU')],
        outputs=[dict(name='allocation.csv',description='Allocation and shortage values',comparison='table')],examples=cases)
    if no_output:
        # A deliberately broken package exits zero but never writes its promised file.
        members['main.py']=b'print("Synthetic negative fixture: no output written")\n'
    members['skill.json']=json.dumps(manifest,ensure_ascii=False,indent=2).encode('utf-8')
    stream=BytesIO()
    with zipfile.ZipFile(stream,'w',zipfile.ZIP_DEFLATED) as archive:
        for name,data in sorted(members.items()):
            info=zipfile.ZipInfo(name,date_time=(2026,11,22,0,0,0));info.compress_type=zipfile.ZIP_DEFLATED
            archive.writestr(info,data)
    return stream.getvalue(),members


def block_smtp(*unused, **kwargs):
    report['smtpAttemptCount']+=1
    raise AssertionError('SMTP is forbidden in this draft-only acceptance')


try:
    check('actual_windows_required',sys.platform=='win32')
    check('native_cpython_executable',platform.python_implementation()=='CPython' and Path(sys.executable).name.lower()=='python.exe')
    report['runtimeKind']='source' if args.allow_source_runtime else 'packaged'
    if args.allow_source_runtime:
        check('explicit_source_backend_only',backend==ROOT/'backend')
        report['limitations'].append('Source-runtime CI run; packaged Windows runtime has not been accepted by this run.')
    else:
        check('actual_bundled_python_required',Path(sys.executable).resolve().parent==backend.parent/'python')
    sys.path[:0]=[str(backend),str(ROOT/'backend/tests')]
    import task_backend
    from task_backend.service import BackendService
    from task_backend.agent_tools import AgentTools
    from task_backend import skill_execution as engine
    from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
    from pydantic import Field
    from test_agent_runtime import ScriptedModel,tool_call
    from network_guard import no_external_network
    check('requested_backend_imported',Path(task_backend.__file__).resolve().is_relative_to(backend))
    spec=importlib.util.spec_from_file_location('windows_skill_mime',ROOT/'scripts/scenario_mail_fixtures.py')
    mime=importlib.util.module_from_spec(spec);spec.loader.exec_module(mime)

    class Decisions(ScriptedModel):
        bindings:list=Field(default_factory=list)

        def bind_tools(self,tools,**kwargs):
            self.bindings.append([getattr(tool,'name',None) or tool.get('function',{}).get('name') for tool in tools])
            return super().bind_tools(tools,**kwargs)

        def _next(self,messages):
            value=super()._next(messages)
            response=value(messages) if callable(value) else value
            for call in response.tool_calls:
                if call['name'] not in self.bound:
                    raise AssertionError('Controlled model attempted an undisclosed tool: '+call['name'])
            return response

    def tool_result(messages,call_id):
        matches=[m for m in messages if isinstance(m,ToolMessage) and m.tool_call_id==call_id]
        if len(matches)!=1:
            raise AssertionError('Expected exactly one actual tool result: '+call_id)
        value=json.loads(matches[0].content)
        if value.get('error'):
            raise AssertionError('Actual tool failed: '+call_id+' '+str(value.get('message',value['error'])))
        return value

    def run_agent(name,text,steps,**kwargs):
        model=Decisions(script=steps)
        service.agent.model_factory=lambda config,reasoning:model
        run=service.call('agent:startTurn',dict(text=text,**kwargs))
        check(name+'_worker_executed',asyncio.run(service.agent.run_next()))
        state=service.call('agent:getRun',run['id'])
        trace={m.tool_call_id:dict(name=m.name,status=m.status,result=m.content)
               for seen in model.seen for m in seen if isinstance(m,ToolMessage)}
        save_json(name+'-run.json',dict(state=state,toolResults=trace,modelToolBindings=model.bindings))
        report['conversations'].append(dict(label=name,id=run['conversationId'],runId=run['id'],status=state['status'],toolResults=list(trace)))
        check(name+'_completed',state['status']=='completed',state.get('error'))
        return state,model

    with tempfile.TemporaryDirectory(prefix='tm-win-skill-workflow-') as temporary,ExitStack() as guards:
        directory=Path(temporary)/'用户资料 with spaces';directory.mkdir()
        docker_staging=Path(temporary)/'Docker 暂存 with spaces';docker_staging.mkdir()
        def close_current():
            global service,ctx
            if ctx is not None:ctx.close();ctx=None
            if service is not None:service.close();service=None
        guards.callback(close_current)
        guards.enter_context(no_external_network())
        guards.enter_context(patch('smtplib.SMTP',side_effect=block_smtp))
        guards.enter_context(patch('smtplib.SMTP_SSL',side_effect=block_smtp))
        guards.enter_context(patch.dict(os.environ,{'LANGSMITH_TRACING':'false','LANGCHAIN_TRACING':'false','LANGCHAIN_TRACING_V2':'false'}))
        # Exercise the actual runner's host bind mounts, not only the SQLite path.
        guards.enter_context(patch.object(tempfile,'tempdir',str(docker_staging)))
        status=engine.get_status();report['runtime']=status
        check('real_local_docker_ready',status['available'],status.get('reason'))
        endpoint=status.get('localDockerHost','')
        check('local_windows_named_pipe',bool(re.fullmatch(r'npipe:////\./pipe/[A-Za-z0-9][A-Za-z0-9_.-]{0,127}',endpoint)),endpoint)
        docker=engine._docker();prefix=[docker,'--host',endpoint]
        server=engine._control(prefix+['info','--format','{{.OSType}}'])
        check('real_linux_container_engine',server.returncode==0 and server.stdout.strip()=='linux',server.stdout.strip())
        original_capture=engine._capture
        def observed_capture(command,timeout):
            # Transparent observation of the real Docker process, not a runner mock.
            entry=dict(command=list(command),timeoutSeconds=timeout)
            report['dockerExecutions'].append(entry)
            mounts=[command[index+1] for index,value in enumerate(command) if value=='--mount']
            check('unicode_space_docker_mount_'+str(len(report['dockerExecutions'])),len(mounts)==2 and
                  all('Docker 暂存 with spaces' in mount and mount.endswith(',readonly') for mount in mounts),mounts)
            result=original_capture(command,timeout)
            code,stdout,stderr,error=result
            entry.update(processExit=code,transportError=error,dockerStderr=stderr.decode('utf-8','replace')[:1500])
            try:
                payload=json.loads(stdout)
                entry.update(skillExit=payload.get('exitCode'),outputNames=sorted(payload.get('outputs',{})),
                             skillStderr=payload.get('stderr','')[:1500])
            except (ValueError,UnicodeError):entry['invalidProtocol']=True
            return result
        guards.enter_context(patch.object(engine,'_capture',observed_capture))

        def open_service():
            value=BackendService(directory,worker=True)
            value.settings.for_request=lambda:dict(baseUrl='https://offline.example.invalid/v1',model='controlled/skill-acceptance',apiKey='not-a-real-key')
            value.agent.schedule=lambda:None
            check('production_docker_runner_selected',value.skills.runner is engine.run_python)
            return value
        service=open_service()
        sender='operations@internship.example.invalid'
        scope=dict(kind='customer',label='Synthetic operations team',emails=[sender],contextKey='')
        teaching='Import this existing allocation skill for '+sender+'. Preserve the original code. Show its test results for my approval. New order attachments use it; check results and prepare a reply draft, never send.'
        raw,members=build_package();(output/'operations-allocation-skill.zip').write_bytes(raw)
        package_file=service.files._save('operations-allocation-skill.zip',raw,'local')
        taught,teacher=run_agent('01-import',teaching,[
            tool_call('read_file',dict(file_path='/skills/builtin/learning/SKILL.md'),'load-import-workflow'),
            tool_call('import_skill',dict(file_id=package_file['id'],scope=scope,instruction='Keep replies concise. Do not promise a delivery date.'),'import-fixed'),
            AIMessage(content='The imported code has been tested. Please review and approve the pending skill.')],fileIds=[package_file['id']])
        check('official_import_progressive_disclosure','import_skill' not in teacher.bindings[0] and
              any('import_skill' in names for names in teacher.bindings[1:]))
        skill_id=next(a['id'] for a in taught['artifacts'] if a['kind']=='skill_draft')
        skill=service.skills.get(skill_id);save_json('import-review.json',skill)
        check('two_success_one_failure_real_docker',skill['canApprove'] and len(skill['validation']['cases'])==3
              and all(c['passed'] and c['backend']=='docker' for c in skill['validation']['cases']),skill['validation'])
        check('import_is_pending_not_authorized',not skill['canRun'] and skill['status']!='approved')
        imported=directory/skill['packagePath']/'imported'
        check('imported_source_byte_exact',all((imported/name).read_bytes()==data for name,data in members.items()))
        check('held_out_oracle_not_in_package',sha((EXAMPLES/'held-out-oracle.csv').read_bytes()) not in {sha(v) for v in members.values()})
        calls=len(report['dockerExecutions'])
        try:service.skills.run(skill_id,{},skill['scopeId'])
        except ValueError:rejected=True
        else:rejected=False
        check('unapproved_execution_rejected_before_docker',rejected and len(report['dockerExecutions'])==calls)
        approved=service.call('skill:approve',dict(id=skill_id,expectedHash=skill['versionHash']))
        check('separate_user_approval_enables_skill',approved['canRun'])

        messages=[]
        for key,order,stock,body in [
            ('new-order','orders-new.csv','stock-new.csv','Please allocate the attached order against the current stock snapshot. Return the shortage sheet for review; do not promise shipment.'),
            ('missing-price','orders-missing-price.csv','stock-missing-price.csv','Here is another order. Please use the same procedure. I may have missed a value in the pricing column.')]:
            messages.append(dict(messageKey=key,**{'from':dict(name='Operations colleague',address=sender)},
                to=[dict(name='Intern',address='intern@example.invalid')],subject='Allocation request '+key,
                sentAt='2026-11-22T09:00:00+08:00',messageId='<'+key+'@internship.example.invalid>',body=body,
                attachments=[dict(name=order),dict(name=stock)]))
        scenario=dict(scenarioId='windows-skill-acceptance',metadata=dict(fictional=True),messages=messages)
        seeded=mime.seed(service,scenario,EXAMPLES,register_binding=True,import_attachments=False)
        mime.write_emls(scenario,EXAMPLES,output=output/'messages')
        mail_id=seeded['mailIds']['new-order']
        def found(messages):return tool_result(messages,'discover')['skills'][0]
        def inputs(messages):return {'orders.csv':tool_result(messages,'download-orders')['file']['id'],'stock.csv':tool_result(messages,'download-stock')['file']['id']}
        def generated(messages):return tool_result(messages,'execute-existing')['files'][0]['id']
        with mime.fixture_transport(service,seeded):
            fresh,learner=run_agent('02-new-conversation','Please process this new allocation request using the approved procedure. Check the output and attach it to a reply draft for my review.',[
                tool_call('read_mail',dict(message_id=mail_id),'read-new-mail'),
                tool_call('import_attachment',dict(message_id=mail_id,index=0),'download-orders'),
                tool_call('import_attachment',dict(message_id=mail_id,index=1),'download-stock'),
                tool_call('search_skills',dict(sender=sender),'discover'),
                lambda m:tool_call('read_file',dict(file_path='/skills/approved/'+found(m)['id']+'/SKILL.md'),'read-skill-markdown'),
                lambda m:tool_call('get_skill',dict(id=found(m)['id']),'load-approved'),
                lambda m:tool_call('run_skill',dict(version_id=found(m)['id'],scope_id=found(m)['scopeId'],inputs=inputs(m),source_mail_id=mail_id),'execute-existing'),
                lambda m:tool_call('validate_file',dict(file_id=generated(m)),'check-generated'),
                lambda m:tool_call('create_mail_draft',dict(subject='Re: Allocation request new-order',body='Please review the attached allocation and shortage sheet. Shipment timing remains to be confirmed.',reply_to_local_id=mail_id,attachment_ids=[generated(m)]),'draft-reply'),
                lambda m:tool_call('validate_mail_draft',dict(draft_id=tool_result(m,'draft-reply')['id'],expected_attachment_ids=[generated(m)]),'check-draft'),
                AIMessage(content='The allocation has been checked and attached to a reply draft. Nothing has been sent.')],mailIds=[mail_id])
            check('fresh_conversation_has_no_teaching_transcript',fresh['conversationId']!=taught['conversationId'] and
                  not any(isinstance(m,HumanMessage) and m.content==teaching for m in learner.seen[0]))
            history=learner.seen[-1]
            check('official_reuse_progressive_disclosure','run_skill' not in learner.bindings[0] and
                  any('run_skill' in names for names in learner.bindings[1:]))
            check('model_cannot_approve_or_send',all(not any('approve' in name or name.startswith('send') for name in names)
                  for names in teacher.bindings+learner.bindings))
            result=tool_result(history,'execute-existing');file=result['files'][0];file_id=file['id']
            actual=service.files.bytes(file_id);expected=(EXAMPLES/'held-out-oracle.csv').read_bytes()
            (output/'actual-allocation.csv').write_bytes(actual);(output/'independent-oracle.csv').write_bytes(expected)
            check('held_out_output_matches_independent_oracle',table(actual)==table(expected),table(actual))
            check('agent_checked_generated_csv',tool_result(history,'check-generated')['structuralPassed'])
            check('actual_docker_execution_cleanup',result['execution']['backend']=='docker' and result['execution']['exitCode']==0
                  and result['execution']['imageId']==status['imageId'] and result['execution']['cleanupConfirmed'],result['execution'])
            check('output_has_approved_version_and_mail_provenance',file['skill']['versionHash']==approved['versionHash'] and
                  all(p['originalMailIds']==[mail_id] for p in result['audit']['inputProvenance']))
            check('source_attachments_unchanged',all(service.files.bytes(fid)==(EXAMPLES/'attachments'/name).read_bytes()
                for fid,name in [(inputs(history)['orders.csv'],'orders-new.csv'),(inputs(history)['stock.csv'],'stock-new.csv')]))
            draft=tool_result(history,'draft-reply');delivery=tool_result(history,'check-draft')
            check('exact_output_in_reply_draft',delivery['passed'] and delivery['actualAttachmentIds']==[file_id]
                  and draft['attachmentIds']==[file_id] and draft['to']==sender and draft['replyToLocalId']==mail_id,delivery)
            check('draft_attachment_hash_matches_output',draft['attachments'][0]['sha256']==sha(actual))
            report['result']=dict(skillId=skill_id,versionHash=approved['versionHash'],fileId=file_id,
                outputSha256=sha(actual),draftId=draft['id'],inputHashes=result['audit']['inputHashes'])

            before_ids={f['id'] for f in service.files.list_files()};before_calls=len(report['dockerExecutions'])
            bad_id=seeded['mailIds']['missing-price']
            failed,bad_model=run_agent('03-invalid-new-mail','Use the same approved procedure for this order. If the input is incomplete, tell me what is missing.',[
                tool_call('read_mail',dict(message_id=bad_id),'read-bad-mail'),
                tool_call('import_attachment',dict(message_id=bad_id,index=0),'bad-orders'),
                tool_call('import_attachment',dict(message_id=bad_id,index=1),'bad-stock'),
                tool_call('read_file',dict(file_path='/skills/approved/'+skill_id+'/SKILL.md'),'bad-read-skill-markdown'),
                tool_call('get_skill',dict(id=skill_id),'bad-load-skill'),
                lambda m:tool_call('run_skill',dict(version_id=skill_id,scope_id=approved['scopeId'],source_mail_id=bad_id,
                    inputs={'orders.csv':tool_result(m,'bad-orders')['file']['id'],'stock.csv':tool_result(m,'bad-stock')['file']['id']}),'reject-bad-input'),
                AIMessage(content='A unit price is missing. Please provide it; I have not created an output file or reply draft.')],mailIds=[bad_id])
            execution=report['dockerExecutions'][-1]
            check('invalid_input_rejected_by_real_container',len(report['dockerExecutions'])==before_calls+1 and
                  execution.get('skillExit')==2 and 'MISSING_PRICE' in execution.get('skillStderr','') and execution.get('outputNames')==[],execution)
            added=[f for f in service.files.list_files() if f['id'] not in before_ids]
            check('invalid_input_no_generated_files_or_drafts',all(f['source']=='mail' for f in added) and failed['artifacts']==[])

        broken_raw,_=build_package(no_output=True)
        broken_file=service.files._save('missing-output-skill.zip',broken_raw,'local')
        broken=service.call('skill:importPackage',dict(fileId=broken_file['id'],scope=scope))
        save_json('missing-output-review.json',broken)
        check('zero_exit_without_outputs_cannot_pass_review',not broken['canApprove'] and not broken['canRun'] and
              all(c.get('backend')=='docker' for c in broken['validation']['cases']) and
              all(not c['passed'] for c in broken['validation']['cases']),broken['validation'])
        try:service.call('skill:approve',dict(id=broken['id'],expectedHash=broken['versionHash']))
        except ValueError:blocked=True
        else:blocked=False
        check('missing_output_package_cannot_be_approved',blocked)
        check('no_smtp_or_send_receipts',report['smtpAttemptCount']==0 and service.compose.list_recent()==[])
        service.close();service=None
        service=open_service();ctx=AgentTools(service,'restart-check',fresh['conversationId'])
        check('approved_skill_survives_restart',service.skills.get(skill_id)['canRun'] and service.skills.get(skill_id)['versionHash']==approved['versionHash'])
        check('sender_discovery_survives_restart',skill_id in [s['id'] for s in service.skills.search(sender=sender)])
        check('output_and_reply_survive_restart',service.files.bytes(file_id)==actual and ctx.validate_mail_draft(draft['id'],[file_id])['passed'])
        check('conversation_history_survives_restart',len(service.call('agent:getConversation',fresh['conversationId'])['turns'])==2)
        check('restart_did_not_execute_skill_again',len(report['dockerExecutions'])==8)
        names=[e['command'][e['command'].index('--name')+1] for e in report['dockerExecutions']]
        remaining=engine._control(prefix+['ps','-a','--format','{{.Names}}'])
        check('all_own_containers_removed',remaining.returncode==0 and not set(names)&set(remaining.stdout.splitlines()))
        check('all_executions_offline_readonly_linux',len(names)==8 and all('--network' in e['command'] and
            e['command'][e['command'].index('--network')+1]=='none' and '--read-only' in e['command'] and
            e['command'][1:3]==['--host',endpoint] and e['command'][-1]==status['imageId'] for e in report['dockerExecutions']))
        close_current()
except Exception as error:
    report['error']=str(error);report['traceback']=traceback.format_exc()
finally:
    try:
        if ctx is not None:ctx.close()
        if service is not None:service.close()
    except Exception as error:
        report['cleanupError']=str(error)
    report['finishedAt']=datetime.now(timezone.utc).isoformat()
    report['passed']=bool(report['checks']) and all(v['passed'] for v in report['checks']) and 'error' not in report and 'cleanupError' not in report
    save_json('skill-workflow.json',report)
print(json.dumps(dict(passed=report['passed'],checks=len(report['checks']),report=str(output/'skill-workflow.json')),ensure_ascii=False))
raise SystemExit(0 if report['passed'] else 1)
