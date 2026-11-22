"""Actual official ToolNode injection/validation, real versioned local stores.

No mock execute, fabricated runtime, provider or mailbox. Both proposal tools
must retain an explicitly supplied correction parent, or reject ambiguous input.
"""
from network_guard import no_external_network
import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

from langchain_core.messages import AIMessage
from langgraph.graph import StateGraph, MessagesState, START, END
from langgraph.prebuilt import ToolNode
from task_backend.agent_tools import AgentTools, build_tools
from task_backend.service import BackendService


class ProposalToolArgumentsTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.service=BackendService(Path(self.temp.name),worker=True);self.addCleanup(self.service.close)
        self.ctx=AgentTools(self.service,'proposal-wire-test');self.addCleanup(self.ctx.close)
        self.enterContext(no_external_network())
        self.tools={t.name:t for t in build_tools(self.ctx)}
        graph=StateGraph(MessagesState)
        graph.add_node('tools',ToolNode([self.tools[n] for n in ('propose_learning','propose_skill')]))
        graph.add_edge(START,'tools');graph.add_edge('tools',END)
        self.graph=graph.compile()

    def proposal(self,name):
        common=dict(title='Quoted layout only',scope=dict(kind='personal',label='My layout',emails=[],contextKey=name),
                    instruction='Keep the customer part and drawing revision in separate columns.',sources=[])
        return dict(common,kind='note') if name=='propose_learning' else dict(common,kind='memory',rationale='Reusable layout preference, not commercial terms.')

    def library(self,name):return self.service.learning if name=='propose_learning' else self.service.skills

    def invoke(self,name,args,id=None):
        call=AIMessage(content='',tool_calls=[dict(name=name,args=args,id=id or str(uuid4()),type='tool_call')])
        result=asyncio.run(self.graph.ainvoke(dict(messages=[call])))
        return result['messages'][-1]

    def parent(self,name):
        msg=self.invoke(name,dict(value=self.proposal(name)),id=name+'-initial')
        self.assertEqual(msg.status,'success',msg.content)
        result=json.loads(msg.content);self.assertNotIn('error',result)
        return result

    def test_top_level_parent_alias_persists_real_correction_and_replay_normalizes(self):
        for name in ('propose_learning','propose_skill'):
            with self.subTest(tool=name):
                parent=self.parent(name);value=dict(self.proposal(name),instruction='Layout only. Read tax and currency from each order.')
                msg=self.invoke(name,dict(value=value,parentId=parent['id']),id=name+'-fix')
                self.assertEqual(msg.status,'success',msg.content)
                result=json.loads(msg.content);self.assertNotIn('error',result)
                stored=self.library(name).get(result['id'])
                self.assertEqual(stored['parentId'],parent['id']);self.assertEqual(stored['version'],2)
                self.assertEqual(stored['status'],'draft');self.assertEqual(stored['proposal']['instruction'],value['instruction'])
                # Wire placement does not create a second proposal on replay.
                replay=self.invoke(name,dict(value=dict(value,parentId=parent['id'])),id=name+'-fix')
                self.assertEqual(json.loads(replay.content)['id'],result['id'])
                self.assertEqual(self.library(name).get(parent['id'])['status'],'draft')
                self.assertEqual(self.library(name).search(status='approved'),[])

    def test_standard_inner_parent_and_matching_explicit_alias_both_work(self):
        for name in ('propose_learning','propose_skill'):
            with self.subTest(tool=name):
                parent=self.parent(name)
                value=dict(self.proposal(name),parentId=parent['id'],instruction='Also prefer editable Word.')
                first=json.loads(self.invoke(name,dict(value=value)).content)
                self.assertEqual(first['parentId'],parent['id'])
                both=dict(self.proposal(name),parentId=first['id'],instruction='Editable Word, no fixed commercial terms.')
                second=json.loads(self.invoke(name,dict(value=both,parentId=first['id'])).content)
                self.assertEqual(second['parentId'],first['id']);self.assertEqual(second['version'],3)

    def test_conflicting_parent_ids_fail_before_storage(self):
        for name in ('propose_learning','propose_skill'):
            with self.subTest(tool=name):
                parent=self.parent(name)
                msg=self.invoke(name,dict(value=dict(self.proposal(name),parentId=parent['id']),parentId=str(uuid4())))
                self.assertEqual(msg.status,'error');self.assertIn('冲突',msg.content)
                self.assertEqual(len(self.library(name).search(status='all')),1)

    def test_unknown_outer_fields_are_rejected_not_silently_dropped(self):
        for name in ('propose_learning','propose_skill'):
            with self.subTest(tool=name):
                self.assertEqual(self.tools[name].args_schema.model_config['extra'],'forbid')
                self.assertNotIn('runtime',self.tools[name].tool_call_schema.model_json_schema()['properties'])
                for extra in ('parent_id','approved','unexpected'):
                    msg=self.invoke(name,dict(value=self.proposal(name),**{extra:'must not disappear'}))
                    self.assertEqual(msg.status,'error',msg.content);self.assertIn(extra,msg.content)
                self.assertEqual(self.library(name).search(status='all'),[])

    def test_omitted_parent_is_not_guessed_from_duplicate_title(self):
        for name in ('propose_learning','propose_skill'):
            with self.subTest(tool=name):
                self.parent(name)
                msg=self.invoke(name,dict(value=dict(self.proposal(name),instruction='Correction with no parent.')))
                result=json.loads(msg.content)
                self.assertEqual(result['error'],'tool_failed');self.assertIn('"parentId"',result['message'])
                self.assertEqual(len(self.library(name).search(status='all')),1)

    def test_alias_is_revalidated_by_existing_nested_schema(self):
        msg=self.invoke('propose_learning',dict(value=self.proposal('propose_learning'),parentId='customer-scope-not-version'))
        self.assertEqual(msg.status,'error');self.assertIn('parentId',msg.content)
        self.assertEqual(self.service.learning.search(status='all'),[])


if __name__=='__main__':unittest.main()
