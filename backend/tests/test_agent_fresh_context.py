import asyncio
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[2]
sys.path[:0]=[str(ROOT/'backend'),str(ROOT/'backend/tests')]
from deepagents import create_deep_agent
from langchain_core.messages import AIMessage, HumanMessage
from task_backend.agent_evidence import EvidenceAgentTools
from task_backend.service import BackendService
from test_agent_runtime import ScriptedModel
from task_backend.agent_fresh_context import FreshLocalContext, FreshContextState, current_deliverables, current_tasks
from task_backend.draft_lifecycle import DraftLifecycle
from task_backend.archive import drafts_value


class NoToolModel(ScriptedModel):
    async def _agenerate(self,messages,stop=None,run_manager=None,**kwargs):
        return self._generate(messages,**kwargs)


class FreshContextTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.service=BackendService(Path(self.temp.name),worker=True);self.addCleanup(self.service.close)
        self.events=[];self.ctx=EvidenceAgentTools(self.service,'run-13','conversation',self.events.append)
        self.addCleanup(lambda:self.ctx.close())
        self.enterContext(patch('socket.socket.connect',side_effect=AssertionError('Network forbidden')))
        self.service.mail.manager.entry=lambda id:dict(id=id)

    def mail(self,id,body,sender='orders@harbor.example.invalid',binding='local',identity='identity',status='complete',at='2026-11-21T08:00:00Z'):
        return self.service.mail.store.put(binding,identity,'me@example.invalid',dict(id=id,subject='PO update',sender=sender,
            body=body,receivedAt=at,attachments=[],bodyStatus=status))['localId']

    def seed(self):
        first=self.mail('h01','Both requested dates are December 2.');self.ctx.read_mail(first)
        return first

    def hook(self,turn='user-13',**kwargs):return FreshLocalContext(self.ctx,turn,**kwargs)

    def save_task(self,draft,items=None):
        return self.service.call('mail-workspace:saveAnalysis',draft['id'],
            items if items is not None else self.service.mail.store.get_analysis(draft['id'])['drafts'])

    def test_task_only_turn_injects_saved_current_state_without_model_tool_calls(self):
        draft=self.ctx.create_task_draft('Review 10 April',due_date='2027-04-10',call_id='task')
        pending=self.hook(turn='before-approval').snapshot()
        self.assertEqual(pending['current_tasks']['items'][0]['status'],'pending_confirmation')
        self.save_task(draft)
        task=self.service.call('listTodos')[0]
        change=self.service.task_changes.propose(dict(action='update',taskId=task['id'],expectedRevision=1,
            patch=dict(title='Review 13 April',due=dict(mode='value',value=dict(kind='date',date='2027-04-13'))),reason='Confirmed change'))
        self.service.call('task-changes:approve',dict(id=change['id'],expectedHash=change['hash']))
        model=NoToolModel(script=[AIMessage(content='Current state is available without calling list_tasks.')])
        graph=create_deep_agent(model,tools=[],middleware=[self.hook(turn='after-approval')],state_schema=FreshContextState)
        asyncio.run(graph.ainvoke(dict(messages=[HumanMessage(content='这个已经确认的日程现在是什么状态？')]),config={'recursion_limit':256}))
        context=[m.content for m in model.seen[0] if str(m.id).startswith('fresh-context:')][0]
        packet=json.loads(context.split('<local_context_data>',1)[1].split('</local_context_data>',1)[0])
        self.assertEqual(packet['mail'],[]);self.assertEqual(packet['deliverables']['items'],[])
        item=packet['current_tasks']['items'][0];current=item['currentTasks'][0]['task']
        self.assertEqual(item['status'],'saved');self.assertFalse(item['isPending'])
        self.assertEqual(item['saveProgress']['state'],'saved')
        self.assertEqual(current['due']['value']['date'],'2027-04-13');self.assertEqual(current['revision'],2)
        self.assertEqual(current['title'],'Review 13 April');self.assertEqual(item['proposalTitle'],'Review 10 April')
        self.assertEqual(len(model.seen),1);self.assertNotIn('另外两张',context)

    def test_other_conversation_approval_updates_linked_task_without_leaking_other_drafts(self):
        own=self.ctx.create_task_draft('Own task',due_date='2027-04-10',call_id='own');self.save_task(own)
        task=self.service.call('listTodos')[0]
        other=EvidenceAgentTools(self.service,'other-run','other-conversation')
        try:
            secret=other.create_task_draft('Other customer private task',call_id='other');self.save_task(secret)
            change=other.propose_task_change('update',task['id'],1,'User confirmed a new date',
                patch=dict(due=dict(mode='value',value=dict(kind='date',date='2027-04-13'))),call_id='change')
            self.service.call('task-changes:approve',dict(id=change['id'],expectedHash=change['hash']))
        finally:other.close()
        self.ctx.close();self.ctx=EvidenceAgentTools(self.service,'new-run','conversation',self.events.append)
        packet=self.hook(turn='new-turn-after-approval').snapshot()
        items=packet['current_tasks']['items'];self.assertEqual([v['id'] for v in items],[own['id']])
        self.assertEqual(items[0]['currentTasks'][0]['task']['revision'],2)
        self.assertEqual(items[0]['currentTasks'][0]['task']['due']['value']['date'],'2027-04-13')
        self.assertNotIn('Other customer',json.dumps(packet))

    def test_task_snapshot_preserves_business_databases_and_same_turn_cache(self):
        draft=self.ctx.create_task_draft('Saved task',call_id='task');self.save_task(draft)
        with DraftLifecycle(self.service.directory) as life:
            before=[list(db.iterdump()) for db in (life.db,self.service.mail.store.db,self.service.repo.db)]
            hook=self.hook(turn='stable-turn');packet=hook.snapshot()
            after=[list(db.iterdump()) for db in (life.db,self.service.mail.store.db,self.service.repo.db)]
        self.assertEqual(after,before)
        task=self.service.call('listTodos')[0];self.service.call('setCompleted',task['id'],True,task['revision'])
        self.ctx.close();self.ctx=EvidenceAgentTools(self.service,'run-13','conversation',self.events.append)
        with patch.object(self.ctx,'read_draft',side_effect=AssertionError('Resume must reuse the same turn packet')):
            self.assertEqual(self.hook(turn='stable-turn').snapshot(),packet)
        next_packet=self.hook(turn='new-turn').snapshot()
        self.assertEqual(next_packet['current_tasks']['items'][0]['currentTasks'][0]['state'],'completed')
        self.assertTrue(next_packet['current_tasks']['items'][0]['currentTasks'][0]['task']['completed'])

    def test_partial_task_save_is_included_but_superseded_and_dismissed_are_excluded(self):
        def persist(id,created):
            items=drafts_value([dict(tempId=t,parentTempId=None,title=t,due=dict(mode='none')) for t in ('a','b')])
            analysis=dict(id=id,messageIds=[],drafts=items,saved=False,complete=True,createdAt=created)
            self.service.mail.store.save_analyses([analysis])
            with DraftLifecycle(self.service.directory) as life:
                return life.ensure(dict(id=id,kind='task_draft',title='Two tasks',analysisId=id,
                    conversationId=self.ctx.conversation_id,analysis=analysis),'task_draft')
        batch=self.ctx._once('create_task_draft','batch',{},persist);self.save_task(batch,batch['analysis']['drafts'][:1])
        old=self.ctx.create_task_draft('Obsolete',call_id='old')
        new=self.ctx.replace_task_draft(old['id'],1,'Withdrawn',call_id='new')
        self.ctx.discard_draft(new['id'],2,call_id='discard')
        items=current_tasks(self.ctx)['items'];self.assertEqual([v['id'] for v in items],[batch['id']])
        self.assertEqual(items[0]['status'],'partially_saved');self.assertTrue(items[0]['isPending'])
        self.assertEqual(items[0]['saveProgress']['savedCount'],1)
        self.assertEqual(items[0]['saveProgress']['pendingTempIds'],['b'])
        self.assertEqual(len(items[0]['currentTasks']),1)

    def test_task_index_is_bounded_and_reports_truncation(self):
        for n in range(3):
            draft=self.ctx.create_task_draft('Task '+str(n),call_id='task-'+str(n));self.save_task(draft)
        index=current_tasks(self.ctx,limit=2)
        self.assertEqual(len(index['items']),2);self.assertTrue(index['truncated'])
        self.assertEqual(sum(len(v['currentTasks']) for v in index['items']),2)

    def test_official_hook_gives_latest_mail_to_model_that_never_calls_read(self):
        first=self.seed();latest=self.mail('h10','Line 01 remains 2 December. Line 02 requested date is 5 December. Not a warehouse commitment.')
        self.ctx.create_mail_draft('Harbor 00396','Old body',reply_to_local_id=first,call_id='draft')
        model=NoToolModel(script=[AIMessage(content='A response without any tool call.')])
        graph=create_deep_agent(model,tools=[],middleware=[self.hook()],state_schema=FreshContextState)
        result=asyncio.run(graph.ainvoke(dict(messages=[HumanMessage(content='最新这个两号能到吗？仓库还没有问。')]),config={'recursion_limit':256}))
        visible='\n'.join(str(m.content) for m in model.seen[0])
        self.assertIn('Line 02 requested date is 5 December',visible)
        self.assertIn(latest,visible);self.assertIn('Harbor 00396',visible)
        self.assertTrue(self.ctx._is_read(self.service.mail.store.get_local(latest)))
        self.assertEqual(len(model.seen),1)
        self.assertFalse(any(isinstance(m,AIMessage) and m.tool_calls for m in result['messages']))
        self.assertTrue(any(e.get('source',{}).get('id')==latest for e in self.events))

    def test_canonical_index_excludes_read_only_customer_and_other_conversations(self):
        harbor=self.seed();ridge=self.mail('ridge','Same PO number',sender='same-name@ridge.example.invalid');self.ctx.read_mail(ridge)
        for n in ('00381','00396','00412'):
            self.ctx.create_mail_draft('Harbor '+n,'Private draft body not injected',reply_to_local_id=harbor,call_id=n)
        other=EvidenceAgentTools(self.service,'other-run','other-conversation')
        try:other.create_mail_draft('Other conversation order','secret',call_id='different')
        finally:other.close()
        self.mail('ridge-new','Unrelated new customer fact',sender='same-name@ridge.example.invalid')
        latest=self.mail('harbor-new','Harbor latest fact')
        packet=self.hook().snapshot()
        self.assertEqual({x['subject'] for x in packet['deliverables']['items']},{'Harbor 00381','Harbor 00396','Harbor 00412'})
        self.assertEqual([x['id'] for x in packet['mail']],[latest])
        self.assertNotIn('Private draft body',json.dumps(packet));self.assertNotIn('Unrelated new customer fact',json.dumps(packet))

    def test_exact_account_and_identity_and_sender_filter(self):
        self.seed();correct=self.mail('new','Correct same context')
        self.mail('other-account','Wrong account',binding='elsewhere')
        self.mail('other-identity','Old reconfigured mailbox',identity='elsewhere')
        self.mail('other-address','Same display name not same address',sender='orders@another.example.invalid')
        self.assertEqual([x['id'] for x in self.hook().snapshot()['mail']],[correct])

    def test_search_only_and_general_chat_do_not_read_mail(self):
        self.mail('mail','News');self.ctx.search_mail('News')
        with patch.object(self.ctx,'read_mail',side_effect=AssertionError('No explicit context')):
            update=asyncio.run(self.hook().abefore_model({},None))
            self.assertNotIn('messages',update)
        self.seed();self.mail('new','Latest')
        with patch.object(self.ctx,'read_mail',side_effect=AssertionError('Disabled for general chat')):
            self.assertIsNone(asyncio.run(self.hook(enabled=False).abefore_model({},None)))

    def test_each_saved_user_turn_reads_once_and_worker_restart_reuses_packet(self):
        self.seed();latest=self.mail('new','Update')
        first=asyncio.run(self.hook().abefore_model({},None))
        self.assertIsNone(asyncio.run(self.hook().abefore_model(first,None)))
        self.ctx.close();self.ctx=EvidenceAgentTools(self.service,'run-13','conversation',self.events.append)
        with patch.object(self.ctx,'read_mail',side_effect=AssertionError('Cached turn must not reread')):
            restored=asyncio.run(self.hook().abefore_model({},None))
        self.assertEqual(first['fresh_context_snapshot'],restored['fresh_context_snapshot'])
        self.assertEqual(first['messages'][0].id,restored['messages'][0].id)
        newest=self.mail('next','Clarification receives another update')
        packet=self.hook(turn='user-clarification-same-run').snapshot()
        self.assertEqual([x['id'] for x in packet['mail']],[newest])

    def test_body_pagination_budget_and_cumulative_read_are_explicit(self):
        self.seed();latest=self.mail('long','abcdefghijkl')
        packets=[]
        for n in range(3):packets.append(self.hook(turn=str(n),max_chars=4,page_chars=4,max_pages=1).snapshot())
        self.assertEqual([p['mail'][0]['pages'][0]['offset'] for p in packets],[0,4,8])
        self.assertEqual([p['mail'][0]['complete'] for p in packets],[False,False,True])
        self.assertFalse(packets[-1]['mail'][0]['includesEntireBody'])
        self.assertTrue(self.ctx._is_read(self.service.mail.store.get_local(latest)))
        self.assertTrue(all(sum(len(pg['text']) for m in p['mail'] for pg in m['pages'])<=4 for p in packets))

    def test_partial_local_mail_does_not_claim_complete_or_fetch_remote(self):
        self.seed();latest=self.mail('pending','Known prefix only',status='partial')
        packet=self.hook().snapshot();self.assertFalse(packet['mail'][0]['complete']);self.assertFalse(packet['remoteChecked'])
        self.assertFalse(self.ctx._is_read(self.service.mail.store.get_local(latest)))

    def test_crash_rolls_back_read_evidence_with_cache_even_if_source_event_emitted(self):
        self.seed();latest=self.mail('crash','New content');original=self.ctx.read_mail
        def crash(*args,**kwargs):original(*args,**kwargs);raise KeyboardInterrupt('simulate interruption before cache commit')
        with patch.object(self.ctx,'read_mail',crash):
            with self.assertRaises(KeyboardInterrupt):self.hook().snapshot()
        self.assertFalse(self.ctx._is_read(self.service.mail.store.get_local(latest)))
        self.assertEqual(self.ctx.db.execute('SELECT COUNT(*) FROM agent_fresh_context').fetchone()[0],0)
        self.assertTrue(any(e.get('source',{}).get('id')==latest for e in self.events))
        self.assertTrue(self.hook().snapshot()['mail'][0]['complete'])

    def test_concurrent_refresh_discards_mixed_body_and_existing_write_guard_stays(self):
        first=self.seed();latest=self.mail('change','Old new content');original=self.ctx.read_mail
        def refresh(*args,**kwargs):
            result=original(*args,**kwargs)
            if args and args[0]==latest or kwargs.get('message_id')==latest:self.mail('change','Changed during read')
            return result
        with patch.object(self.ctx,'read_mail',refresh):packet=self.hook().snapshot()
        self.assertEqual(packet['mail'][0]['pages'],[]);self.assertFalse(packet['mail'][0]['complete'])
        self.assertFalse(self.ctx._is_read(self.service.mail.store.get_local(latest)))
        guarded=self.ctx.execute('create_task_draft',dict(title='Follow up',source_mail_ids=[first],source_quote='Both'),'write')
        self.assertEqual(guarded['error'],'context_stale')

    def test_candidate_and_mail_limits_report_unexamined_items(self):
        self.seed()
        for n in range(12):self.mail('new-'+str(n),'body '+str(n))
        packet=self.hook(candidate_limit=5,max_mails=2).snapshot()
        self.assertEqual(len(packet['mail']),2);self.assertEqual(len(packet['pendingIds']),3)
        self.assertTrue(packet['candidateWindowLimited']);self.assertFalse(packet['allRelatedMailRead'])

    def test_explicit_selected_old_mail_is_not_lost_outside_arrival_window(self):
        self.seed();selected=self.mail('selected-old','The explicitly chosen source',at='2026-01-01T00:00:00Z')
        for n in range(8):self.mail('later-'+str(n),'Later unrelated item in same customer')
        packet=self.hook(selected_mail_ids=[selected],candidate_limit=2,max_mails=1).snapshot()
        self.assertEqual([x['id'] for x in packet['mail']],[selected])
        self.assertIn('explicitly chosen',packet['mail'][0]['pages'][0]['text'])

    def test_prefetch_includes_bounded_attachment_index_not_attachment_contents(self):
        self.seed()
        latest=self.service.mail.store.put('local','identity','me@example.invalid',dict(id='attachment',subject='Revised file',
            sender='orders@harbor.example.invalid',body='Please use the attached revised CSV.',receivedAt='2026-11-21T08:00:00Z',
            attachments=[dict(name='revision.csv',type='text/csv',size=27)]))['localId']
        packet=self.hook().snapshot();item=packet['mail'][0]
        self.assertEqual(item['id'],latest)
        self.assertEqual(item['attachments'][0]['index'],0)
        self.assertEqual(item['attachments'][0]['name'],'revision.csv')
        self.assertEqual(self.service.files.list_files(),[])

    def test_mail_removed_during_read_becomes_unavailable_not_a_failed_conversation(self):
        self.seed();latest=self.mail('removed','Body that disappears');original=self.ctx.read_mail
        def remove(*args,**kwargs):
            result=original(*args,**kwargs)
            self.service.mail.store.db.execute('DELETE FROM local_mail WHERE local_id=?',(latest,))
            return result
        with patch.object(self.ctx,'read_mail',remove):packet=self.hook().snapshot()
        self.assertEqual(packet['mail'][0]['pages'],[])
        self.assertFalse(packet['mail'][0]['complete'])
        self.assertIn('warning',packet['mail'][0])

    def test_refresh_before_actual_read_rolls_back_undelivered_new_evidence(self):
        first=self.seed();latest=self.mail('before-read','Candidate old body');original=self.ctx.read_mail
        def refresh_before(*args,**kwargs):
            self.mail('before-read','New body not yet delivered to the model')
            return original(*args,**kwargs)
        with patch.object(self.ctx,'read_mail',refresh_before):packet=self.hook().snapshot()
        self.assertEqual(packet['mail'][0]['pages'],[])
        self.assertFalse(self.ctx._is_read(self.service.mail.store.get_local(latest)))
        self.assertIsNone(self.ctx.db.execute('SELECT covered FROM agent_mail_read_evidence WHERE conversation_id=? AND mail_id=?',
                         (self.ctx.evidence_conversation,latest)).fetchone())
        guarded=self.ctx.check_mail_evidence('create_task_draft',dict(source_mail_ids=[first]))
        self.assertEqual(guarded['error'],'context_stale');self.assertIn(latest,guarded['requiredMailIds'])
        next_packet=self.hook(turn='new-user-turn').snapshot()
        self.assertIn('New body not yet delivered',next_packet['mail'][0]['pages'][0]['text'])
        self.assertTrue(self.ctx._is_read(self.service.mail.store.get_local(latest)))

    def test_discarded_read_restores_prior_partial_coverage_exactly(self):
        self.seed();latest=self.mail('partial-race','abcdefghijkl');self.ctx.read_mail(latest,0,3)
        previous=tuple(self.ctx.db.execute('SELECT fingerprint,covered,complete FROM agent_mail_read_evidence '
            'WHERE conversation_id=? AND mail_id=?',(self.ctx.evidence_conversation,latest)).fetchone())
        original=self.ctx.read_mail
        def refresh_before(*args,**kwargs):
            self.mail('partial-race','ABCDEFGHIJKL');return original(*args,**kwargs)
        with patch.object(self.ctx,'read_mail',refresh_before):packet=self.hook().snapshot()
        after=tuple(self.ctx.db.execute('SELECT fingerprint,covered,complete FROM agent_mail_read_evidence '
            'WHERE conversation_id=? AND mail_id=?',(self.ctx.evidence_conversation,latest)).fetchone())
        self.assertEqual(after,previous);self.assertEqual(packet['mail'][0]['pages'],[])


from test_agent_runtime import AgentRuntimeTests, tool_call


class FreshContextServiceTests(unittest.TestCase):
    setUp=AgentRuntimeTests.setUp
    tearDown=AgentRuntimeTests.tearDown
    open_service=AgentRuntimeTests.open_service
    reopen=AgentRuntimeTests.reopen
    model=AgentRuntimeTests.model
    start=AgentRuntimeTests.start
    execute_run=AgentRuntimeTests.execute_run

    def test_ask_user_same_run_resume_prefetches_new_mail_after_service_reopen(self):
        def mail(id,body):
            return self.service.mail.store.put('local','identity','me@example.invalid',dict(
                id=id,subject='Order followup',sender='orders@harbor.example.invalid',
                body=body,receivedAt='2026-11-21T08:00:00Z',attachments=[]))['localId']
        first=mail('first','Original line 02 requested date 2 December.')
        initial_model=self.model(tool_call('ask_user',dict(question='Which order?',options=['00381','00396']),'clarify'))
        run=self.start('Review the selected customer email.',mailIds=[first])
        waiting=self.execute_run(run)
        self.assertEqual(waiting['status'],'waiting',waiting.get('error'))
        first_packets=[m for m in initial_model.seen[0] if isinstance(m,HumanMessage) and str(m.id).startswith('fresh-context:')]
        self.assertEqual(len(first_packets),1)
        self.reopen()
        newest=mail('new','Latest correction: line 02 requested date 5 December. Warehouse not yet confirmed.')
        model=self.model(AIMessage(content='The relevant latest source is now in context.'))
        resumed=self.service.call('agent:resumeRun',dict(id=run['id'],answer='00396',questionId=waiting['clarification']['id']))
        completed=self.execute_run(resumed)
        self.assertEqual(completed['status'],'completed',completed.get('error'))
        packets=[m for m in model.seen[0] if isinstance(m,HumanMessage) and str(m.id).startswith('fresh-context:')]
        newest_packets=[m for m in packets if newest in str(m.content)]
        self.assertEqual(len(newest_packets),1)
        self.assertNotEqual(first_packets[0].id,newest_packets[0].id)
        self.assertIn('line 02 requested date 5 December',newest_packets[0].content)
        self.assertTrue(any(s['id']==newest for s in completed['sources']))
        proof=EvidenceAgentTools(self.service,'verification',run['conversationId'])
        try:
            self.assertTrue(proof._is_read(self.service.mail.store.get_local(newest)))
            self.assertEqual(proof.db.execute('SELECT COUNT(*) FROM agent_fresh_context').fetchone()[0],2)
        finally:proof.close()
        turns=self.service.call('agent:getConversation',run['conversationId'])['turns']
        self.assertEqual(sum(t['content']=='00396' for t in turns),1)


del AgentRuntimeTests
if __name__=='__main__':unittest.main()
