from network_guard import no_external_network
import base64
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from task_backend.service import BackendService


class WorkspaceTests(unittest.TestCase):
    def setUp(self):
        # This offline fixture must never access the user's OS keychain.
        self.enterContext(patch('task_backend.vault.encrypt',side_effect=lambda value:base64.b64encode(value.encode()).decode()))
        self.enterContext(patch('task_backend.vault.decrypt',side_effect=lambda value:base64.b64decode(value).decode()))
        self.enterContext(no_external_network())
    def test_offline_workspace_and_batch_saving_keeps_parent_links(self):
        with tempfile.TemporaryDirectory() as folder:
            service=BackendService(Path(folder))
            try:
                binding=service.call('mail-workspace:addAccount','imap')
                self.assertIsNone(binding['state']['account'])
                mail=service.mail.store.put('binding','identity','user',dict(id='remote',subject='Source',sender='sender',receivedAt='2026-11-12T00:00:00Z',preview='',unread=True,webUrl='',imported=False,body='do work'))['localId']
                service.call('saveSettings',dict(baseUrl='https://example.invalid/v1',model='fixture',apiKey='synthetic'))
                service.request=lambda *args:json.dumps({'choices':[{'message':{'content':json.dumps({'drafts':[dict(tempId='root',parentTempId=None,title='Root'),dict(tempId='child',parentTempId='root',title='Child')],'notes':''})}}]}).encode()
                analysis=service.call('mail-workspace:analyze',dict(messageIds=[mail],instructions='',target=dict(baseUrl='https://example.invalid/v1',model='fixture')))
                self.assertEqual(service.call('listTodos'),[])
                root=analysis['drafts'][0];child=analysis['drafts'][1]
                self.assertEqual(service.call('mail-workspace:saveAnalysis',analysis['id'],[root])['count'],1)
                self.assertEqual(service.call('mail-workspace:saveAnalysis',analysis['id'],[root,child])['count'],1)
                self.assertEqual(service.call('mail-workspace:saveAnalysis',analysis['id'],[root,child])['count'],0)
                tasks=service.call('listTodos');self.assertEqual(tasks[1]['parentId'],tasks[0]['id'])
                self.assertEqual(service.call('mail-workspace:getLocal',mail)['status'],'analyzed')
            finally: service.close()
