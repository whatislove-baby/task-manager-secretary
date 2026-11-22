"""Parent/subagent share actual immutable-file review evidence without re-calling vision."""
import asyncio
from io import BytesIO
import json
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import patch
from pypdf import PdfWriter
from task_backend.agent_tools import AgentTools
from task_backend.service import BackendService


class DocumentReviewCoverageTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.s=BackendService(Path(self.tmp.name),worker=True);self.addCleanup(self.s.close)
        self.ctx=AgentTools(self.s,'run','chat',lambda event:None);self.addCleanup(self.ctx.close)
        self.s.settings.for_request=lambda:dict(apiKey='synthetic',baseUrl='https://example.invalid',model='test/vision')
        writer=PdfWriter();writer.add_blank_page(100,100);writer.add_blank_page(100,100)
        stream=BytesIO();writer.write(stream)
        self.file=self.s.files._save('two-pages.pdf',stream.getvalue(),'generated')
        self.enterContext(patch('socket.socket.connect',side_effect=AssertionError('No network')))

    def review(self,pages,status='pass',issues=None):
        body=dict(pages=[dict(page=p,status=status,blockingIssues=issues or [],suggestions=[]) for p in pages],summary='Actual fixture page check')
        calls=[]
        class Vision:
            async def ainvoke(_,messages):
                calls.append(messages)
                return SimpleNamespace(content=json.dumps(body),response_metadata={'finish_reason':'stop'})
        self.ctx.vision_model_factory=lambda config:Vision()
        result=asyncio.run(self.ctx.view_file(self.file['id'],pages,status+str(len(issues or [])),purpose='layout',call_id='review'))
        self.assertEqual(len(calls),1);self.assertNotIn('error',result)
        return result

    def validate(self):return self.ctx.validate_file(self.file['id'])

    def test_no_review_render_success_is_not_visual_success(self):
        result=self.validate();self.assertTrue(result['rendered'])
        self.assertFalse(result['visualReviewed']);self.assertFalse(result['layoutReviewCoverage']['allPassed'])
        self.assertEqual(result['layoutReviewCoverage']['missingPages'],[1,2])

    def test_child_review_visible_to_parent_without_new_provider_call_or_proof_write(self):
        self.review([1,2])
        before='\n'.join(self.ctx.db.iterdump())
        self.ctx.vision_model_factory=lambda config:self.fail('Validation must not call vision')
        parent=AgentTools(self.s,'new-run','new-chat',lambda event:None)
        try:result=parent.validate_file(self.file['id'])
        finally:parent.close()
        self.assertTrue(result['visualReviewed']);self.assertTrue(result['layoutReviewCoverage']['allPassed'])
        self.assertEqual(result['layoutReviewCoverage']['passedPages'],[1,2])
        self.assertEqual(result['layoutReviewCoverage']['pages'][0]['model'],'test/vision')
        self.assertEqual(before,'\n'.join(self.ctx.db.iterdump()))
        stored=self.s.files.get(self.file['id'])['validation']
        self.assertFalse(stored['visualReviewed']);self.assertNotIn('layoutReviewCoverage',stored)

    def test_partial_page_then_later_uncertain_is_complete_but_not_passed(self):
        self.review([1]);partial=self.validate()
        self.assertFalse(partial['visualReviewed']);self.assertEqual(partial['layoutReviewCoverage']['missingPages'],[2])
        self.review([2]);self.assertTrue(self.validate()['layoutReviewCoverage']['allPassed'])
        self.review([2],'uncertain');result=self.validate()
        self.assertTrue(result['visualReviewed']);self.assertFalse(result['layoutReviewCoverage']['allPassed'])
        self.assertEqual(result['layoutReviewCoverage']['passedPages'],[1])
        self.assertEqual(result['layoutReviewCoverage']['pages'][1]['status'],'uncertain')

    def test_wrong_hash_plain_observation_and_malformed_proof_not_counted(self):
        good=self.review([1,2]);self.ctx.db.execute("DELETE FROM agent_tool_results WHERE operation='view_file'")
        variants=[]
        wrong=dict(good,sha256='wrong');variants.append(wrong)
        plain=dict(good,purpose='read');variants.append(plain)
        broken=dict(good,layoutReview=dict(pages=[],summary='All fine',passed=True));variants.append(broken)
        for i,value in enumerate(variants):
            self.ctx.db.execute('INSERT INTO agent_tool_results VALUES(?,?,?,?,?,?,?)',(str(i),'run','chat','view_file','hash',str(i),json.dumps(value)))
        self.assertFalse(self.validate()['visualReviewed'])
        self.assertEqual(self.validate()['layoutReviewCoverage']['reviewedPages'],[])

    def test_same_bytes_different_file_id_does_not_inherit_unbound_review(self):
        self.review([1,2]);data=self.s.files.bytes(self.file['id'])
        self.file=self.s.files._save('another.pdf',data,'generated')
        self.assertFalse(self.validate()['layoutReviewCoverage']['complete'])

if __name__=='__main__':unittest.main()
