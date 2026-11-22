"""Opt-in official skill disclosure -> business run_skill -> real local Docker.

The model alone is scripted. Runtime, progressive SkillsMiddleware, dispatch,
reviewed skill library, managed files and Docker execution are production code.
Never contacts a real provider or mailbox, and never executes skill code on host.
"""
import json
import os
import unittest
from unittest.mock import patch
from langchain_core.messages import AIMessage, ToolMessage
from task_backend.skill_execution import get_status
from task_backend.spreadsheet_files import read_table
from test_agent_runtime import tool_call
from test_deepagents_runtime import DeepAgentsRuntimeTests
from test_skill_library_docker import CODE


@unittest.skipUnless(os.environ.get('TM_RUN_SKILL_DOCKER_TESTS')=='1',
                     'Opt-in official Deep Agents to real Docker integration')
class DeepAgentsSkillDockerTests(unittest.TestCase):
    setUp=DeepAgentsRuntimeTests.setUp
    tearDown=DeepAgentsRuntimeTests.tearDown
    open_service=DeepAgentsRuntimeTests.open_service
    model=DeepAgentsRuntimeTests.model
    start=DeepAgentsRuntimeTests.start
    execute=DeepAgentsRuntimeTests.execute

    def test_official_learning_skill_exposes_run_skill_and_dispatches_to_real_docker(self):
        status=get_status();self.assertTrue(status['available'],status)
        service=self.service
        def file(name,body):return service.files._save(name,body.encode(),'local')['id']
        first=file('first.csv','price\n2\n3.25\n')
        second=file('second.csv','price\n4.50\n')
        bad=file('missing.csv','price\n""\n')
        answer1=file('first-expected.csv','amount\n4\n6.50\n')
        answer2=file('second-expected.csv','amount\n9.00\n')
        proposal=dict(title='Double exact price',kind='script',
            scope=dict(kind='personal',label='Synthetic price exercise',emails=[],contextKey='price-double'),
            rationale='Repeated exact arithmetic needs a tested script.',
            instruction='Double every price preserving rows; reject missing values before producing output.',
            code=CODE,inputs=[dict(name='order.csv',description='CSV price column')],
            outputs=[dict(name='result.csv',description='CSV amount column',comparison='table')],
            examples=[dict(name='multiple rows',kind='success',inputFiles={'order.csv':first},expectedFiles={'result.csv':answer1}),
                      dict(name='decimal zeros',kind='success',inputFiles={'order.csv':second},expectedFiles={'result.csv':answer2}),
                      dict(name='missing values',kind='failure',inputFiles={'order.csv':bad},expectedError='Missing or invalid price')])
        draft=service.skills.propose(proposal)
        self.assertTrue(draft['canApprove'],draft['validation'])
        self.assertEqual([case['backend'] for case in draft['validation']['cases']],['docker']*3)
        # Test fixture simulates the separate human review endpoint; the agent
        # receives no approval capability and cannot approve its own code.
        approved=service.skills.approve(dict(id=draft['id'],expectedHash=draft['versionHash']))
        fresh=file('fresh.csv','price\n7.25\n0.50\n')
        model=self.model(
            tool_call('read_file',dict(file_path='/skills/builtin/learning/SKILL.md'),'learn'),
            tool_call('search_skills',dict(scope_id=approved['scopeId']),'search'),
            tool_call('get_skill',dict(id=approved['id']),'load'),
            tool_call('run_skill',dict(version_id=approved['id'],inputs={'order.csv':fresh},scope_id=approved['scopeId']),'execute'),
            AIMessage(content='已用批准的方法处理新文件，结果已保存。'))
        with patch.object(service.skills,'runner',wraps=service.skills.runner) as actual_runner:
            result=self.execute(self.start('按以前批准的精确价格翻倍方法处理这个新文件。',fileIds=[fresh]))
        self.assertNotIn('run_skill',model.bindings[0])
        self.assertIn('run_skill',model.bindings[1])
        self.assertNotIn('approve_skill',model.bindings[-1])
        actual_runner.assert_called_once()
        self.assertEqual(actual_runner.call_args.args[0],CODE)
        self.assertEqual(actual_runner.call_args.args[1],{'order.csv':b'price\n7.25\n0.50\n'})
        completed=[e['tool'] for e in result['events'] if e.get('status')=='completed']
        self.assertEqual(completed,['read_file','search_skills','get_skill','run_skill'])
        returned=next(m for m in model.seen[-1] if isinstance(m,ToolMessage) and m.name=='run_skill')
        self.assertEqual(returned.status,'success')
        payload=json.loads(returned.content)
        self.assertEqual(payload['execution']['backend'],'docker')
        self.assertTrue(payload['execution']['cleanupConfirmed'])
        self.assertEqual(payload['execution']['imageId'],status['imageId'])
        self.assertEqual(payload['execution']['runtimeHash'],status['runtimeHash'])
        self.assertEqual(len(payload['files']),1)
        output=payload['files'][0]
        self.assertEqual(read_table(service.files.bytes(output['id']),'csv')['rows'],[['14.50'],['1.00']])
        self.assertEqual(output['skill']['versionHash'],approved['versionHash'])
        self.assertTrue(any(a['kind']=='file' and a['id']==output['id'] for a in result['artifacts']))
        self.assertTrue(any(s['kind']=='skill' and s['id']==approved['id'] for s in result['sources']))


del DeepAgentsRuntimeTests
