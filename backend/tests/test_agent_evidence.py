"""Evidence regression uses cached synthetic mail only, sockets forbidden."""
import json
import unittest
from unittest.mock import patch
from task_backend.agent_evidence import EvidenceAgentTools
try:
    from .test_agent_tools import AgentToolsTests
except ImportError:
    from test_agent_tools import AgentToolsTests


class AgentEvidenceTests(unittest.TestCase):
    _fixture_setup=AgentToolsTests.setUp
    mail=AgentToolsTests.mail
    file=AgentToolsTests.file
    def setUp(self):
        self._fixture_setup()
        self.ctx.close()
        self.ctx=EvidenceAgentTools(self.service,'fixture-execution','fixture-conversation',self.events.append)
    def restart(self,execution='another-execution',conversation='fixture-conversation',selected=None):
        self.ctx.close();self.ctx=EvidenceAgentTools(self.service,execution,conversation,self.events.append,selected)
    def draft(self,id,call='draft'):
        return self.ctx.execute('create_task_draft',dict(title='Project follow-up',source_mail_ids=[id],source_quote='Project'),call)
    def put(self,remote,body='Project latest',sender='teacher@example.invalid',binding='fixture-binding',at='2026-11-21T12:00:00Z'):
        return self.service.mail.store.put(binding,'fixture-identity','student@example.invalid',dict(id=remote,body=body,subject='Project',sender=sender,receivedAt=at,attachments=[]))['localId']

    def test_search_does_not_count_as_read_and_first_source_must_be_read(self):
        id=self.mail()
        self.ctx.execute('search_mail',{'query':'Project'},'search')
        self.assertEqual(self.draft(id)['error'],'context_stale')
        self.assertEqual(self.ctx.db.execute('SELECT count(*) FROM agent_tool_results').fetchone()[0],0)
        self.ctx.execute('read_mail',{'message_id':id},'read')
        self.assertEqual(self.draft(id)['kind'],'task_draft')

    def test_complete_read_requires_all_pages_and_resets_on_body_change(self):
        id=self.mail(body='ProjectABCDEF')
        result=self.ctx.read_mail(id,0,7);self.assertFalse(result['evidenceComplete'])
        self.assertEqual(self.draft(id)['error'],'context_stale')
        self.ctx.read_mail(id,9,4)  # deliberate missing characters 7..9
        self.assertEqual(self.draft(id)['error'],'context_stale')
        self.assertTrue(self.ctx.read_mail(id,7,2)['evidenceComplete'])
        self.assertEqual(self.draft(id)['kind'],'task_draft')
        self.mail(body='Project changed longer')
        self.assertFalse(self.ctx.read_mail(id,7,7)['evidenceComplete'])
        self.assertEqual(self.draft(id,'changed')['error'],'context_stale')
        self.assertTrue(self.ctx.read_mail(id)['evidenceComplete'])
        self.assertEqual(self.draft(id,'changed')['kind'],'task_draft')

    def test_partial_local_body_cannot_be_credited_even_when_all_cached_text_read(self):
        id=self.mail(body='Project',bodyStatus='partial')
        self.assertFalse(self.ctx.read_mail(id)['evidenceComplete'])
        self.assertEqual(self.draft(id)['error'],'context_stale')

    def test_read_evidence_persists_across_turns_but_not_new_conversation(self):
        id=self.mail();self.ctx.read_mail(id);self.restart()
        self.assertEqual(self.draft(id)['kind'],'task_draft')
        self.restart(execution='fresh-execution',conversation='fresh-conversation')
        self.assertEqual(self.draft(id)['error'],'context_stale')

    def test_newer_existing_unread_mail_requires_read_not_search(self):
        first=self.mail();later=self.put('later')
        self.ctx.read_mail(first)
        failed=self.draft(first)
        self.assertEqual(failed['requiredMailIds'],[later])
        self.ctx.search_mail(sender='teacher@example.invalid')
        self.assertEqual(self.draft(first)['error'],'context_stale')
        self.ctx.read_mail(later)
        self.assertEqual(self.draft(first)['kind'],'task_draft')

    def test_new_import_and_changed_read_mail_require_reread(self):
        id=self.mail();self.ctx.read_mail(id)
        newer=self.put('new-arrival')
        self.assertEqual(self.draft(id)['requiredMailIds'],[newer])
        self.ctx.read_mail(newer);self.mail(body='Project changed')
        self.assertIn(id,self.draft(id)['requiredMailIds'])
        self.ctx.read_mail(id)
        self.assertEqual(self.draft(id)['kind'],'task_draft')

    def test_old_dated_mail_imported_after_read_is_still_new_evidence(self):
        id=self.mail();self.ctx.read_mail(id)
        delayed=self.put('delayed',at='2026-11-19T00:00:00Z')
        self.assertIn(delayed,self.draft(id)['requiredMailIds'])

    def test_exact_customer_and_account_scope_not_display_name_or_all_read_mail(self):
        a=self.mail(sender='Same Person <teacher@example.invalid>')
        b=self.mail(sender='Same Person <other@example.invalid>',remote='other')
        self.ctx.read_mail(a);self.ctx.read_mail(b)
        self.put('other-new',sender='Same Person <other@example.invalid>')
        self.put('other-account',binding='another-binding')
        self.assertEqual(self.draft(a)['kind'],'task_draft')
        self.assertEqual(self.draft(b,'other-draft')['error'],'context_stale')

    def test_plain_reminder_does_not_scan_mail_and_selection_is_not_read(self):
        id=self.mail();self.ctx.read_mail(id);self.put('new')
        self.assertEqual(self.ctx.execute('create_task_draft',{'title':'买牛奶'},'plain')['kind'],'task_draft')
        self.restart(conversation='selected-conversation',selected=[id])
        result=self.ctx.execute('create_task_draft',{'title':'课程提醒'},'selected')
        self.assertEqual(result['error'],'context_stale');self.assertIn(id,result['requiredMailIds'])

    def test_replace_pending_and_formal_task_change_preserve_source_guard(self):
        id=self.mail();self.ctx.read_mail(id);old=self.draft(id)
        latest=self.put('latest')
        result=self.ctx.execute('replace_task_draft',dict(draft_id=old['id'],expected_revision=1,title='updated'), 'replace')
        self.assertEqual(result['error'],'context_stale');self.assertIn(latest,result['requiredMailIds'])
        self.service.mail.save_analysis(old['id'],self.service.mail.store.get_analysis(old['id'])['drafts'])
        todo=self.service.repo.call('listTodos')[0]
        result=self.ctx.execute('propose_task_change',dict(action='update',task_id=todo['id'],expected_revision=todo['revision'],reason='latest',patch={'title':'updated'}),'change')
        self.assertEqual(result['error'],'context_stale');self.assertIn(latest,result['requiredMailIds'])
        self.assertEqual(self.service.repo.call('listTodos')[0]['title'],'Project follow-up')

    def test_outgoing_recipient_matches_actual_reads_and_reply_source_cannot_bypass(self):
        id=self.mail();self.ctx.read_mail(id);latest=self.put('latest')
        result=self.ctx.execute('create_mail_draft',dict(subject='Re: Project',body='reply',to='teacher@example.invalid'),'outgoing')
        self.assertEqual(result['error'],'context_stale');self.assertIn(latest,result['requiredMailIds'])
        result=self.ctx.execute('create_mail_draft',dict(subject='Project',body='hello',to='unrelated@example.invalid'),'unrelated')
        self.assertEqual(result['kind'],'mail_draft')
        self.restart(conversation='new')
        result=self.ctx.execute('create_mail_draft',dict(subject='Project',body='reply',reply_to_local_id=id),'reply')
        self.assertEqual(result['error'],'context_stale');self.assertIn(id,result['requiredMailIds'])

    def test_committed_call_and_commit_before_journal_replay_are_recoverable(self):
        id=self.mail();self.ctx.read_mail(id);old=self.draft(id)
        self.put('newer')
        self.assertEqual(self.draft(id)['id'],old['id'])
        self.ctx.db.execute('UPDATE agent_tool_results SET result=NULL WHERE id=?',(old['id'],))
        self.assertEqual(self.draft(id)['id'],old['id'])
        self.assertEqual(len(self.ctx.list_drafts()['items']),1)
        self.assertEqual(self.draft(id,'new-call')['error'],'context_stale')

    def test_failed_write_without_side_effect_does_not_bypass_freshness_on_retry(self):
        id=self.mail();self.ctx.read_mail(id)
        with patch.object(self.service.mail.store,'save_analyses',side_effect=OSError('disk full')):
            self.assertEqual(self.draft(id)['error'],'tool_failed')
        latest=self.put('latest')
        self.assertEqual(self.draft(id)['requiredMailIds'],[latest])
        self.assertEqual(self.ctx.list_drafts()['items'],[])
        self.ctx.read_mail(latest)
        self.assertEqual(self.draft(id)['kind'],'task_draft')


del AgentToolsTests
