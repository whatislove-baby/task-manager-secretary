"""Real persistence and review gates; execution is isolated behind a test runner.

These tests do not claim Docker/live-model verification. Real runner coverage is
in test_skill_execution and the end-to-end API acceptance.
"""
from copy import deepcopy
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from uuid import uuid4
import json
import os
import signal
import subprocess
import sys
import unittest
from unittest.mock import patch
from zipfile import ZipFile

from task_backend.learning import LearningService
from task_backend.mail_files import MailFiles
from task_backend.skill_library import SkillLibraryService
from task_backend.skill_schema import SkillProposal
from task_backend.agent_tools import AgentTools
from task_backend.bundle_files import extract_archive

SCOPE={'kind':'customer','label':'Harbor','emails':['orders@harbor.example'],'contextKey':''}


class Runner:
    def __init__(self):self.calls=[];self.crash=False;self.extra=False;self.wrong=False
    def __call__(self,source,inputs):
        self.calls.append((source,deepcopy(inputs)))
        value=inputs['order.csv'].decode().splitlines()[1].strip('"')
        if self.crash:return dict(ok=False,exitCode=1,outputs={},stdout='',stderr='Missing price\nSyntaxError',backend='unit-test-double')
        if not value:return dict(ok=False,exitCode=2,outputs={},stdout='',stderr='Missing price',backend='unit-test-double')
        result=('amount\n'+str(int(value)* (3 if self.wrong else 2))+'\n').encode()
        return dict(ok=True,exitCode=0,outputs={'result.csv':result,**({'extra.txt':b'extra'} if self.extra else {})},stdout='',stderr='',backend='unit-test-double')


class FailingDatabase:
    """Fail a single storage boundary without replacing SQLite semantics."""
    def __init__(self,db,boundary):self.db=db;self.boundary=boundary;self.failed=False;self.saw_run=False
    def __getattr__(self,name):return getattr(self.db,name)
    def execute(self,sql,*args,**kwargs):
        if sql.startswith('INSERT INTO learning_runs'):self.saw_run=True
        fail=(self.boundary=='metadata' and sql.startswith('UPDATE files SET record=')) or (self.boundary=='run_insert' and sql.startswith('INSERT INTO learning_runs')) or (self.boundary=='run_commit' and self.saw_run and sql=='COMMIT')
        if fail and not self.failed:
            self.failed=True;raise OSError('synthetic '+self.boundary+' storage interruption')
        return self.db.execute(sql,*args,**kwargs)


class SkillStories(unittest.TestCase):
    def setUp(self):
        self.temp=TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.root=Path(self.temp.name)
        self.mails={'a':dict(fromAddress='orders@harbor.example',subject='Order',body='Process my order'),
                    'b':dict(fromAddress='orders@ridge.example',subject='Order',body='Different customer'),
                    'internal':dict(fromAddress='ops@supplier.example',subject='Internal example',body='Completed reference')}
        self.s=SimpleNamespace(directory=self.root,mail=SimpleNamespace(store=SimpleNamespace(get_local=lambda id:deepcopy(self.mails[id]))))
        self.s.files=MailFiles(self.s);self.addCleanup(self.s.files.close)
        self.s.learning=LearningService(self.s);self.addCleanup(self.s.learning.close)
        self.runner=Runner();self.s.skills=SkillLibraryService(self.s,self.runner);self.addCleanup(lambda:self.s.skills.close())
        self.a=self.file('a.csv',b'price\n2\n',mail='a');self.b=self.file('b.csv',b'price\n4\n',mail='a')
        self.c=self.file('c.csv',b'price\n""\n',mail='a')
        self.x=self.file('x.csv',b'amount\n4\n');self.y=self.file('y.csv',b'amount\n8\n')

    def file(self,name,data,mail=None,source=None):
        return self.s.files._save(name,data,source or ('mail' if mail else 'local'),source_mail=mail)

    def proposal(self):
        return dict(title='Harbor double amount',kind='hybrid',scope=deepcopy(SCOPE),rationale='Repeated exact processing plus customer communication guidance',instruction='For Harbor only: double the price. Missing prices must be clarified. Reply briefly.',code='print("only a mock runner sees this in unit tests")',sources=[],
            inputs=[dict(name='order.csv',description='Customer price table')],outputs=[dict(name='result.csv',description='Computed amount',comparison='table')],examples=[
                dict(name='first',kind='success',inputFiles={'order.csv':self.a['id']},expectedFiles={'result.csv':self.x['id']}),
                dict(name='second',kind='success',inputFiles={'order.csv':self.b['id']},expectedFiles={'result.csv':self.y['id']}),
                dict(name='missing price',kind='failure',inputFiles={'order.csv':self.c['id']},expectedError='Missing price')])

    def approve(self,p):return self.s.skills.approve(dict(id=p['id'],expectedHash=p['versionHash']))

    def test_memory_is_actual_markdown_and_survives_reopen(self):
        p=self.s.skills.propose(dict(title='Reply preference',kind='memory',scope=SCOPE,rationale='Language and tone are flexible guidance',instruction='Use concise English. Explain missing information first.',sources=[dict(kind='mail',id='a')]))
        self.assertTrue(p['canApprove']);self.assertFalse(p['canRun']);self.assertEqual(p['validation']['status'],'manual_review_required')
        package=self.root/p['packagePath'];self.assertTrue((package/'SKILL.md').read_text().startswith('---\nname:'))
        self.assertIn('Use concise English',(package/'SKILL.md').read_text());self.assertFalse((package/'main.py').exists())
        self.approve(p);self.s.skills.close();self.s.skills=SkillLibraryService(self.s,self.runner)
        result=self.s.skills.search(sender='orders@harbor.example');self.assertEqual(result[0]['id'],p['id'])
        self.assertFalse(result[0]['canRun']);self.assertEqual(self.runner.calls,[])
        self.assertEqual(self.s.learning.list(),[])

    def test_teach_validate_approve_run_then_replay(self):
        p=self.s.skills.propose(self.proposal());self.assertTrue(p['canApprove']);self.assertEqual(len(self.runner.calls),3)
        self.assertTrue(p['validation']['passed']);self.assertEqual([c['kind'] for c in p['validation']['cases']],['success','success','failure'])
        self.assertTrue((self.root/p['packagePath']/'examples/0/expected/result.csv').exists())
        self.assertFalse(p['canRun']);self.approve(p)
        fresh=self.file('fresh.csv',b'price\n7\n',mail='a')
        output=self.s.skills.run(p['id'],{'order.csv':fresh['id']},p['scopeId'])
        self.assertEqual(self.s.files.bytes(output['files'][0]['id']),b'amount\n14\n')
        self.assertEqual(output['files'][0]['skill']['approvedVersion'],1)
        again=self.s.skills.run(p['id'],{'order.csv':fresh['id']},p['scopeId'])
        self.assertTrue(again['replayed']);self.assertEqual(len(self.runner.calls),4)
        self.assertEqual(again['files'][0]['id'],output['files'][0]['id'])

    def test_no_test_answers_are_given_to_the_runner(self):
        self.s.skills.propose(self.proposal())
        self.assertTrue(all(set(inputs)=={'order.csv'} for _,inputs in self.runner.calls))
        self.assertTrue(all(b'amount' not in value for _,inputs in self.runner.calls for value in inputs.values()))

    def test_insufficient_or_duplicate_cases_are_not_publishable(self):
        raw=self.proposal();raw['examples']=raw['examples'][:1]
        p=self.s.skills.propose(raw);self.assertFalse(p['canApprove']);self.assertEqual(p['validation']['status'],'needs_examples')
        with self.assertRaisesRegex(ValueError,'样例'):self.approve(p)
        self.assertEqual(self.runner.calls,[])
        raw=self.proposal();raw['parentId']=p['id'];raw['examples'][1]=deepcopy(raw['examples'][0])
        p2=self.s.skills.propose(raw);self.assertFalse(p2['canApprove'])

    def test_bad_script_draft_can_be_corrected_without_overwriting_original(self):
        self.runner.wrong=True;p=self.s.skills.propose(self.proposal())
        self.assertFalse(p['validation']['passed']);self.assertFalse(p['canApprove'])
        self.assertIn("实际 '6'；样例 '4'",p['validation']['cases'][0]['detail'])
        self.assertEqual((self.root/p['packagePath']/'tests/0/actual/result.csv').read_bytes(),b'amount\n6\n')
        raw=self.proposal();raw['parentId']=p['id'];raw['code']='print("fixed mock")';self.runner.wrong=False
        fixed=self.s.skills.propose(raw);self.assertTrue(fixed['canApprove']);self.approve(fixed)
        self.assertEqual(self.s.skills.get(p['id'])['status'],'superseded')
        self.assertEqual(self.s.skills.get(p['id'])['versionHash'],p['versionHash'])
        self.assertFalse(self.s.skills.get(p['id'])['canApprove'])
        self.assertEqual(self.s.skills.search(status='draft'),[])

    def test_an_ordinary_crash_is_not_a_successful_failure_test(self):
        self.runner.crash=True;p=self.s.skills.propose(self.proposal())
        self.assertFalse(p['canApprove']);self.assertFalse(p['validation']['cases'][-1]['passed'])

    def test_unexpected_output_files_fail_validation_and_runtime(self):
        self.runner.extra=True;p=self.s.skills.propose(self.proposal());self.assertFalse(p['canApprove'])
        self.runner.extra=False;raw=self.proposal();raw['parentId']=p['id'];fixed=self.approve(self.s.skills.propose(raw))
        self.runner.extra=True;before=len(self.s.files.list_files())
        with self.assertRaisesRegex(ValueError,'输出契约'):self.s.skills.run(fixed['id'],{'order.csv':self.a['id']},fixed['scopeId'])
        self.assertEqual(len(self.s.files.list_files()),before)

    def test_agent_generated_expected_file_cannot_self_certify(self):
        fake=self.file('fake.csv',b'amount\n4\n',source='generated');raw=self.proposal();raw['examples'][0]['expectedFiles']['result.csv']=fake['id']
        with self.assertRaisesRegex(ValueError,'自己的正确答案'):self.s.skills.propose(raw)
        self.assertEqual(self.runner.calls,[])

    def test_expected_file_cannot_be_an_input_of_any_example(self):
        raw=self.proposal();raw['examples'][1]['inputFiles']['order.csv']=self.x['id']
        with self.assertRaisesRegex(ValueError,'同时作为任何测试输入'):self.s.skills.propose(raw)
        self.assertEqual(self.runner.calls,[])

    def test_reimported_expected_bytes_cannot_be_any_example_input(self):
        copied=self.file('renamed-oracle.csv',self.s.files.bytes(self.x['id']));raw=self.proposal()
        raw['examples'][1]['inputFiles']['order.csv']=copied['id']
        with self.assertRaisesRegex(ValueError,'相同字节'):self.s.skills.propose(raw)
        self.assertEqual(self.runner.calls,[])

    def reference_archive(self,source,mail=None):
        data=BytesIO()
        with ZipFile(data,'w') as package:
            package.writestr('x.csv',b'amount\n4\n');package.writestr('y.csv',b'amount\n8\n')
        root=self.s.files._save('completed.zip',data.getvalue(),source,source_mail=mail)
        return root,extract_archive(self.s.files,root['id'],['x.csv','y.csv'])

    def test_generated_archive_cannot_launder_its_member_into_an_oracle(self):
        _,members=self.reference_archive('generated');raw=self.proposal()
        raw['examples'][0]['expectedFiles']['result.csv']=members[0]['id']
        with self.assertRaisesRegex(ValueError,'自己的正确答案'):self.s.skills.propose(raw)
        self.assertEqual(self.runner.calls,[])

    def test_user_uploaded_and_mail_archive_members_are_valid_references(self):
        for source,mail in (('local',None),('mail','internal')):
            with self.subTest(source=source):
                _,members=self.reference_archive(source,mail);raw=self.proposal();raw['title']+=' '+source
                for case,member in zip(raw['examples'],members):case['expectedFiles']['result.csv']=member['id']
                p=self.s.skills.propose(raw);self.assertTrue(p['canApprove'])

    def test_reference_provenance_checks_archive_origin_even_without_parent_ids(self):
        _,members=self.reference_archive('generated');member=members[0];member['parentIds']=[]
        self.s.files.db.execute('UPDATE files SET record=? WHERE id=?',(json.dumps(member),member['id']))
        with self.assertRaisesRegex(ValueError,'自己的正确答案'):self.s.skills._verified_reference(member['id'])
        self.assertEqual(self.runner.calls,[])

    def test_reference_provenance_cycles_and_depth_fail_closed(self):
        record=self.file('cyclic.csv',b'amount\n4\n');record['parentIds']=[record['id']]
        self.s.files.db.execute('UPDATE files SET record=? WHERE id=?',(json.dumps(record),record['id']))
        with self.assertRaisesRegex(ValueError,'循环'):self.s.skills._verified_reference(record['id'])
        parent=None
        for index in range(61):
            parent=self.s.files._save(str(index)+'.txt',b'reference','local',parents=[parent['id']] if parent else [])
        with self.assertRaisesRegex(ValueError,'过深|过长'):self.s.skills._verified_reference(parent['id'])

    def test_oracle_container_and_its_reimport_are_rejected_but_extracted_siblings_work(self):
        data=BytesIO()
        with ZipFile(data,'w') as package:
            package.writestr('raw.csv',b'price\n2\n');package.writestr('completed.csv',b'amount\n4\n')
        container=self.file('teaching.zip',data.getvalue());raw_input,expected=extract_archive(self.s.files,container['id'],['raw.csv','completed.csv'])
        duplicate=self.file('renamed-bundle.zip',data.getvalue())
        for leaked in (container,duplicate):
            with self.subTest(inputId=leaked['id']):
                value=self.proposal();value['examples'][0]['inputFiles']['order.csv']=leaked['id'];value['examples'][0]['expectedFiles']['result.csv']=expected['id']
                with self.assertRaisesRegex(ValueError,'来源容器或相同副本'):self.s.skills.propose(value)
                self.assertEqual(self.runner.calls,[])
        value=self.proposal();value['examples'][0]['inputFiles']['order.csv']=raw_input['id'];value['examples'][0]['expectedFiles']['result.csv']=expected['id']
        result=self.s.skills.propose(value);self.assertTrue(result['canApprove']);self.assertEqual(len(self.runner.calls),3)

    def test_reference_colleague_mail_is_preserved_without_changing_scope(self):
        self.x=self.file('internal.csv',b'amount\n4\n',mail='internal');raw=self.proposal();raw['sources']=[dict(kind='mail',id='internal')]
        p=self.approve(self.s.skills.propose(raw))
        self.assertEqual(p['scope']['emails'],SCOPE['emails'])
        source=next(s for s in p['proposal']['sources'] if s['kind']=='mail')
        self.assertEqual(source['role'],'reference-output');self.assertEqual(source['fromAddress'],'ops@supplier.example')
        output=self.s.skills.run(p['id'],{'order.csv':self.a['id']},p['scopeId'])
        self.assertEqual(self.s.skills._file_mails(output['files'][0]['id']),{'a'})

    def test_cross_customer_and_unrelated_reference_mail_rejected(self):
        raw=self.proposal();raw['sources']=[dict(kind='mail',id='b')]
        with self.assertRaisesRegex(ValueError,'跨客户'):self.s.skills.propose(raw)
        p=self.approve(self.s.skills.propose(self.proposal()));other=self.file('other.csv',b'price\n9\n',mail='b')
        with self.assertRaisesRegex(ValueError,'跨客户'):self.s.skills.run(p['id'],{'order.csv':other['id']},p['scopeId'])
        with self.assertRaisesRegex(ValueError,'范围'):self.s.skills.run(p['id'],{'order.csv':self.a['id']},'wrong')

    def test_tampered_package_is_not_executable_or_approvable(self):
        p=self.s.skills.propose(self.proposal());path=self.root/p['packagePath']/'main.py';path.write_text('changed')
        with self.assertRaisesRegex(ValueError,'校验'):self.approve(p)
        with self.assertRaisesRegex(ValueError,'校验'):self.s.skills.get(p['id'])

    def test_revocation_blocks_even_cached_execution(self):
        p=self.approve(self.s.skills.propose(self.proposal()));self.s.skills.run(p['id'],{'order.csv':self.a['id']},p['scopeId'])
        self.s.skills.disable(dict(id=p['id'],expectedHash=p['versionHash']))
        with self.assertRaisesRegex(ValueError,'已批准'):self.s.skills.run(p['id'],{'order.csv':self.a['id']},p['scopeId'])

    def test_approval_hash_and_old_window_cannot_override_newer_version(self):
        p=self.s.skills.propose(self.proposal());raw=self.proposal();raw['parentId']=p['id'];fixed=self.s.skills.propose(raw)
        with self.assertRaisesRegex(ValueError,'变化'):self.s.skills.approve(dict(id=p['id'],expectedHash='0'*64))
        self.approve(fixed)
        with self.assertRaises(ValueError):self.approve(p)
        self.assertEqual(self.s.skills.search()[0]['id'],fixed['id'])

    def test_schema_refuses_server_metadata_and_paths(self):
        raw=self.proposal();raw['reviewBase']={}
        with self.assertRaises(ValueError):SkillProposal.model_validate(raw)
        raw=self.proposal();raw['inputs'][0]['name']='../secrets'
        with self.assertRaises(ValueError):SkillProposal.model_validate(raw)
        raw=self.proposal();raw['kind']='memory'
        with self.assertRaises(ValueError):SkillProposal.model_validate(raw)

    def test_same_proposal_id_reuses_package_and_never_reexecutes(self):
        id=str(uuid4());p=self.s.skills.propose(self.proposal(),proposal_id=id)
        again=self.s.skills.propose(self.proposal(),proposal_id=id);self.assertEqual(again['versionHash'],p['versionHash']);self.assertEqual(len(self.runner.calls),3)
        raw=self.proposal();raw['code']='different'
        with self.assertRaisesRegex(ValueError,'标识'):self.s.skills.propose(raw,proposal_id=id)

    def test_package_symlink_and_unknown_file_are_rejected(self):
        p=self.s.skills.propose(self.proposal());path=self.root/p['packagePath']/'unknown.py';path.write_text('extra')
        with self.assertRaisesRegex(ValueError,'清单'):self.s.skills.get(p['id'])
        path.unlink();path.symlink_to('/etc/hosts')
        with self.assertRaisesRegex(ValueError,'链接'):self.s.skills.get(p['id'])

    def test_agent_captures_original_user_teaching_and_replays_current_status(self):
        conversation={'turns':[dict(id='user-original',runId='teach-run',role='user',content='Harbor always doubles the price; reject missing price. Save this for later.',createdAt='2026-11-21T01:00:00Z'),dict(id='assistant-text',runId='teach-run',role='assistant',content='Model paraphrase must not become teaching.')]}
        self.s.agent=SimpleNamespace(_conversation=lambda id:deepcopy(conversation))
        events=[];agent=AgentTools(self.s,'teach-run','conversation-a',events.append);self.addCleanup(agent.close)
        value=self.proposal();p=agent.propose_skill(value,call_id='skill-call')
        self.assertEqual([t['content'] for t in p['proposal']['teaching']['turns']],[conversation['turns'][0]['content']])
        conversation['turns'].append(dict(id='later',runId='teach-run',role='user',content='A later clarification must not rewrite the earlier evidence.',createdAt='2026-11-21T01:01:00Z'))
        self.approve(p);replayed=agent.propose_skill(value,call_id='skill-call')
        self.assertEqual(replayed['proposal']['teaching'],p['proposal']['teaching']);self.assertEqual(replayed['status'],'approved')
        self.assertEqual(len(self.runner.calls),3)
        newer=AgentTools(self.s,'new-run','fresh-conversation',events.append);self.addCleanup(newer.close)
        found=newer.search_skills(sender=SCOPE['emails'][0]);self.assertEqual(found['count'],1)
        self.assertNotIn('code',found['skills'][0]);self.assertEqual(newer.get_skill(p['id'])['versionHash'],p['versionHash'])
        fresh=self.file('fresh.csv',b'price\n7\n',mail='a')
        result=newer.run_skill(p['id'],{'order.csv':fresh['id']},p['scopeId'],call_id='new-call')
        self.assertEqual(self.s.files.bytes(result['files'][0]['id']),b'amount\n14\n')
        self.assertTrue(any(e.get('artifact',{}).get('kind')=='file' for e in events))

    def test_process_death_before_or_after_rename_recovers_without_overwrite(self):
        child='''import json,os,signal,sys
from types import SimpleNamespace
from task_backend.skill_library import SkillLibraryService
args=json.loads(sys.stdin.read()); service=SimpleNamespace(directory=args['directory'])
skills=SkillLibraryService(service); original=os.rename
def interrupted(source,target):
    if args['after']: original(source,target)
    os.kill(os.getpid(),signal.SIGTERM)
os.rename=interrupted
skills.propose(args['value'],proposal_id=args['id'])
'''
        for after in (False,True):
            with self.subTest(afterRename=after):
                id=str(uuid4());value=dict(title='Crash recovery '+str(after),kind='memory',scope=SCOPE,rationale='Reusable tone',instruction='Reply politely and ask for missing data.')
                result=subprocess.run([sys.executable,'-c',child],input=json.dumps(dict(directory=str(self.root),after=after,id=id,value=value)),text=True,capture_output=True,timeout=20)
                # Windows os.kill(SIGTERM) uses TerminateProcess(SIGTERM),
                # whereas POSIX subprocess exposes a negative signal number.
                expected_exit=signal.SIGTERM if os.name=='nt' else -signal.SIGTERM
                self.assertEqual(result.returncode,expected_exit,result.stderr)
                self.assertEqual(self.s.skills.db.execute('SELECT count(*) FROM skill_package_journal WHERE id=?',(id,)).fetchone()[0],1)
                self.assertIsNone(self.s.skills.db.execute('SELECT id FROM learning_versions WHERE id=?',(id,)).fetchone())
                p=self.s.skills.propose(value,proposal_id=id);self.assertEqual(p['status'],'draft');self.assertTrue(p['canApprove'])
                again=self.s.skills.propose(value,proposal_id=id);self.assertEqual(p['versionHash'],again['versionHash'])
                self.assertEqual(self.s.skills.db.execute('SELECT count(*) FROM skill_package_journal').fetchone()[0],0)
                self.assertEqual(list(self.s.skills.packages.glob('.staging-*')),[])

    def test_unavailable_runtime_stops_remaining_cases_and_saves_unapprovable_draft(self):
        calls=[]
        def unavailable(code,inputs):
            calls.append(inputs);return dict(ok=False,exitCode=None,outputs={},stdout='',stderr='',error='Docker not running',backend='docker')
        self.s.skills.runner=unavailable;p=self.s.skills.propose(self.proposal())
        self.assertEqual(len(calls),1);self.assertFalse(p['canApprove'])
        self.assertTrue(all(case.get('skipped') for case in p['validation']['cases'][1:]))

    def multi_output_skill(self):
        original=self.runner
        def changing_bytes(code,inputs):
            result=original(code,inputs)
            if result['ok']:
                # Same table content; different valid bytes on every invocation,
                # as with PDF/XLSX timestamps. A replay must not regenerate it.
                if len(original.calls)%2==0:result['outputs']['result.csv']=result['outputs']['result.csv'].replace(b'\n',b'\r\n')
                result['outputs']['summary.txt']=b'Ready\n'
            return result
        self.s.skills.runner=changing_bytes
        value=self.proposal();value['outputs'].append(dict(name='summary.txt',description='Summary',comparison='text'))
        summary=self.file('summary.txt',b'Ready\n')
        for case in value['examples']:
            if case['kind']=='success':case['expectedFiles']['summary.txt']=summary['id']
        return self.approve(self.s.skills.propose(value))

    def recover_run_boundary(self,boundary):
        version=self.multi_output_skill();fresh=self.file('new.csv',b'price\n7\n',mail='a');run_id=str(uuid4())
        save=self.s.files._save;before=len(self.s.files.list_files())
        def second_file_failure(name,*args,**kwargs):
            if name=='summary.txt':raise OSError('synthetic second file interruption')
            return save(name,*args,**kwargs)
        if boundary=='second_file':guard=patch.object(self.s.files,'_save',side_effect=second_file_failure)
        elif boundary=='metadata':guard=patch.object(self.s.files,'db',FailingDatabase(self.s.files.db,boundary))
        else:guard=patch.object(self.s.skills,'db',FailingDatabase(self.s.skills.db,boundary))
        with guard:
            with self.assertRaisesRegex(OSError,'synthetic'):
                self.s.skills.run(version['id'],{'order.csv':fresh['id']},version['scopeId'],run_id=run_id)
        self.assertEqual(self.s.skills.db.execute('SELECT count(*) FROM learning_runs').fetchone()[0],0)
        self.assertEqual(self.s.skills.db.execute('SELECT count(*) FROM skill_run_journal').fetchone()[0],1)
        frozen={r['name']:r['data'] for r in self.s.skills.db.execute('SELECT name,data FROM skill_run_outputs')}
        self.assertEqual(len(frozen),2);self.assertGreater(len(self.s.files.list_files()),before)
        self.assertEqual(len(self.runner.calls),4)
        runner=self.s.skills.runner;self.s.skills.close();self.s.skills=SkillLibraryService(self.s,runner)
        recovered=self.s.skills.run(version['id'],{'order.csv':fresh['id']},version['scopeId'],run_id=run_id)
        self.assertTrue(recovered['recovered']);self.assertEqual(len(self.runner.calls),4)
        self.assertEqual({f['name']:self.s.files.bytes(f['id']) for f in recovered['files']},frozen)
        self.assertTrue(all(f['skill']['versionHash']==version['versionHash'] for f in recovered['files']))
        self.assertEqual(len(self.s.files.list_files()),before+2)
        self.assertEqual(self.s.skills.db.execute('SELECT count(*) FROM learning_runs').fetchone()[0],1)
        self.assertEqual(self.s.skills.db.execute('SELECT count(*) FROM skill_run_journal').fetchone()[0],0)
        self.assertEqual(self.s.skills.db.execute('SELECT count(*) FROM skill_run_outputs').fetchone()[0],0)
        self.assertTrue(self.s.skills.run(version['id'],{'order.csv':fresh['id']},version['scopeId'],run_id=run_id)['replayed'])
        self.assertEqual(len(self.runner.calls),4)

    def test_second_file_failure_replays_frozen_nondeterministic_outputs(self):self.recover_run_boundary('second_file')
    def test_lineage_write_failure_replays_frozen_outputs(self):self.recover_run_boundary('metadata')
    def test_run_insert_failure_replays_frozen_outputs(self):self.recover_run_boundary('run_insert')
    def test_run_commit_failure_replays_frozen_outputs(self):self.recover_run_boundary('run_commit')

    def test_pending_run_rechecks_input_identity_and_current_approval(self):
        version=self.multi_output_skill();run_id=str(uuid4())
        with patch.object(self.s.files,'_save',side_effect=OSError('storage failure')):
            with self.assertRaises(OSError):self.s.skills.run(version['id'],{'order.csv':self.a['id']},version['scopeId'],run_id=run_id)
        with self.assertRaisesRegex(ValueError,'不同输入'):
            self.s.skills.run(version['id'],{'order.csv':self.b['id']},version['scopeId'],run_id=run_id)
        self.s.skills.disable(dict(id=version['id'],expectedHash=version['versionHash']))
        with self.assertRaisesRegex(ValueError,'已批准'):
            self.s.skills.run(version['id'],{'order.csv':self.a['id']},version['scopeId'],run_id=run_id)
        self.assertEqual(len(self.runner.calls),4)

    def test_corrupted_frozen_output_is_neither_imported_nor_regenerated(self):
        version=self.multi_output_skill();run_id=str(uuid4())
        with patch.object(self.s.files,'_save',side_effect=OSError('storage failure')):
            with self.assertRaises(OSError):self.s.skills.run(version['id'],{'order.csv':self.a['id']},version['scopeId'],run_id=run_id)
        self.s.skills.db.execute('UPDATE skill_run_outputs SET data=? WHERE name=?',(b'bad','result.csv'))
        with self.assertRaisesRegex(ValueError,'冻结的技能输出校验'):
            self.s.skills.run(version['id'],{'order.csv':self.a['id']},version['scopeId'],run_id=run_id)
        self.assertEqual(len(self.runner.calls),4)

    def test_context_mail_is_distinct_from_original_attachment_provenance(self):
        version=self.approve(self.s.skills.propose(self.proposal()));local=self.file('stock-upload.csv',b'price\n7\n')
        result=self.s.skills.run(version['id'],{'order.csv':local['id']},version['scopeId'],source_mail_id='a')
        lineage=result['audit']['inputProvenance'][0]
        self.assertEqual(lineage['originalMailIds'],[]);self.assertEqual(lineage['scopeEvidenceMailIds'],['a'])
        self.assertIn('适用范围',result['audit']['sourceMailIdsNotice'])

    def test_search_preview_requires_loading_full_memory_before_use(self):
        instruction='Use clear English. '*25+' Never reveal the confidential supplier price.'
        version=self.approve(self.s.skills.propose(dict(title='Long reply guidance',kind='memory',scope=SCOPE,rationale='Repeated communication guidance',instruction=instruction)))
        events=[];agent=AgentTools(self.s,'discovery-run',on_event=events.append);self.addCleanup(agent.close)
        found=agent.search_skills(sender=SCOPE['emails'][0]);item=found['skills'][0]
        self.assertEqual(item['preview'],instruction[:240]);self.assertTrue(item['instructionTruncated']);self.assertTrue(item['requiresLoad'])
        self.assertNotIn('instruction',item);self.assertNotIn('contract',item);self.assertNotIn('rationale',item)
        self.assertIn('每个实际使用的技能',found['notice'])
        self.assertEqual(agent.get_skill(version['id'])['proposal']['instruction'],instruction)
        self.assertTrue(any(e.get('source',{}).get('id')==version['id'] for e in events))


if __name__=='__main__':unittest.main()
