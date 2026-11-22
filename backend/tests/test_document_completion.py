"""Real official graph boundaries preserve saved output instead of endless polish."""
from network_guard import no_external_network
import asyncio
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from langchain.agents import create_agent
from langchain_core.messages import AIMessage,HumanMessage
from langchain_core.messages import ToolMessage
from langchain_core.tools import tool
from langchain_core.outputs import ChatGeneration,ChatResult
from task_backend.agent_tools import AgentTools
from task_backend.service import BackendService
from task_backend.document_completion import document_completion_middleware,document_outputs
from test_agent_runtime import ScriptedModel,tool_call

class AsyncScriptedModel(ScriptedModel):
    async def _agenerate(self,messages,stop=None,run_manager=None,**kwargs):
        return ChatResult(generations=[ChatGeneration(message=self._next(messages))])

class DocumentCompletionTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.service=BackendService(Path(self.tmp.name),worker=True);self.addCleanup(self.service.close)
        self.events=[];self.ctx=AgentTools(self.service,'doc-run','doc-chat',self.events.append);self.addCleanup(self.ctx.close)
        self.enterContext(no_external_network())
        self.enterContext(patch.dict(os.environ,{'LANGSMITH_TRACING':'false','LANGCHAIN_TRACING_V2':'false'}))
    def agent(self,script,limit=3):
        ctx=self.ctx
        @tool
        async def create_document(n:int)->dict:
            """Save an independently requested report."""
            return ctx.create_document('report-'+str(n),'md','Report','Facts from the source.',[],call_id='write-'+str(n))
        @tool
        async def read_source()->dict:
            """Read another explicitly relevant source section."""
            return {'text':'Source information read without writing output.'}
        model=AsyncScriptedModel(script=script)
        graph=create_agent(model,tools=[create_document,read_source],middleware=document_completion_middleware(ctx,max_tool_calls=limit,review_after=2))
        return graph,model
    def test_endless_polish_stops_at_official_boundary_with_actual_files(self):
        graph,model=self.agent([tool_call('create_document',{'n':n},'c'+str(n)) for n in range(8)])
        result=asyncio.run(graph.ainvoke({'messages':[HumanMessage(content='两页左右，资料里的不确定情况要保留。')]},config={'recursion_limit':100}))
        self.assertEqual(len(model.seen),3)
        self.assertEqual(next(e['toolCalls'] for e in self.events if e.get('type')=='document_completion'),3)
        self.assertEqual(len(self.service.files.list_files()),3)
        final=result['messages'][-1].content
        self.assertIn('部分完成',final)
        for file in self.service.files.list_files():self.assertIn(file['id'],final)
        self.assertNotIn('已全部完成',final)
        self.assertTrue(any(e.get('type')=='document_completion' and e.get('limited') for e in self.events))
    def test_parallel_overrun_does_not_execute_over_limit_writes(self):
        batch=AIMessage(content='',tool_calls=[dict(name='create_document',args={'n':n},id='p'+str(n),type='tool_call') for n in range(5)])
        graph,model=self.agent([batch],limit=2)
        result=asyncio.run(graph.ainvoke({'messages':[HumanMessage(content='Prepare files')]},config={'recursion_limit':100}))
        self.assertEqual(len(self.service.files.list_files()),2)
        self.assertIn('部分完成',result['messages'][-1].content)
        self.assertEqual(len(model.seen),1)
    def test_success_final_includes_real_file_not_unrelated_conversation_output(self):
        unrelated=self.ctx.create_document('old','md','Old','Unrelated customer',[],call_id='unrelated')['id']
        graph,model=self.agent([tool_call('create_document',{'n':1}),AIMessage(content='已准备好正文。')])
        result=asyncio.run(graph.ainvoke({'messages':[HumanMessage(content='Write report')]}))
        final=result['messages'][-1].content
        self.assertNotIn(unrelated,final)
        self.assertIn('实际保存文件',final)
        self.assertIn('尚未验证',final)
        self.assertEqual(len(model.seen),2)
    def test_each_subtask_gets_independent_official_run_budget(self):
        graph,model=self.agent([tool_call('create_document',{'n':n},'c'+str(n)) for n in range(6)])
        for _ in range(2):asyncio.run(graph.ainvoke({'messages':[HumanMessage(content='Next independent document')]}))
        self.assertEqual(len(model.seen),6)
        self.assertEqual(len(self.service.files.list_files()),6)

    def test_checkpoint_keeps_file_evidence_if_summary_evicts_tool_messages(self):
        graph,_=self.agent([tool_call('create_document',{'n':1}),AIMessage(content='Done')])
        result=asyncio.run(graph.ainvoke({'messages':[HumanMessage(content='Write report')]}))
        self.assertEqual(len(result['document_output_ids']),1)
        current=document_outputs(self.ctx,{**result,'messages':[]})
        self.assertEqual([f['id'] for f in current['files']],result['document_output_ids'])

    def test_no_stale_corrupt_or_imported_file_is_claimed_as_current_delivery(self):
        from task_backend.mail_files import make_docx
        from task_backend.draft_lifecycle import DraftLifecycle
        original=self.service.files._save('source.docx',make_docx('Source','Keep source'),'local')
        first=self.ctx.edit_word(original['id'],'v1',[dict(target='p1',expected='Keep source',text='Current text')],call_id='v1')
        second=self.ctx.format_word(first['id'],'v2',[dict(target='p1',expected='Current text',keep_together=True)],call_id='v2')
        record=self.service.files.get(second['id'])
        state=dict(messages=[],document_output_ids=[original['id'],first['id'],second['id']],
            document_page_views={first['id']+':'+first['file']['sha256']:[1],second['id']+':wrong-hash':[1,2]})
        result=document_outputs(self.ctx,state)
        self.assertEqual([f['id'] for f in result['files']],[second['id']])
        self.assertEqual(result['files'][0]['pagesViewed'],[])
        (self.service.files.root/second['id']).write_bytes(b'corrupt')
        result=document_outputs(self.ctx,state)
        self.assertEqual(result['files'],[])
        self.assertEqual(result['unavailableIds'],[second['id']])

    def test_sandbox_published_file_shape_is_included_without_claiming_it_was_sent(self):
        import json
        record=self.ctx.create_document('sandbox-chart','md','Chart','Generated output',[],call_id='sandbox')['file']
        message=ToolMessage(name='publish_file',tool_call_id='publish',content=json.dumps(
            {'file':record,'status':'generated_not_sent','notice':'Saved in managed storage'}))
        result=document_outputs(self.ctx,{'messages':[message]})
        self.assertEqual([f['id'] for f in result['files']],[record['id']])

    def test_default_more_than_sixteen_source_reads_can_still_generate_and_finish(self):
        script=[tool_call('read_source',{},'read-'+str(i)) for i in range(18)]
        script.extend([tool_call('create_document',{'n':1},'write'),AIMessage(content='The requested report is ready.')])
        graph,model=self.agent(script,limit=None)
        result=asyncio.run(graph.ainvoke({'messages':[HumanMessage(content='Read all relevant sections and write the report.')]},config={'recursion_limit':300}))
        self.assertEqual(len(model.seen),20)
        self.assertEqual(len(result['document_output_ids']),1)
        self.assertIn(result['document_output_ids'][0],result['messages'][-1].text)
        self.assertFalse(any(e.get('limited') for e in self.events))

    def test_default_official_model_budget_returns_partial_with_actual_artifacts(self):
        script=[tool_call('create_document',{'n':1},'save')]
        script.extend(tool_call('read_source',{},'read-'+str(i)) for i in range(45))
        graph,model=self.agent(script,limit=None)
        result=asyncio.run(graph.ainvoke({'messages':[HumanMessage(content='Finish the report.')]},config={'recursion_limit':500}))
        self.assertEqual(len(model.seen),40)
        self.assertTrue(result['document_model_limited'])
        self.assertIn('部分完成',result['messages'][-1].text)
        self.assertIn(result['document_output_ids'][0],result['messages'][-1].text)

    def reviewed_pdf(self):
        output=self.ctx.create_document('ready','pdf','Report','A compact, sourced report.',[],call_id='report-pdf')
        validation=self.ctx.validate_file(output['id'])
        self.assertTrue(validation['rendered'])
        return output

    def review_payload(self,output,status='pass',issues=None,pages=None):
        return dict(fileId=output['id'],sha256=output['file']['sha256'],vision=True,pages=pages or [1],
            layoutReview=dict(passed=status=='pass',pages=[dict(page=p,status=status,blockingIssues=issues or [],suggestions=['More whitespace is optional']) for p in pages or [1]]))

    def test_complete_current_layout_is_distinct_from_plain_vision_observation(self):
        import json
        output=self.reviewed_pdf()
        def snapshot(payload):
            return document_outputs(self.ctx,dict(document_output_ids=[output['id']],messages=[
                ToolMessage(name='view_file',tool_call_id='look',content=json.dumps(payload))]))['files'][0]
        plain=snapshot(dict(fileId=output['id'],sha256=output['file']['sha256'],vision=True,pages=[1],observation='Looks fine'))
        self.assertFalse(plain['layoutReady'])
        self.assertEqual(plain['pagesViewed'],[1])
        checked=snapshot(self.review_payload(output))
        self.assertTrue(checked['layoutReady'])
        self.assertEqual(checked['layoutPassedPages'],[1])
        self.assertEqual(checked['layoutIssues'],[])

    def test_blocking_uncertain_wrong_hash_and_malformed_review_cannot_clear_report(self):
        import json
        output=self.reviewed_pdf()
        incorrect=self.review_payload(output);incorrect['sha256']='wrong'
        malformed=self.review_payload(output,pages=[1,2]);malformed['layoutReview']['pages'].pop()
        missing=self.review_payload(output);missing['layoutReview'].pop('pages')
        for payload in [self.review_payload(output,'needs_fix',['Clipped figure']),self.review_payload(output,'uncertain'),incorrect,malformed,missing]:
            with self.subTest(payload=payload):
                result=document_outputs(self.ctx,dict(document_output_ids=[output['id']],messages=[
                    ToolMessage(name='view_file',tool_call_id='look',content=json.dumps(payload))]))
                self.assertFalse(result['files'][0]['layoutReady'])

    def test_layout_requires_all_pages_and_keeps_latest_per_page_review(self):
        from task_backend.document_completion import _layout_review
        output=self.reviewed_pdf();getter=self.service.files.get
        def get(fid):
            result=getter(fid)
            result['validation']={**result.get('validation',{}),'pageCount':2}
            return result
        with patch.object(self.service.files,'get',side_effect=get):
            state=dict(messages=[],document_output_ids=[output['id']],document_layout_reviews=_layout_review(self.review_payload(output)))
            self.assertFalse(document_outputs(self.ctx,state)['files'][0]['layoutReady'])
            state['document_layout_reviews']=_layout_review(self.review_payload(output,pages=[1,2]))
            self.assertTrue(document_outputs(self.ctx,state)['files'][0]['layoutReady'])
        # Checkpoint evidence survives summary eviction and outranks an older
        # residual message; a newer failure may never become an old pass.
        import json
        state=dict(document_output_ids=[output['id']],document_layout_reviews=_layout_review(self.review_payload(output,'needs_fix',['overlap'])),
            messages=[ToolMessage(name='view_file',tool_call_id='old',content=json.dumps(self.review_payload(output)))])
        snapshot=document_outputs(self.ctx,state)['files'][0]
        self.assertFalse(snapshot['layoutReady'])
        self.assertEqual(snapshot['layoutIssues'][0]['issues'],['overlap'])

    def test_official_graph_persists_readiness_and_exposes_no_optional_rewrite_prompt(self):
        output=self.reviewed_pdf()
        ctx=self.ctx
        @tool
        async def convert_to_pdf()->dict:
            """Return the actual newly converted file."""
            return output
        @tool
        async def view_file()->dict:
            """Review the actual generated file, not unrelated previous files."""
            return self.review_payload(output)
        model=AsyncScriptedModel(script=[tool_call('convert_to_pdf',{},'convert'),tool_call('view_file',{},'review'),AIMessage(content='Report ready.')])
        graph=create_agent(model,tools=[convert_to_pdf,view_file],middleware=document_completion_middleware(ctx))
        result=asyncio.run(graph.ainvoke({'messages':[HumanMessage(content='Return report and PDF')]}))
        self.assertIn(output['id'],result['document_output_ids'])
        self.assertIn(output['id']+':'+output['file']['sha256'],result['document_layout_reviews'])
        final_system=model.seen[-1][0].content
        self.assertIn('"layoutReady": true',final_system)
        self.assertIn('不要继续为留白',final_system)
        self.assertIn('当前版本全部页面未发现',result['messages'][-1].text)
        self.assertEqual(len(model.seen),3)
        compacted=document_outputs(ctx,{**result,'messages':[]})
        self.assertTrue(compacted['files'][0]['layoutReady'])

if __name__=='__main__':unittest.main()
