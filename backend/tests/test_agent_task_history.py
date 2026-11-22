"""History is queryable without restoring tasks or inferring cancellation from trash."""
from network_guard import no_external_network
import asyncio
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from langchain_core.messages import AIMessage, HumanMessage
from task_backend.agent_tools import AgentTools, build_tools
from task_backend.service import BackendService


class TaskHistoryTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.s=BackendService(Path(self.temp.name),worker=True)
        self.ctx=AgentTools(self.s,'history-test','history-chat')
        self.addCleanup(self.temp.cleanup);self.addCleanup(self.s.close);self.addCleanup(self.ctx.close)
        self.enterContext(no_external_network())
    def task(self,title='Course discussion',completed=False):
        value=self.s.repo.call('createTodo',dict(title=title,notes='project',due={'mode':'value','value':{'kind':'minute','at':'2026-11-26T15:00:00+08:00','timeZone':'Asia/Hong_Kong'}}))['value']
        if completed:value=self.s.repo.call('setCompleted',value['id'],True,value['revision'])['value']
        return value
    def cancel(self,task):
        p=self.s.task_changes.propose(dict(action='cancel',taskId=task['id'],expectedRevision=task['revision'],reason='Teacher cancelled meeting',patch={},evidence=[]))
        return self.s.task_changes.approve(dict(id=p['id'],expectedHash=p['hash']))
    def dump(self):
        return list(self.s.repo.db.iterdump()),[r[0] for r in self.s.repo.db.execute('SELECT payload FROM task_changes.proposals ORDER BY id')]
    def test_default_excludes_trash_history_reads_same_cancelled_id_without_mutation(self):
        task=self.task();receipt=self.cancel(task);before=self.dump()
        self.assertEqual(self.ctx.list_tasks('Course')['items'],[])
        self.assertIn('不证明从未创建',self.ctx.list_tasks('Course')['scope'])
        result=self.ctx.list_tasks('Course',include_deleted=True);found=result['items'][0]
        self.assertTrue(result['includeDeleted']);self.assertEqual(found['id'],task['id'])
        self.assertEqual(found['lifecycleState'],'cancelled');self.assertFalse(found['completed'])
        self.assertEqual(found['cancellation']['changeId'],receipt['id']);self.assertTrue(found['deletedAt'])
        self.assertIsNone(found['effectiveDue']);self.assertEqual(found['due'],task['due'])
        self.assertEqual(self.dump(),before)
    def test_manual_trash_and_restore_retrash_not_falsely_labelled_cancellation(self):
        task=self.task();old=self.cancel(task)
        self.s.repo.call('restoreBatch',old['trashBatchId']);restored=self.s.repo.call('listTodos')[0]
        self.s.repo.call('trashTodo',task['id'],restored['revision'])
        item=self.ctx.list_tasks(include_deleted=True)['items'][0]
        self.assertEqual(item['lifecycleState'],'trashed');self.assertNotIn('cancellation',item)
        self.assertFalse(item['completed'])
    def test_query_completed_filters_pagination_and_deleted_parent_due(self):
        active=self.task('Course active');cancelled=self.task('Course cancel');self.cancel(cancelled)
        done=self.task('Course done',True);self.task('Other item')
        parent=self.task('Other parent');child=self.s.repo.call('createTodo',dict(title='Course inherited',parentId=parent['id'],due={'mode':'inherit'}))['value']
        self.s.repo.call('trashTodo',parent['id'],parent['revision'])
        first=self.ctx.list_tasks('Course',False,limit=1,include_deleted=True)
        second=self.ctx.list_tasks('Course',False,offset=first['nextOffset'],limit=2,include_deleted=True)
        self.assertEqual(first['total'],3);self.assertIsNone(second['nextOffset'])
        self.assertEqual({v['id'] for v in first['items']+second['items']},{active['id'],cancelled['id'],child['id']})
        inherited=next(v for v in first['items']+second['items'] if v['id']==child['id'])
        self.assertEqual(inherited['due'],{'mode':'inherit'});self.assertIsNone(inherited['effectiveDue'])
        self.assertEqual(self.ctx.list_tasks('Course',True,include_deleted=True)['items'][0]['id'],done['id'])
        self.assertEqual(self.ctx.list_tasks('Course',False)['total'],1)
    def test_flag_validation_and_official_tool_schema(self):
        for value in ('true',1,None):
            with self.assertRaises(ValueError):self.ctx.list_tasks(include_deleted=value)
        tool=next(t for t in build_tools(self.ctx) if t.name=='list_tasks')
        field=tool.args['include_deleted'];self.assertEqual(field['type'],'boolean');self.assertFalse(field['default'])
        self.assertIn('does NOT mean never created',tool.description)
    def test_registered_tool_forwards_history_flag(self):
        from langchain.agents import create_agent
        from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
        class ToolModel(FakeMessagesListChatModel):
            def bind_tools(self,tools,**kwargs):return self
        task=self.task();self.cancel(task)
        model=ToolModel(responses=[AIMessage(content='',tool_calls=[dict(name='list_tasks',args={'query':'Course','include_deleted':True},id='history-call')]),AIMessage(content='History read')])
        result=asyncio.run(create_agent(model,build_tools(self.ctx)).ainvoke({'messages':[HumanMessage(content='Find past discussion')]}))
        tool=next(m for m in result['messages'] if getattr(m,'name',None)=='list_tasks')
        payload=json.loads(tool.content)
        self.assertTrue(payload['includeDeleted']);self.assertEqual(payload['items'][0]['lifecycleState'],'cancelled')
        self.assertEqual(self.s.repo.call('listTodos'),[])

if __name__=='__main__':unittest.main()
