"""Opt-in real Docker + library + AgentTools acceptance, without a live LLM.

The fixture code is intentionally deterministic. Full model-authoring acceptance
is separate; this test proves the real execution/storage/review integration.
"""
from copy import deepcopy
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest

from task_backend.agent_tools import AgentTools
from task_backend.mail_files import MailFiles
from task_backend.skill_execution import get_status
from task_backend.skill_library import SkillLibraryService
from task_backend.spreadsheet_files import read_table


CODE='''import csv,os,sys
from decimal import Decimal,InvalidOperation
from pathlib import Path
root=Path(os.environ['TM_SKILL_INPUT_DIR'])
out=Path(os.environ['TM_SKILL_OUTPUT_DIR'])
with (root/'order.csv').open(newline='') as f:
    reader=csv.DictReader(f)
    rows=list(reader)
try:
    amounts=[Decimal(row['price'])*2 for row in rows]
    if not rows or any(not v.is_finite() for v in amounts):raise ValueError()
except (KeyError,ValueError,InvalidOperation):
    print('Missing or invalid price',file=sys.stderr)
    sys.exit(2)
with (out/'result.csv').open('w',newline='') as f:
    writer=csv.writer(f);writer.writerow(['amount'])
    writer.writerows([[format(v,'f')] for v in amounts])
'''


@unittest.skipUnless(os.environ.get('TM_RUN_SKILL_DOCKER_TESTS')=='1','Opt-in real Docker library acceptance')
class DockerSkillLibrary(unittest.TestCase):
    def test_teach_three_cases_review_fresh_conversation_reuse_and_revoke(self):
        self.assertTrue(get_status()['available'],get_status())
        with TemporaryDirectory() as temp:
            service=SimpleNamespace(directory=Path(temp))
            service.files=MailFiles(service);self.addCleanup(service.files.close)
            service.skills=SkillLibraryService(service);self.addCleanup(service.skills.close)
            conversation=dict(turns=[dict(id='teaching-message',runId='teach-run',role='user',createdAt='2026-11-21T00:00:00Z',content='Double each price using exact decimal arithmetic. Reject missing values before making output. Save this reusable procedure.')])
            service.agent=SimpleNamespace(_conversation=lambda id:deepcopy(conversation))
            def file(name,body):return service.files._save(name,body.encode(),'local')['id']
            a=file('first.csv','price\n2\n3.25\n');b=file('second.csv','price\n4.50\n');bad=file('missing.csv','price\n""\n')
            x=file('first-completed.csv','amount\n4\n6.50\n');y=file('second-completed.csv','amount\n9.00\n')
            teacher=AgentTools(service,'teach-run','teaching-chat');self.addCleanup(teacher.close)
            proposal=dict(title='Double exact price',kind='script',scope=dict(kind='personal',label='Price exercise',emails=[],contextKey='price-double'),rationale='Repeated exact decimal calculation benefits from tested code.',instruction='Double every price, preserving row count. Ask for missing values; never substitute zero.',code=CODE,inputs=[dict(name='order.csv',description='CSV price column')],outputs=[dict(name='result.csv',description='CSV amount column',comparison='table')],examples=[
                dict(name='multiple rows',kind='success',inputFiles={'order.csv':a},expectedFiles={'result.csv':x}),
                dict(name='trailing decimal zeros',kind='success',inputFiles={'order.csv':b},expectedFiles={'result.csv':y}),
                dict(name='missing value',kind='failure',inputFiles={'order.csv':bad},expectedError='Missing or invalid price')])
            draft=teacher.propose_skill(proposal,call_id='create')
            self.assertTrue(draft['canApprove'],draft['validation']);self.assertFalse(draft['canRun'])
            self.assertEqual([case['backend'] for case in draft['validation']['cases']],['docker']*3)
            self.assertTrue(all(case['passed'] for case in draft['validation']['cases']))
            approved=service.skills.approve(dict(id=draft['id'],expectedHash=draft['versionHash']))
            self.assertTrue(approved['canRun'])
            # Fresh AgentTools and conversation, with no old transcript needed.
            events=[];user=AgentTools(service,'fresh-run','fresh-chat',events.append);self.addCleanup(user.close)
            found=user.search_skills(scope_id=approved['scopeId']);self.assertEqual(found['skills'][0]['id'],approved['id'])
            user.get_skill(approved['id'])
            fresh=file('new-order.csv','price\n7.25\n0.50\n')
            result=user.run_skill(approved['id'],{'order.csv':fresh},approved['scopeId'],call_id='reuse')
            output=service.files.bytes(result['files'][0]['id'])
            self.assertEqual(read_table(output,'csv')['rows'],[['14.50'],['1.00']])
            self.assertEqual(result['execution']['backend'],'docker')
            self.assertEqual(result['files'][0]['skill']['versionHash'],approved['versionHash'])
            self.assertTrue(any(e.get('artifact',{}).get('kind')=='file' for e in events))
            self.assertTrue(user.run_skill(approved['id'],{'order.csv':fresh},approved['scopeId'],call_id='reuse')['replayed'])
            service.skills.disable(dict(id=approved['id'],expectedHash=approved['versionHash']))
            with self.assertRaisesRegex(ValueError,'已批准'):
                user.run_skill(approved['id'],{'order.csv':fresh},approved['scopeId'],call_id='reuse')


if __name__=='__main__':unittest.main()
