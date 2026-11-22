"""Application integration using the official harness and offline model boundary.

Includes genuine checkpoints written by the pre-migration create_agent graph.
No test sends email, reaches a provider, or authorizes an external operation.
"""
import asyncio
import os
from pathlib import Path
import tempfile
from typing import NotRequired
import unittest
from unittest.mock import patch

from langchain.agents import AgentState, create_agent
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.tools import tool
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from langgraph.checkpoint.sqlite import SqliteSaver
from pydantic import Field

from task_backend.service import BackendService
from test_agent_runtime import CONFIG, ScriptedModel, tool_call


class RecordingModel(ScriptedModel):
    bindings: list = Field(default_factory=list)

    def bind_tools(self, tools, **kwargs):
        self.bindings.append([getattr(t, 'name', None) or t.get('function', {}).get('name') for t in tools])
        return super().bind_tools(tools, **kwargs)


class DeepAgentsRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.enterContext(patch.dict(os.environ, {'LANGSMITH_TRACING': 'false',
            'LANGCHAIN_TRACING_V2': 'false', 'LANGCHAIN_TRACING': 'false'}))
        self.enterContext(patch('socket.socket.connect', side_effect=AssertionError('Network forbidden')))
        self.enterContext(patch('socket.socket.connect_ex', side_effect=AssertionError('Network forbidden')))
        self.temp = tempfile.TemporaryDirectory()
        self.directory = Path(self.temp.name)
        self.service = self.open_service()

    def open_service(self):
        service = BackendService(self.directory, worker=True)
        service.settings.for_request = lambda: dict(CONFIG)
        service.agent.schedule = lambda: None
        return service

    def tearDown(self):
        self.service.close()
        self.temp.cleanup()

    def reopen(self):
        self.service.close()
        self.service = self.open_service()

    def model(self, *responses):
        model = RecordingModel(script=list(responses))
        self.service.agent.model_factory = lambda config, reasoning: model
        return model

    def start(self, content='Read the provided order.', **kwargs):
        return self.service.call('agent:startTurn', dict(text=content, **kwargs))

    def execute(self, run):
        self.assertTrue(asyncio.run(self.service.agent.run_next()))
        result = self.service.call('agent:getRun', run['id'])
        self.assertEqual(result['status'], 'completed', result.get('error'))
        return result

    def test_document_subagent_pdf_page_selection_uses_actual_source_and_is_not_parent_tool(self):
        from test_pdf_page_selection import fixture_pdf
        from pypdf import PdfReader
        from io import BytesIO
        source=self.service.files._save('source.pdf',fixture_pdf(),'local')
        model=self.model(tool_call('task',dict(description='Extract pages3and1 from '+source['id'],subagent_type='document'),'doc'),
            tool_call('extract_pdf_pages',dict(file_id=source['id'],name='excerpt',pages=[3,1]),'pages'),
            AIMessage(content='已保存所选页副本。'),AIMessage(content='已完成选页，原件保留。'))
        result=self.execute(self.start('把PDF第3页和第1页按这个顺序另存。',fileIds=[source['id']]))
        self.assertNotIn('extract_pdf_pages',model.bindings[0]);self.assertIn('extract_pdf_pages',model.bindings[1])
        fid=next(a['id'] for a in result['artifacts'] if a['kind']=='file')
        reader=PdfReader(BytesIO(self.service.files.bytes(fid)))
        self.assertEqual([p.extract_text().strip() for p in reader.pages],['Page 3','Page 1'])
        self.assertEqual(self.service.files.get(fid)['pageSelection']['pages'],[3,1])

    def test_document_receives_actual_user_goal_despite_parent_expanded_outline(self):
        source=self.service.files._save('weekly.md',b'Actual source facts','local')
        original='只写一段简短周报，加一张对比图；不需要照旧版式。'
        model=self.model(
            tool_call('task',dict(description='必须写十章长篇，沿用旧PDF版式。',subagent_type='document'),'doc'),
            AIMessage(content='按真实用户目标保留简短范围。'),AIMessage(content='已完成短汇报。'))
        self.execute(self.start(original,fileIds=[source['id']]))
        child_system=model.seen[1][0].text
        self.assertIn('真实用户目标数据：',child_system)
        self.assertIn(original,child_system)
        self.assertIn(source['id'],child_system)
        self.assertIn('父助手的提纲只是建议',child_system)
        self.assertNotIn('必须写十章',child_system)
        self.assertTrue(any(isinstance(m,HumanMessage) and '必须写十章' in m.text for m in model.seen[1]))

    def test_document_can_locate_imported_source_when_parent_omits_file_ids(self):
        import json
        from task_backend.agent_tools import AgentTools
        source=self.service.files._save('latest.csv',b'name,done\nModule,2\n','mail',source_mail='mail-source')
        other=self.service.files._save('old.csv',b'name,done\nModule,1\n','mail',source_mail='other-mail')
        model=self.model(tool_call('task',dict(description='用邮件附件写简短周报。',subagent_type='document'),'doc'),
            AIMessage(content='拿到已导入附件ID，不需要重复搜索。'),AIMessage(content='已处理。'))
        run=self.start('用本轮已导入邮件的进展附件写简短汇报。')
        ctx=AgentTools(self.service,run['id'],run['conversationId'],lambda e:None)
        try:
            for ident,execution,file in [('import-current',run['id'],source),('import-other','old-run',other)]:
                ctx.db.execute('INSERT INTO agent_tool_results VALUES(?,?,?,?,?,?,?)',(ident,execution,run['conversationId'],'import_attachment','hash','2026-11-21T01:00:00Z',json.dumps(dict(file=file))))
        finally:ctx.close()
        self.execute(run)
        system=model.seen[1][0].text
        self.assertIn(source['id'],system);self.assertIn('mail-source',system)
        self.assertNotIn(other['id'],system)
        self.assertIn('不要因委派遗漏ID重复搜索或导入',system)

    def test_scratch_file_survives_worker_reopen_and_is_not_a_deliverable(self):
        self.model(tool_call('write_file', dict(file_path='/notes/order.md', content='Order A: eight cartons.'), 'write'),
                   AIMessage(content='已保存工作笔记。'))
        first = self.start('记住本单八箱。')
        result = self.execute(first)
        self.assertEqual(result['artifacts'], [])
        self.assertTrue(any(e.get('tool') == 'write_file' and e.get('status') == 'completed' for e in result['events']))
        self.reopen()
        model = self.model(tool_call('read_file', dict(file_path='/notes/order.md'), 'read'),
                           AIMessage(content='工作笔记记载八箱。'))
        self.execute(self.start('看看刚才记下的数量。', conversationId=first['conversationId']))
        self.assertTrue(any(isinstance(m, ToolMessage) and m.name == 'read_file' and 'eight cartons' in m.content
                            for m in model.seen[-1]))
        self.assertFalse(self.service.files.list_files())

    def test_parent_skill_read_cannot_bypass_document_subagent_and_emits_progress(self):
        model = self.model(tool_call('read_file', dict(file_path='/skills/builtin/documents/SKILL.md'), 'skill'),
                           AIMessage(content='已读取文档处理技能，准备处理资料。'))
        result = self.execute(self.start('准备修改一个 PDF。'))
        self.assertNotIn('create_business_document', model.bindings[0])
        self.assertNotIn('transform_csv', model.bindings[0])
        # Lifecycle repair is a common operation, not a specialist capability
        # that may disappear when its SKILL read falls out of summarized context.
        self.assertIn('archive_generated_files', model.bindings[0])
        self.assertIn('restore_generated_file', model.bindings[0])
        self.assertIn('validate_mail_draft', model.bindings[0])
        self.assertNotIn('create_business_document', model.bindings[-1])
        self.assertNotIn('transform_csv', model.bindings[-1])
        self.assertNotIn('create_document', model.bindings[-1])
        self.assertNotIn('format_word', model.bindings[-1])
        self.assertTrue(any(e.get('tool') == 'read_file' and e.get('status') == 'completed' for e in result['events']))

    def test_managed_file_path_read_is_attributed_to_real_file_id(self):
        file = self.service.files._save('order.txt', b'Order A: eight cartons.', 'local')
        self.model(tool_call('read_file', dict(file_path='/files/' + file['id'] + '/content.txt'), 'read'),
                   AIMessage(content='已核实订单为八箱。'))
        result = self.execute(self.start('检查本地订单。'))
        self.assertTrue(any(s['kind'] == 'file' and s['id'] == file['id'] for s in result['sources']))

    def test_official_subagent_inherits_managed_sources_and_skill_tools(self):
        file = self.service.files._save('order.txt', b'Customer Harbor: eight cartons.', 'local')
        model = self.model(
            tool_call('task', dict(description='For customer Harbor, inspect the provided managed order; report quantity only.',
                                  subagent_type='general-purpose'), 'delegate'),
            tool_call('read_file', dict(file_path='/skills/builtin/documents/SKILL.md'), 'child-skill'),
            tool_call('read_file', dict(file_path='/files/' + file['id'] + '/content.txt'), 'child-read'),
            AIMessage(content='Harbor has eight cartons in the supplied order.'),
            AIMessage(content='已核对，Harbor 这单为八箱。'))
        result = self.execute(self.start('请把订单核对工作交给独立子任务。'))
        self.assertTrue(any(s['kind'] == 'file' and s['id'] == file['id'] for s in result['sources']))
        self.assertTrue(any(e.get('tool') == 'task' and e.get('status') == 'completed' for e in result['events']))
        self.assertTrue(any(isinstance(m, ToolMessage) and m.name == 'task' and 'eight cartons' in m.content
                            for m in model.seen[-1]))
        self.assertNotIn('create_business_document', model.bindings[2])
        self.assertEqual(result['partialText'], '已核对，Harbor 这单为八箱。')

    def test_document_subagent_format_word_registry_and_actual_template_patch(self):
        from task_backend.mail_files import make_docx
        from task_backend.document_formats import inspect_docx
        original=self.service.files._save('template.docx',make_docx('Report','Keep this exact source.'),'local')
        model=self.model(
            tool_call('task',dict(description='Change only paragraph spacing in this Word '+original['id'],subagent_type='document'),'doc'),
            tool_call('format_word',dict(file_id=original['id'],name='formatted.docx',changes=[
                dict(target='p1',expected='Keep this exact source.',keep_together=True)]),'patch'),
            AIMessage(content='已局部修改，没有改写正文。'),AIMessage(content='已准备好修订文件，尚未发送。'))
        result=self.execute(self.start('请只改这份Word排版。',fileIds=[original['id']]))
        self.assertNotIn('format_word',model.bindings[0])
        self.assertIn('format_word',model.bindings[1])
        self.assertNotIn('create_mail_draft',model.bindings[1])
        self.assertNotIn('stage_files',model.bindings[1]);self.assertNotIn('publish_file',model.bindings[1])
        self.assertEqual(len(result['artifacts']),1)
        fid=result['artifacts'][0]['id']
        paragraph=inspect_docx(self.service.files.bytes(fid))['paragraphs'][1]
        self.assertEqual(paragraph['text'],'Keep this exact source.')
        self.assertTrue(paragraph['format']['keepTogether'])
        handover=next(m.content for m in model.seen[-1] if isinstance(m,ToolMessage) and m.name=='task')
        self.assertIn(fid,handover);self.assertIn('实际保存文件',handover)

    def test_parent_direct_document_write_is_rejected_even_after_reading_skill(self):
        self.model(tool_call('read_file',dict(file_path='/skills/builtin/documents/SKILL.md'),'read-doc-skill'),
            tool_call('create_document',dict(name='bypass',format='md',title='X',content='Wrong path'),'bypass'),
            AIMessage(content='需要交由文档子助手处理。'))
        result=self.execute(self.start('检查不能绕过文档子任务。'))
        self.assertEqual(result['artifacts'],[])
        self.assertEqual(self.service.files.list_files(),[])

    def test_inspect_file_registry_passes_xlsx_sheet_and_range(self):
        from io import BytesIO
        from openpyxl import Workbook
        book=Workbook();book.active.title='Summary';book.active['A1']='Other'
        sheet=book.create_sheet('Orders');sheet.merge_cells('A1:C1');sheet['A1']='Purchase order'
        sheet['B3']='0007';sheet['C3']=12;out=BytesIO();book.save(out);book.close()
        record=self.service.files._save('orders.xlsx',out.getvalue(),'local')
        model=self.model(tool_call('inspect_file',dict(file_id=record['id'],sheet_name='Orders',cell_range='B3:C3',limit=1),'range'),
            AIMessage(content='该行料号为0007，数量12。'))
        self.execute(self.start('只查看Orders表B3:C3。',fileIds=[record['id']]))
        import json
        info=json.loads(next(m.content for m in model.seen[-1] if isinstance(m,ToolMessage) and m.name=='inspect_file'))['structure']
        self.assertEqual([(c['coordinate'],c['value']) for c in info['cells']],[('B3','0007'),('C3',12)])

    def test_document_model_budget_returns_real_partial_files_to_parent(self):
        responses=[tool_call('task',dict(description='Create a simple report, keep source uncertainty.',subagent_type='document'),'doc')]
        responses.extend(tool_call('create_document',dict(name='version-'+str(n),format='md',title='Report',content='Source not yet verified.'),'v'+str(n)) for n in range(40))
        responses.append(AIMessage(content='已保留当前文件；文档子任务部分完成。'))
        model=self.model(*responses)
        result=self.execute(self.start('生成报告。'))
        self.assertEqual(len(model.seen),42)
        handover=next(m.content for m in model.seen[-1] if isinstance(m,ToolMessage) and m.name=='task')
        self.assertIn('部分完成',handover)
        for artifact in result['artifacts']:self.assertIn(artifact['id'],handover)
        self.assertTrue(any(e.get('type')=='document_completion' and e.get('limited') for e in result['events']))

    def test_genuine_legacy_create_agent_checkpoint_continues_in_deepagents(self):
        # Seed from the old framework itself, before any new harness run exists.
        # This is not a new DeltaChannel checkpoint merely reopened as old code.
        old = self.start('Earlier order A has eight cartons.')

        class LegacyDesktopState(AgentState):
            host_run_id: NotRequired[str]

        path = self.directory / 'agent-checkpoints.sqlite'
        with SqliteSaver.from_conn_string(str(path)) as saver:
            saver.serde = JsonPlusSerializer(allowed_msgpack_modules=[])
            graph = create_agent(ScriptedModel(script=[AIMessage(content='Recorded order A and its quantity.')]),
                                 tools=[], state_schema=LegacyDesktopState, checkpointer=saver)
            config = {'configurable': {'thread_id': old['conversationId']}}
            graph.invoke({'messages': [HumanMessage(content='Earlier order A has eight cartons.', id=old['id'] + ':user')],
                          'host_run_id': old['id']}, config)
            saved = saver.get_tuple(config)
            self.assertIsInstance(saved.checkpoint['channel_values']['messages'], list)
            self.assertEqual(len(saved.checkpoint['channel_values']['messages']), 2)
        self.service.agent._update(old['id'], dict(status='completed', partialText='Recorded order A and its quantity.'))
        self.reopen()
        model = self.model(AIMessage(content='Earlier order A contained eight cartons.'))
        followup = self.start('What quantity did we discuss earlier?', conversationId=old['conversationId'])
        self.execute(followup)
        self.assertTrue(any(isinstance(m, HumanMessage) and 'Earlier order A has eight cartons.' == m.content
                            for m in model.seen[0]))
        self.assertTrue(any(isinstance(m, AIMessage) and 'Recorded order A' in m.content for m in model.seen[0]))
        # A second reopening checks that the first migrated write is durable too.
        self.reopen()
        again = self.model(AIMessage(content='The migrated conversation still contains the original order.'))
        self.execute(self.start('再核对一次原订单。', conversationId=old['conversationId']))
        self.assertEqual(sum(isinstance(m, HumanMessage) and m.content == 'Earlier order A has eight cartons.'
                             for m in again.seen[0]), 1)

    def test_genuine_legacy_pending_read_file_resumes_as_managed_file(self):
        file = self.service.files._save('legacy-order.txt', b'Legacy order: eight cartons.', 'local')
        old = self.start('Please read the selected legacy order.')

        class LegacyDesktopState(AgentState):
            host_run_id: NotRequired[str]

        @tool
        def read_file(file_id: str) -> str:
            """Legacy managed-file tool signature before official filesystem adoption."""
            raise AssertionError('The old graph must be checkpointed before this tool executes')

        with SqliteSaver.from_conn_string(str(self.directory / 'agent-checkpoints.sqlite')) as saver:
            saver.serde = JsonPlusSerializer(allowed_msgpack_modules=[])
            graph = create_agent(ScriptedModel(script=[tool_call('read_file', {'file_id': file['id']}, 'legacy-read')]),
                                 tools=[read_file], state_schema=LegacyDesktopState,
                                 checkpointer=saver, interrupt_before=['tools'])
            config = {'configurable': {'thread_id': old['conversationId']}}
            graph.invoke({'messages': [HumanMessage(content='Please read the selected legacy order.', id=old['id'] + ':user')],
                          'host_run_id': old['id']}, config)
            self.assertEqual(graph.get_state(config).next, ('tools',))
        self.service.agent._update(old['id'], dict(status='paused', _started=True, _executing=False))
        self.reopen()
        model = self.model(AIMessage(content='旧订单记载八箱，已经重新读取。'))
        resumed = self.service.call('agent:resumeRun', {'id': old['id']})
        result = self.execute(resumed)
        tool_result = next(m for m in model.seen[0] if isinstance(m, ToolMessage) and m.tool_call_id == 'legacy-read')
        self.assertNotEqual(tool_result.status, 'error', tool_result.content)
        self.assertIn('eight cartons', tool_result.content)
        self.assertTrue(any(s['kind'] == 'file' and s['id'] == file['id'] for s in result['sources']))


if __name__ == '__main__':
    unittest.main()
