from pathlib import Path
import tempfile
import unittest
from task_backend.mail_store import MailStore,DEFAULT_FILTER


def message(body='body'):
    return dict(id='remote',subject='Subject',sender='sender@example.invalid',receivedAt='2026-11-12T00:00:00.000Z',preview='preview',unread=True,webUrl='',imported=False,body=body)


class MailStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.store=MailStore(Path(self.temp.name))
    def tearDown(self): self.store.close();self.temp.cleanup()
    def put(self,body='body'): return self.store.put('binding','identity','user@example.invalid',message(body))['localId']

    def test_deleted_receipt_survives_restart_and_prevents_resurrection(self):
        id=self.put();self.assertEqual(self.store.delete_local([id]),{'count':1})
        self.store.close();self.store=MailStore(Path(self.temp.name))
        self.assertTrue(self.store.is_deleted('binding','identity','remote'))
        self.assertFalse(self.store.put('binding','identity','user@example.invalid',message())['added'])
        self.assertEqual(self.store.list_local({})['total'],0)

    def test_body_status_and_analysis_invalidated_only_when_content_changes(self):
        id=self.put();analysis=dict(id='analysis',messageIds=[id],drafts=[],createdAt='2026-11-12T01:00:00Z',saved=False)
        self.store.save_analyses([analysis]);self.assertEqual(self.store.get_local(id)['status'],'analyzed')
        self.put();self.assertEqual(self.store.get_local(id)['status'],'analyzed')
        self.put('new body');self.assertEqual(self.store.get_local(id)['status'],'unanalyzed')
        self.assertEqual(self.store.get_local(id)['body'],'new body')

    def test_summary_then_body_and_listing_omits_large_body(self):
        summary=message();summary.pop('body')
        id=self.store.put_summary('binding','identity','user',summary)['localId']
        self.assertEqual(self.store.get_local(id)['bodyStatus'],'pending')
        self.store.put_body(id,message())
        self.assertEqual(self.store.get_local(id)['bodyStatus'],'complete')
        self.assertEqual(self.store.list_local({})['messages'][0]['body'],'')

    def test_folder_move_atomic_and_folder_deletion_keeps_mail(self):
        id=self.put();folder=self.store.create_folder('Archive')
        with self.assertRaises(ValueError): self.store.move_local([id,'missing'],folder['id'])
        self.assertIsNone(self.store.get_local(id)['folderId'])
        self.store.move_local([id],folder['id']);self.assertEqual(self.store.list_local({'folderId':folder['id']})['total'],1)
        self.store.delete_folder(folder['id']);self.assertIsNone(self.store.get_local(id)['folderId'])

    def test_default_preset_seeded_once_and_query_parameterized(self):
        preset=self.store.list_presets('binding')[0]
        self.store.delete_preset(preset['id'])
        self.assertEqual(self.store.list_presets('binding'),[])
        self.put()
        self.assertEqual(self.store.list_local({'search':"' OR 1=1 --"})['total'],0)
        self.assertEqual(self.store.known_senders('binding'),['sender@example.invalid'])

    def test_analysis_batch_failure_is_atomic_and_partial_is_not_marked(self):
        id=self.put()
        first=dict(id='a',messageIds=[id],drafts=[],createdAt='2026-11-12T01:00:00Z',saved=False,complete=False)
        second=dict(first,id='b',messageIds=['missing'])
        with self.assertRaises(ValueError): self.store.save_analyses([first,second])
        with self.assertRaises(ValueError): self.store.get_analysis('a')
        self.store.save_analyses([first]);self.assertEqual(self.store.get_local(id)['status'],'unanalyzed')
        self.store.complete_analysis('a');self.assertEqual(self.store.get_local(id)['status'],'analyzed')
