"""Teaching/review/reuse/correction stories with real SQLite and managed files.

These tests use synthetic business documents and no network, real mailbox or API.
They verify the deterministic learning contract, not model inference quality.
"""
from copy import deepcopy
from hashlib import sha256
from io import BytesIO
from pathlib import Path
import sqlite3
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from uuid import uuid4
import zipfile

from task_backend.csv_documents import encode_csv
from task_backend.learning import LearningService, normalize_scope, apply_recipe, teaching_from_conversation
from task_backend.learning_tools import LearningAgentTools
from task_backend.mail_files import MailFiles
from task_backend.spreadsheet_files import read_table, encode_xlsx, inspect_xlsx


SCOPE=dict(kind='customer',label='Harbor Instruments',emails=['orders@harbor.example'],contextKey='')
ROWS=[['000031','00127','1','3','0.10'],['000031','00128','02','2','0.20']]


def proposal(example_id,**changes):
    value=dict(title='订单转内部清单',kind='table_workflow',scope=deepcopy(SCOPE),
        instruction='料号为 ITEM-REV，REV 补足两位；PO 前导零保留。USD 未税，PCS，逐行保留。',
        sources=[dict(kind='file',id=example_id)],exampleFileId=example_id,
        recipe=dict(inputColumns=['PO','ITEM','REV','QTY','PRICE'],allowExtraColumns=False,
            mapping={'PO号':'PO','数量':'QTY','单价':'PRICE'},constants={},
            concat=[dict(target='客户料号',columns=['ITEM','REV'],separator='-',padLast=2)],
            operations=[dict(op='derive',column='金额',operator='multiply',left=dict(column='数量'),right=dict(column='单价'))],
            outputColumns=['PO号','客户料号','数量','单价','金额'],requiredValues=['PO号','客户料号','数量','单价'],
            numericColumns=['数量','单价','金额'],moneyColumns=['单价','金额'],totals=['金额'],preserveRowCount=True,
            commercial=dict(currency='USD',taxBasis='exclusive',unit='PCS'),outputFormat='csv'))
    value.update(changes)
    return value


class LearningStories(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.mails={}
        self.service=SimpleNamespace(directory=self.root,mail=SimpleNamespace(store=SimpleNamespace(get_local=lambda id:deepcopy(self.mails[id]))))
        self.service.files=MailFiles(self.service);self.addCleanup(self.service.files.close)
        self.service.learning=LearningService(self.service)
        self.addCleanup(lambda:self.service.learning.close())
        self.enterContext(patch('socket.socket.connect',side_effect=AssertionError('Network is forbidden')))
        self.mails['mail-a']=dict(fromAddress='orders@harbor.example',sender='Purchasing <orders@harbor.example>',subject='PO 000031 — November call-off',body='Please retain the line revisions shown in the attachment.',messageId='<po31@harbor.example>')
        self.mails['mail-b']=dict(fromAddress='orders@ridge.example',sender='Purchasing <orders@ridge.example>',subject='PO 000031',body='Different customer; never inherit Harbor conventions.',messageId='<po31@ridge.example>')
        self.input=self.file('PO-000031.csv',ROWS,mail='mail-a')

    def file(self,name,rows,mail=None,columns=None):
        return self.service.files._save(name,encode_csv(columns or ['PO','ITEM','REV','QTY','PRICE'],rows),'mail' if mail else 'local',source_mail=mail)

    def approve(self,value):
        return self.service.learning.approve(dict(id=value['id'],expectedHash=value['versionHash']))

    def test_teach_review_reopen_reuse_then_create_xlsx(self):
        value=proposal(self.input['id']);value['recipe']['outputFormat']='xlsx'
        draft=self.service.learning.propose(value)
        self.assertEqual(draft['status'],'draft');self.assertEqual(draft['preview']['totals'],{'金额':'0.7'})
        self.assertEqual(self.service.learning.search(sender='orders@harbor.example'),[])
        with self.assertRaisesRegex(ValueError,'批准'):
            self.service.learning.run(draft['id'],self.input['id'],'converted',draft['scopeId'])
        self.approve(draft)
        self.service.learning.close();self.service.learning=LearningService(self.service)
        found=self.service.learning.search(sender='orders@harbor.example')
        self.assertEqual([v['id'] for v in found],[draft['id']])
        result=self.service.learning.run(draft['id'],self.input['id'],'converted',draft['scopeId'])
        output=self.service.files.bytes(result['file']['id']);table=read_table(output,'xlsx')
        self.assertEqual(table['rows'],[['000031','00127-01','3','0.10','0.3'],['000031','00128-02','2','0.20','0.4']])
        self.assertEqual(result['audit']['totals'],{'金额':'0.7'})
        self.assertEqual(result['audit']['versionHash'],draft['versionHash'])
        self.assertEqual(result['audit']['inputSha256'],self.input['sha256'])
        self.assertEqual(result['audit']['outputSha256'],sha256(output).hexdigest())
        self.assertEqual(self.service.files.get(result['file']['id'])['learning'],result['audit'])
        self.assertIn(self.input['id'],result['file']['parentIds'])
        self.assertTrue(self.service.files.validate(result['file']['id'])['structuralPassed'])

    def test_corrected_v2_waits_for_review_and_never_overwrites_v1(self):
        raw=proposal(self.input['id']);v1=self.approve(self.service.learning.propose(raw))
        first=self.service.learning.run(v1['id'],self.input['id'],'v1',v1['scopeId'])
        raw['parentId']=v1['id'];raw['instruction']='以后料号与版本之间改为斜线，其余保持原规则。'
        raw['recipe']['concat'][0]['separator']=' / '
        v2=self.service.learning.propose(raw)
        self.assertEqual(self.service.learning.search(sender='orders@harbor.example')[0]['id'],v1['id'])
        self.assertEqual(self.service.learning.get(v1['id'])['proposal']['recipe']['concat'][0]['separator'],'-')
        self.approve(v2)
        self.assertEqual(self.service.learning.get(v1['id'])['status'],'superseded')
        self.assertEqual(self.service.learning.search(sender='orders@harbor.example')[0]['id'],v2['id'])
        with self.assertRaisesRegex(ValueError,'替代'):
            self.service.learning.run(v1['id'],self.input['id'],'v1',v1['scopeId'])
        second=self.service.learning.run(v2['id'],self.input['id'],'v2',v2['scopeId'])
        self.assertNotEqual(first['file']['id'],second['file']['id'])
        self.assertIn('00127 / 01',self.service.files.read(second['file']['id']))
        self.assertIn('00127-01',self.service.files.read(first['file']['id']))
        with self.assertRaises(sqlite3.IntegrityError):self.service.learning.db.execute('UPDATE learning_versions SET title=? WHERE id=?',('mutated',v1['id']))

    def test_same_display_name_and_forwarded_artifacts_do_not_cross_customers(self):
        learned=self.approve(self.service.learning.propose(proposal(self.input['id'])))
        other=self.file('other.csv',ROWS,mail='mail-b')
        copied=self.service.files._save('copy.csv',self.service.files.bytes(other['id']),'generated',parents=[other['id']])
        self.assertEqual(self.service.learning.search(sender='orders@ridge.example'),[])
        for item in (other,copied):
            with self.assertRaisesRegex(ValueError,'跨客户'):
                self.service.learning.run(learned['id'],item['id'],'wrong-customer',learned['scopeId'])
        raw=proposal(other['id']);raw['scope']['emails']=['orders@ridge.example']
        theirs=self.approve(self.service.learning.propose(raw))
        self.assertNotEqual(theirs['scopeId'],learned['scopeId'])
        self.assertEqual(self.service.learning.search(sender='orders@ridge.example')[0]['id'],theirs['id'])

    def test_exact_identity_scope_normalization_and_course_separation(self):
        scope1=deepcopy(SCOPE);scope2=deepcopy(SCOPE)
        scope2['label']='Purchasing';scope2['emails']=['ORDERS@HARBOR.EXAMPLE']
        self.assertEqual(normalize_scope(scope1)[0],normalize_scope(scope2)[0])
        course=dict(kind='course',label='Data Structures',emails=['lecturer@school.example'],contextKey='COMP 2011')
        other=dict(course,contextKey='COMP 3111')
        self.assertNotEqual(normalize_scope(course)[0],normalize_scope(other)[0])
        with self.assertRaises(ValueError):normalize_scope(dict(scope1,emails=['Harbor <orders@harbor.example>']))
        with self.assertRaises(ValueError):normalize_scope(dict(course,contextKey=''))

    def test_missing_price_unknown_layout_or_revision_needs_clarification(self):
        method=self.approve(self.service.learning.propose(proposal(self.input['id'])))
        cases=[self.file('blank-price.csv',[['000032','00127','1','5','']],mail='mail-a'),
               self.file('new-layout.csv',ROWS,columns=['PO','ITEM','REV','Quantity','PRICE'],mail='mail-a'),
               self.file('missing-rev.csv',[['000032','00127','','5','0.10']],mail='mail-a')]
        before=len(self.service.files.list_files())
        for file in cases:
            with self.assertRaises(ValueError):self.service.learning.run(method['id'],file['id'],'must-not-exist',method['scopeId'])
        self.assertEqual(before,len(self.service.files.list_files()))
        self.assertEqual(len(self.service.learning.list()),1)

    def test_price_and_tax_require_explicit_user_reviewed_basis(self):
        raw=proposal(self.input['id']);raw['recipe']['commercial']['taxBasis']='unknown'
        with self.assertRaisesRegex(ValueError,'税'):self.service.learning.propose(raw)
        raw=proposal(self.input['id']);del raw['recipe']['commercial']
        with self.assertRaises(ValueError):self.service.learning.propose(raw)
        raw=proposal(self.input['id']);raw['recipe']['operations']=[dict(op='eval',code='1+1')]
        with self.assertRaisesRegex(ValueError,'代码'):self.service.learning.propose(raw)
        self.assertEqual(self.service.learning.list(),[])

    def test_expected_example_must_match_before_user_can_approve(self):
        good=self.file('internal.csv',[['000031','00127-01','3','0.10','0.3'],['000031','00128-02','2','0.20','0.4']],columns=['PO号','客户料号','数量','单价','金额'])
        raw=proposal(self.input['id'],expectedOutputFileId=good['id'])
        draft=self.service.learning.propose(raw);self.assertTrue(draft['preview']['expectedOutputMatched'])
        bad=self.file('wrong.csv',[['000031','00127-01','3','0.10','999']],columns=['PO号','客户料号','数量','单价','金额'])
        raw=proposal(self.input['id'],expectedOutputFileId=bad['id'],title='另一个方法')
        with self.assertRaisesRegex(ValueError,'不一致'):self.service.learning.propose(raw)

    def test_internal_colleague_reference_is_reviewed_without_rebinding_customer(self):
        self.mails['internal']=dict(fromAddress='ops@supplier.example',subject='Completed acknowledgement',body='Use the completed reference after review.')
        reference=self.file('reference.csv',[['000031','00127-01','3','0.10','0.3'],['000031','00128-02','2','0.20','0.4']],mail='internal',columns=['PO号','客户料号','数量','单价','金额'])
        raw=proposal(self.input['id'],expectedOutputFileId=reference['id'])
        raw['sources'].append(dict(kind='mail',id='internal'))
        method=self.approve(self.service.learning.propose(raw))
        evidence=next(s for s in method['proposal']['sources'] if s['id']==reference['id'])
        self.assertEqual(evidence['role'],'reference-output')
        self.assertEqual(evidence['mailSources'][0]['fromAddress'],'ops@supplier.example')
        mail_evidence=next(s for s in method['proposal']['sources'] if s['id']=='internal')
        self.assertEqual(mail_evidence['role'],'reference-output');self.assertEqual(mail_evidence['fromAddress'],'ops@supplier.example')
        self.assertEqual(method['scope']['emails'],['orders@harbor.example'])
        result=self.service.learning.run(method['id'],self.input['id'],'result',method['scopeId'])
        self.assertIn(reference['id'],result['file']['parentIds'])
        self.assertEqual(self.service.learning._file_scope(result['file']['id'],method['scope']),['mail-a'])
        other=self.file('other-customer.csv',ROWS,mail='mail-b')
        with self.assertRaisesRegex(ValueError,'跨客户'):self.service.learning.run(method['id'],other['id'],'wrong',method['scopeId'])
        unrelated=deepcopy(raw);unrelated.update(parentId=method['id'],instruction='Cannot add unrelated foreign evidence.')
        unrelated['sources'].append(dict(kind='mail',id='mail-b'))
        with self.assertRaisesRegex(ValueError,'跨客户'):self.service.learning.propose(unrelated)
        no_reference=proposal(self.input['id'],title='Without explicit selected reference')
        no_reference['sources'].append(dict(kind='mail',id='internal'))
        with self.assertRaisesRegex(ValueError,'跨客户'):self.service.learning.propose(no_reference)

    def test_replay_recovery_and_revocation_revalidate_before_cached_return(self):
        raw=proposal(self.input['id']);raw['recipe']['outputFormat']='xlsx'
        approved=self.approve(self.service.learning.propose(raw));run_id=str(uuid4())
        args=(approved['id'],self.input['id'],'stable',approved['scopeId'])
        first=self.service.learning.run(*args,run_id=run_id)
        repeated=self.service.learning.run(*args,run_id=run_id)
        self.assertTrue(repeated['replayed']);self.assertEqual(first['file']['id'],repeated['file']['id'])
        with self.assertRaises(ValueError):self.service.learning.run(approved['id'],self.input['id'],'changed-name',approved['scopeId'],run_id=run_id)
        self.service.learning.db.execute('DELETE FROM learning_runs WHERE id=?',(run_id,))
        recovered=self.service.learning.run(*args,run_id=run_id)
        self.assertEqual(first['file']['id'],recovered['file']['id'])
        self.assertEqual(len(self.service.files.list_files()),2)
        self.service.learning.disable(dict(id=approved['id'],expectedHash=approved['versionHash']))
        with self.assertRaisesRegex(ValueError,'停用'):self.service.learning.run(*args,run_id=run_id)

    def test_review_hash_and_output_tamper_are_detected(self):
        draft=self.service.learning.propose(proposal(self.input['id']))
        with self.assertRaisesRegex(ValueError,'预览'):self.service.learning.approve(dict(id=draft['id'],expectedHash='stale'))
        method=self.approve(draft)
        result=self.service.learning.run(method['id'],self.input['id'],'result',method['scopeId'])
        (self.service.files.root/result['file']['id']).write_bytes(b'changed')
        with self.assertRaisesRegex(ValueError,'变化'):self.service.learning.run(method['id'],self.input['id'],'result',method['scopeId'])

    def test_two_review_windows_cannot_approve_stale_fork_or_reactivate_old_version(self):
        raw=proposal(self.input['id']);v1=self.approve(self.service.learning.propose(raw))
        same=self.approve(v1);self.assertEqual(same['reviewedAt'],v1['reviewedAt'])
        left=deepcopy(raw);left.update(parentId=v1['id'],instruction='Use slash going forward.');left['recipe']['concat'][0]['separator']='/'
        right=deepcopy(raw);right.update(parentId=v1['id'],instruction='Use colon going forward.');right['recipe']['concat'][0]['separator']=':'
        a=self.service.learning.propose(left);b=self.service.learning.propose(right)
        self.approve(a)
        with self.assertRaisesRegex(ValueError,'旧版本'):self.approve(b)
        self.assertEqual(self.service.learning.search(sender='orders@harbor.example')[0]['id'],a['id'])
        with self.assertRaisesRegex(ValueError,'旧版'):self.approve(v1)
        self.service.learning.disable(dict(id=a['id'],expectedHash=a['versionHash']))
        with self.assertRaisesRegex(ValueError,'停用'):self.approve(a)
        with self.assertRaisesRegex(ValueError,'旧版本'):self.approve(b)

    def test_first_draft_can_be_corrected_repeatedly_before_initial_approval(self):
        raw=proposal(self.input['id']);first=self.service.learning.propose(raw)
        raw.update(parentId=first['id'],instruction='First correction: slash instead of hyphen.');raw['recipe']['concat'][0]['separator']='/'
        second=self.service.learning.propose(raw)
        raw.update(parentId=second['id'],instruction='Second correction: slash and preserve every input row.')
        third=self.service.learning.propose(raw)
        self.assertEqual(first['proposal']['reviewBase'],third['proposal']['reviewBase'])
        self.assertEqual(self.service.learning.search(),[])
        self.approve(third)
        self.assertEqual(self.service.learning.search()[0]['id'],third['id'])
        self.assertEqual(self.service.learning.search(status='draft'),[])
        for old in (first,second):
            self.assertEqual(self.service.learning.get(old['id'])['status'],'superseded')
            self.assertEqual(self.service.learning.get(old['id'])['proposal'],old['proposal'])
            with self.assertRaisesRegex(ValueError,'旧版本|已有已批准'):self.approve(old)
        with self.assertRaisesRegex(ValueError,'旧版本'):
            self.service.learning.propose(dict(raw,parentId=first['id']))

    def test_active_revision_draft_can_be_corrected_and_old_sibling_is_blocked(self):
        raw=proposal(self.input['id']);v1=self.approve(self.service.learning.propose(raw))
        raw.update(parentId=v1['id'],instruction='Proposed slash convention.');raw['recipe']['concat'][0]['separator']='/'
        draft=self.service.learning.propose(raw)
        sibling=self.service.learning.propose(dict(raw,instruction='Competing proposal.'))
        raw.update(parentId=draft['id'],instruction='Corrected explanation, same slash convention.')
        corrected=self.service.learning.propose(raw)
        self.assertEqual(self.service.learning.search()[0]['id'],v1['id'])
        self.approve(corrected)
        for old in (draft,sibling):
            with self.assertRaisesRegex(ValueError,'旧版本'):self.approve(old)
        self.assertEqual(self.service.learning.get(v1['id'])['status'],'superseded')

    def test_latest_disabled_version_requires_new_review_and_handles_aba(self):
        raw=proposal(self.input['id']);first=self.service.learning.propose(raw)
        stale=self.service.learning.propose(dict(raw,parentId=first['id'],instruction='Older unapproved branch.'))
        v1=self.approve(first)
        self.service.learning.disable(dict(id=v1['id'],expectedHash=v1['versionHash']))
        # The active pointer is None again, but an approval and revocation occurred.
        self.assertEqual(self.service.learning.search(),[])
        with self.assertRaisesRegex(ValueError,'旧版本'):self.approve(stale)
        with self.assertRaisesRegex(ValueError,'停用'):self.approve(v1)
        raw.update(parentId=v1['id'],instruction='Reviewed replacement after disabling the original method.')
        restart=self.service.learning.propose(raw)
        corrected=self.service.learning.propose(dict(raw,parentId=restart['id'],instruction='Final correction to restarted method.'))
        self.approve(corrected)
        self.assertEqual(self.service.learning.search()[0]['id'],corrected['id'])
        self.service.learning.disable(dict(id=corrected['id'],expectedHash=corrected['versionHash']))
        with self.assertRaisesRegex(ValueError,'旧版本'):self.service.learning.propose(dict(raw,parentId=v1['id']))
        latest=self.service.learning.propose(dict(raw,parentId=corrected['id']))
        self.approve(latest)

    def test_agent_can_read_unapproved_history_without_executing_it(self):
        raw=proposal(self.input['id']);draft=self.service.learning.propose(raw)
        ctx=LearningAgentTools();ctx.service=self.service;ctx._source=lambda *args:None
        found=ctx.search_learning(sender='orders@harbor.example',status='draft')
        self.assertEqual(found['methods'][0]['id'],draft['id']);self.assertFalse(found['methods'][0]['canRun'])
        self.assertFalse(ctx.get_learning(draft['id'])['canRun'])
        self.assertEqual(ctx.search_learning()['count'],0)
        with self.assertRaisesRegex(ValueError,'批准'):
            self.service.learning.run(draft['id'],self.input['id'],'unapproved',draft['scopeId'])
        v1=self.approve(draft);self.assertTrue(ctx.get_learning(v1['id'])['canRun'])
        self.service.learning.disable(dict(id=v1['id'],expectedHash=v1['versionHash']))
        self.assertFalse(ctx.get_learning(v1['id'])['canRun'])
        self.assertEqual(ctx.search_learning(status='all')['count'],1)
        with self.assertRaises(ValueError):ctx.search_learning(status='disabled')

    def test_note_preferences_require_review_and_mail_cannot_replace_approved_note(self):
        raw=dict(title='回复风格',kind='note',scope=deepcopy(SCOPE),instruction='回复时先确认版本与交期，金额未核实前不要承诺报价。',sources=[dict(kind='mail',id='mail-a')])
        approved=self.approve(self.service.learning.propose(raw))
        newer=dict(raw,parentId=approved['id'],instruction='改为简短英文回复，但仍先核实版本与交期。')
        draft=self.service.learning.propose(newer)
        self.assertEqual(self.service.learning.search(sender='orders@harbor.example')[0]['id'],approved['id'])
        self.assertEqual(self.service.learning.get(draft['id'])['status'],'draft')
        with self.assertRaisesRegex(ValueError,'文字经验'):self.service.learning.run(approved['id'],self.input['id'],'not-executable',approved['scopeId'])
        self.assertFalse(hasattr(LearningAgentTools,'approve_learning'));self.assertFalse(hasattr(LearningAgentTools,'disable_learning'))
        with self.assertRaises(ValueError):self.service.learning.call('propose',raw)

    def test_local_upload_requires_scope_and_sources_cannot_spoof_email(self):
        method=self.approve(self.service.learning.propose(proposal(self.input['id'])))
        local=self.file('local.csv',ROWS)
        with self.assertRaisesRegex(ValueError,'scopeId'):self.service.learning.run(method['id'],local['id'],'local')
        self.service.learning.run(method['id'],local['id'],'local',method['scopeId'])
        with self.assertRaisesRegex(ValueError,'跨客户'):self.service.learning.run(method['id'],local['id'],'local',method['scopeId'],source_mail_id='mail-b')
        self.mails['mail-a'].pop('fromAddress')
        with self.assertRaisesRegex(ValueError,'From'):self.service.learning.run(method['id'],self.input['id'],'no-from',method['scopeId'])

    def test_quantity_deduplication_requires_explicit_changed_row_permission(self):
        source=self.file('duplicates.csv',ROWS+[ROWS[0]],mail='mail-a')
        raw=proposal(source['id']);raw['recipe']['operations'].insert(0,dict(op='drop_duplicates'))
        with self.assertRaisesRegex(ValueError,'行数'):self.service.learning.propose(raw)
        raw['recipe']['preserveRowCount']=False
        draft=self.service.learning.propose(raw)
        self.assertEqual(draft['preview']['rowCount'],2);self.assertEqual(draft['preview']['inputRowCount'],3)

    def test_missing_date_is_required_by_default_even_if_model_omits_result_check(self):
        headers=['PO','ITEM','REV','QTY','PRICE','NEED_BY']
        good=self.file('with-date.csv',[r+['2026-12-10'] for r in ROWS],mail='mail-a',columns=headers)
        raw=proposal(good['id']);raw['recipe']['inputColumns']=headers
        raw['recipe']['mapping']['Required Date']='NEED_BY';raw['recipe']['outputColumns'].append('Required Date')
        # Deliberately reproduce the real model omission of Required Date here.
        self.assertNotIn('Required Date',raw['recipe']['requiredValues'])
        method=self.approve(self.service.learning.propose(raw))
        bad=self.file('missing-date.csv',[ROWS[0]+['']],mail='mail-a',columns=headers)
        with self.assertRaisesRegex(ValueError,'NEED_BY'):
            self.service.learning.run(method['id'],bad['id'],'must-ask',method['scopeId'])
        legacy=deepcopy(method['proposal']['recipe']);legacy.pop('optionalInputColumns')
        with self.assertRaisesRegex(ValueError,'NEED_BY'):apply_recipe(self.service.files.bytes(bad['id']),'csv',legacy)
        changed=deepcopy(raw);changed.update(parentId=method['id'],instruction='The user explicitly permits an unknown required date; keep it blank.')
        changed['recipe']['optionalInputColumns']=['NEED_BY']
        v2=self.approve(self.service.learning.propose(changed))
        output=self.service.learning.run(v2['id'],bad['id'],'explicit-optional',v2['scopeId'])
        self.assertEqual(read_table(self.service.files.bytes(output['file']['id']),'csv')['rows'][0][-1],'')
        self.assertEqual(v2['proposal']['recipe']['optionalInputColumns'],['NEED_BY'])

    def test_direct_calls_do_not_fabricate_user_teaching(self):
        raw=proposal(self.input['id']);draft=self.service.learning.propose(raw)
        self.assertEqual(draft['proposal']['teaching']['source'],'direct-input')
        self.assertEqual(draft['proposal']['teaching']['turns'],[])
        raw['teaching']={'source':'conversation','turns':[{'content':'invented'}]}
        with self.assertRaises(ValueError):self.service.learning.propose(raw)

    def test_shape_errors_identify_unknown_source_keys_and_allow_precise_retry(self):
        raw=proposal(self.input['id']);raw['sources'][0].update(name='PRIVATE_VALUE_MUST_NOT_BE_ECHOED',sha256='private-hash')
        with self.assertRaises(ValueError) as raised:self.service.learning.propose(raw)
        message=str(raised.exception)
        self.assertIn('sources[0]',message);self.assertIn('name',message);self.assertIn('sha256',message)
        self.assertNotIn('PRIVATE_VALUE_MUST_NOT_BE_ECHOED',message);self.assertNotIn('private-hash',message)
        self.assertEqual(self.service.learning.list(),[])
        raw['sources']=[dict(kind='file',id=self.input['id'])]
        draft=self.service.learning.propose(raw)
        self.assertEqual(draft['status'],'draft')
        invalid=proposal(self.input['id']);invalid.pop('instruction');invalid['scopeId']='private-scope-value'
        with self.assertRaises(ValueError) as raised:self.service.learning.propose(invalid)
        self.assertIn('缺少字段：instruction',str(raised.exception));self.assertIn('未知字段：scopeId',str(raised.exception))
        self.assertNotIn('private-scope-value',str(raised.exception))


class TeachingEvidenceTests(unittest.TestCase):
    def setUp(self):
        from task_backend.service import BackendService
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.service=BackendService(Path(self.temp.name),worker=True);self.addCleanup(self.service.close)
        self.service.settings.for_request=lambda:dict(apiKey='offline-fixture',baseUrl='https://example.invalid',model='offline')
        self.service.agent.schedule=lambda:None
        self.enterContext(patch('socket.socket.connect',side_effect=AssertionError('No network')))

    def start(self,text,conversation=None):
        args=dict(text=text)
        if conversation:args['conversationId']=conversation
        return self.service.agent.call('startTurn',args)

    def note(self):
        return dict(title='My reply preference',kind='note',scope=dict(kind='personal',label='Me',emails=[]),instruction='Model interpretation, not the original teaching.',sources=[])

    def test_saved_user_and_clarification_are_verbatim_and_replay_snapshot_is_stable(self):
        from task_backend.agent_tools import AgentTools
        run=self.start('Only use short replies for Harbor. This is my actual instruction.')
        ctx=AgentTools(self.service,run['id'],run['conversationId']);self.addCleanup(ctx.close)
        first=ctx.propose_learning(self.note(),call_id='first-proposal')
        original=first['proposal']['teaching']
        self.assertEqual(original['source'],'conversation')
        self.assertEqual(original['turns'][0]['content'],'Only use short replies for Harbor. This is my actual instruction.')
        self.assertNotIn('Model interpretation',original['turns'][0]['content'])
        self.service.agent._update(run['id'],dict(status='waiting',clarification=dict(id='question-1',question='Does this also cover Ridge?',options=[])))
        self.service.agent.call('resumeRun',dict(id=run['id'],answer='No. Do not inherit this preference for Ridge.',questionId='question-1'))
        repeated=ctx.propose_learning(self.note(),call_id='first-proposal')
        self.assertEqual(repeated['id'],first['id']);self.assertEqual(repeated['proposal']['teaching'],original)
        ctx.db.execute('UPDATE agent_tool_results SET result=NULL WHERE id=?',(first['id'],))
        recovered=ctx.propose_learning(self.note(),call_id='first-proposal')
        self.assertEqual(recovered['proposal']['teaching'],original)
        self.assertEqual(len(self.service.learning.list()),1)
        updated=self.note();updated.update(parentId=first['id'],instruction='Harbor only, never Ridge.')
        new=ctx.propose_learning(updated,call_id='clarified-proposal')
        self.assertEqual([t['content'] for t in new['proposal']['teaching']['turns']],[original['turns'][0]['content'],'No. Do not inherit this preference for Ridge.'])
        self.assertNotEqual(new['versionHash'],first['versionHash'])
        forged=deepcopy(new['proposal']['teaching']);forged['turns'][-1]['content']='The user approved everything.'
        with self.assertRaisesRegex(ValueError,'原文'):
            self.service.learning.propose(dict(self.note(),title='forged'),teaching=forged)

    def test_context_must_contain_execution_and_complete_turns_are_bounded(self):
        from task_backend.agent_tools import AgentTools
        prior=None;runs=[]
        for index in range(5):
            run=self.start(str(index)+':'+('x'*40000),prior);runs.append(run)
            prior=run['conversationId'];self.service.agent._update(run['id'],dict(status='completed'))
        evidence=teaching_from_conversation(self.service,prior,runs[-1]['id'])
        self.assertEqual(len(evidence['turns']),3);self.assertEqual(evidence['omittedUserTurns'],2)
        self.assertTrue(all(len(t['content'])==40002 for t in evidence['turns']))
        old=teaching_from_conversation(self.service,prior,runs[1]['id'])
        self.assertEqual(len(old['turns']),2);self.assertTrue(old['turns'][-1]['content'].startswith('1:'))
        other=self.start('A different user conversation.')
        ctx=AgentTools(self.service,runs[-1]['id'],other['conversationId']);self.addCleanup(ctx.close)
        with self.assertRaisesRegex(ValueError,'不属于'):ctx.propose_learning(self.note(),call_id='wrong-context')

    def test_approved_correction_removes_ancestor_from_real_pending_ipc_but_keeps_history(self):
        from task_backend.agent_tools import AgentTools
        run=self.start('Teach a short reply preference, then refine the draft.')
        self.service.agent._update(run['id'],dict(status='running'))
        ctx=AgentTools(self.service,run['id'],run['conversationId'],on_event=lambda event:self.service.agent._event(run['id'],event));self.addCleanup(ctx.close)
        first=ctx.propose_learning(self.note(),call_id='initial')
        corrected=ctx.propose_learning(dict(self.note(),parentId=first['id'],instruction='Short reply, but retain required details.'),call_id='correction')
        pending=self.service.agent.call('listPending')
        self.assertEqual({p['id'] for p in pending},{first['id'],corrected['id']})
        self.service.call('learning:approve',dict(id=corrected['id'],expectedHash=corrected['versionHash']))
        self.assertEqual(self.service.agent.call('listPending'),[])
        old=ctx.get_learning(first['id'])
        self.assertEqual(old['status'],'superseded');self.assertFalse(old['canRun'])
        self.assertEqual(old['versionHash'],first['versionHash']);self.assertEqual(old['proposal'],first['proposal'])
        self.assertEqual(self.service.learning.search(status='draft'),[])
        self.assertEqual(len(self.service.learning.search(status='all')),2)
        with self.assertRaises(ValueError):self.service.learning.run(first['id'],'unused-file','cannot-run',first['scopeId'])


class XlsxBoundaries(unittest.TestCase):
    def workbook(self,configure):
        from openpyxl import Workbook
        book=Workbook();configure(book);stream=BytesIO();book.save(stream);book.close();return stream.getvalue()

    def test_literal_values_leading_zeros_decimal_and_deterministic_output(self):
        rows=[['00123','0.10','=SUM(A1:A2)'],['00124','0.20','@literal']]
        data=encode_xlsx(['sku','price','notes'],rows)
        self.assertEqual(read_table(data,'xlsx')['rows'],rows)
        self.assertEqual(data,encode_xlsx(['sku','price','notes'],rows))
        self.assertEqual(inspect_xlsx(data,0,1)['nextOffset'],1)

    def test_numeric_cell_with_zero_format_retains_display_identifier(self):
        def build(book):
            sheet=book.active;sheet.append(['sku','qty']);sheet.append([127,3]);sheet['A2'].number_format='00000'
        table=read_table(self.workbook(build),'xlsx')
        self.assertEqual(table['rows'],[['00127','3']])

    def test_formula_and_error_cells_are_rejected_instead_of_stale_cache(self):
        for val in ('=1+2','#DIV/0!'):
            data=self.workbook(lambda book: (book.active.append(['amount']),book.active.append([val])))
            with self.assertRaises(ValueError):read_table(data,'xlsx')

    def test_multi_sheet_requires_selection_and_merged_layout_is_rejected(self):
        def multi(book):
            book.active.title='Orders';book.active.append(['id']);book.active.append(['001'])
            book.create_sheet('Summary').append(['different'])
        data=self.workbook(multi)
        with self.assertRaisesRegex(ValueError,'工作表'):read_table(data,'xlsx')
        self.assertEqual(read_table(data,'xlsx','Orders')['rows'],[['001']])
        def merged(book):
            book.active.append(['Item','Qty']);book.active.append(['001','1']);book.active.merge_cells('A1:B1')
        with self.assertRaisesRegex(ValueError,'合并'):read_table(self.workbook(merged),'xlsx')

    def test_external_hyperlinks_macro_entries_and_bad_headers_are_rejected(self):
        def link(book):
            book.active.append(['url']);book.active.append(['site']);book.active['A2'].hyperlink='https://example.invalid'
        with self.assertRaisesRegex(ValueError,'外部'):read_table(self.workbook(link),'xlsx')
        base=encode_xlsx(['id'],[['001']]);stream=BytesIO(base)
        with zipfile.ZipFile(stream,'a') as archive:archive.writestr('xl/vbaProject.bin',b'not-executable-fixture')
        with self.assertRaisesRegex(ValueError,'宏'):read_table(stream.getvalue(),'xlsx')
        duplicate=self.workbook(lambda book:(book.active.append(['id','id']),book.active.append(['001','002'])))
        with self.assertRaisesRegex(ValueError,'列名'):read_table(duplicate,'xlsx')


if __name__=='__main__':unittest.main()
