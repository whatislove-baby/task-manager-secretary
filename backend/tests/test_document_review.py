import asyncio
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest

from task_backend.agent_tools import AgentTools
from task_backend.document_review import parse_layout_review
from task_backend.mail_files import make_pdf
from task_backend.service import BackendService


def review(pages=(1,),status='pass',issues=None):
    return json.dumps(dict(pages=[dict(page=p,status=status,blockingIssues=issues or [],suggestions=[]) for p in pages],summary='Checked'),ensure_ascii=False)


class ReviewContractTests(unittest.TestCase):
    def test_exact_pages_and_status_are_required(self):
        self.assertTrue(parse_layout_review(review((1,2)),[1,2])['passed'])
        for answer in ('Looks fine',review((1,)),review((1,1)),review((1,3)),
                       review((1,2),issues=['clipped']),review((1,2),'needs_fix')):
            with self.subTest(answer=answer),self.assertRaises(ValueError):
                parse_layout_review(answer,[1,2])
        self.assertFalse(parse_layout_review(review(status='uncertain'),[1])['passed'])
        self.assertFalse(parse_layout_review(review(status='needs_fix',issues=['Table clipped at right']),[1])['passed'])

    def test_optional_advice_is_not_a_blocker(self):
        data=json.loads(review());data['pages'][0]['suggestions']=['Optional smaller heading']
        self.assertTrue(parse_layout_review(json.dumps(data),[1])['passed'])


class LayoutVisionTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.service=BackendService(Path(self.tmp.name),worker=True);self.addCleanup(self.service.close)
        self.events=[];self.ctx=AgentTools(self.service,'layout-test','chat',self.events.append);self.addCleanup(self.ctx.close)
        self.service.settings.for_request=lambda:dict(apiKey='fake-secret',baseUrl='https://example.invalid',model='fake')
        source=Path(self.tmp.name)/'input.pdf';source.write_bytes(make_pdf('Report','Actual PDF page'))
        self.file=self.service.files.import_local(source)

    def test_layout_review_caches_only_valid_proof_separate_from_general_read(self):
        calls=[]
        class Model:
            async def ainvoke(_,messages):
                calls.append(messages)
                return SimpleNamespace(content=review(),response_metadata={'finish_reason':'stop'})
        self.ctx.vision_model_factory=lambda _:Model()
        async def run():
            a=await self.ctx.view_file(self.file['id'],purpose='layout',call_id='a')
            b=await self.ctx.view_file(self.file['id'],purpose='layout',call_id='b')
            c=await self.ctx.view_file(self.file['id'],purpose='read',call_id='c')
            return a,b,c
        a,b,c=asyncio.run(run())
        self.assertEqual(a,b);self.assertEqual(len(calls),2)
        self.assertEqual(a['pageCount'],1);self.assertTrue(a['layoutReview']['passed'])
        self.assertNotIn('layoutReview',c)
        self.assertTrue(any(p.get('type')=='image_url' for p in calls[0][1].content))
        self.assertNotIn('fake-secret',str(self.events))
        self.assertNotIn('base64',str(self.events))

    def test_malformed_review_fails_without_caching_or_fabricating_pass(self):
        class Model:
            async def ainvoke(_,messages):
                return SimpleNamespace(content='Everything is good',response_metadata={'finish_reason':'stop'})
        self.ctx.vision_model_factory=lambda _:Model()
        result=asyncio.run(self.ctx.view_file(self.file['id'],purpose='layout',call_id='bad'))
        self.assertEqual(result['error'],'vision_failed')
        self.assertNotIn('layoutReview',result)
        self.assertEqual(self.ctx.db.execute("SELECT count(*) FROM agent_tool_results WHERE operation='view_file'").fetchone()[0],0)

    def test_invalid_purpose_does_not_call_provider(self):
        self.ctx.vision_model_factory=lambda _:self.fail('Must reject first')
        result=asyncio.run(self.ctx.view_file(self.file['id'],purpose='approve',call_id='bad'))
        self.assertEqual(result['error'],'vision_failed')
