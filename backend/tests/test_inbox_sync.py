"""One-click inbox sync: real persistence/readers, synthetic transport only."""
import base64
from datetime import datetime
import importlib.util
import json
import ssl
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from zoneinfo import ZoneInfo

from cryptography.fernet import Fernet
from task_backend.mail_readers import ImapReader, GraphReader
from task_backend.mail_types import DEFAULT_FILTER
from task_backend.service import BackendService

spec=importlib.util.spec_from_file_location('inbox_sync_wire_fixture',Path(__file__).resolve().parents[2]/'scripts/qa-mail-import.py')
fixture=importlib.util.module_from_spec(spec);spec.loader.exec_module(fixture)


class InboxSyncTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.directory=Path(self.temp.name);cipher=Fernet(Fernet.generate_key())
        for name,fn in [('encrypt',lambda v:base64.b64encode(cipher.encrypt(v.encode())).decode()),
                        ('decrypt',lambda v:cipher.decrypt(base64.b64decode(v)).decode())]:
            p=patch('task_backend.vault.'+name,fn);p.start();self.addCleanup(p.stop)
        p=patch('socket.create_connection',side_effect=AssertionError('No external connection allowed'));p.start();self.addCleanup(p.stop)
        # This class tests synthetic mailbox paging, not OS certificate loading.
        # Keep genuine verified TLS options without reloading the Windows trust
        # store for every one of the 401 simulated connections.
        self.tls_context=ssl.create_default_context()
        self.assertEqual(self.tls_context.verify_mode,ssl.CERT_REQUIRED)
        self.assertTrue(self.tls_context.check_hostname)
        self.enterContext(patch('task_backend.mail_readers.ssl.create_default_context',return_value=self.tls_context))
        self.mailboxes={};self.reads=[];self.fail_ids=set();self.misbehaving=set();self.on_read=None
        self.open_service();self.addCleanup(lambda:self.service.close())

    def open_service(self):
        self.service=BackendService(self.directory,request=lambda *a:self.fail('No model calls expected'),worker=True)
        self.service.clock=lambda:datetime(2026,11,21,1,0,tzinfo=ZoneInfo('Asia/Hong_Kong'))
        def factory(manager,binding):
            def client(*a,**kw):
                self.assertIs(kw['ssl_context'],self.tls_context)
                self.assertTrue(kw['ssl'])
                return self.mailboxes[binding]
            reader=ImapReader(manager,binding,client_factory=client)
            original=reader.get_message
            def get(id,*args):
                self.reads.append((binding,id))
                if self.on_read:self.on_read(binding,id)
                if (binding,id) in self.fail_ids:raise OSError('Synthetic transfer failed')
                return original(id,*args)
            reader.get_message=get
            if binding in self.misbehaving:
                read=reader.read
                return SimpleNamespace(read=read,get_message=get,test_connection=reader.test_connection)
            return reader
        self.service.mail.manager.reader_factory=factory

    def add_mailbox(self,count,name='student',connected=True):
        binding=self.service.call('mail-workspace:addAccount','imap')['id']
        self.mailboxes[binding]=fixture.ProtocolMailbox([fixture.wire_message(n,f'Course message {n}',f'Synthetic body {n}') for n in range(1,count+1)])
        self.service.call('mail-workspace:configureImap',binding,dict(email=name+'@example.invalid',host='imap.example.invalid',port=993,security='tls',password='fixture-only'))
        if connected:self.service.call('mail-workspace:connect',binding)
        return binding

    def start(self):return self.service.call('mail-operations:startInboxSync')
    def finish(self,job):
        self.assertTrue(self.service.operations.run_next())
        result=self.service.call('mail-operations:getJob',job['id'])
        self.assertEqual(result['state'],'completed',result.get('error'))
        return result

    def seed(self,binding,uid,deleted=False):
        identity=self.service.mail.manager.identity(binding)
        message=self.service.mail.manager.reader(binding).get_message(json.dumps(['INBOX','71',uid],separators=(',',':')))
        saved=self.service.mail.store.put(binding,identity['identity'],identity['email'],message)
        if deleted:self.service.call('mail-workspace:deleteLocal',[saved['localId']])

    def test_no_account_and_unknown_parameters_are_rejected(self):
        self.assertIn('mail-operations:startInboxSync',BackendService.METHODS)
        with self.assertRaisesRegex(ValueError,'连接邮箱'):self.start()
        self.add_mailbox(1,connected=False)
        with self.assertRaisesRegex(ValueError,'连接邮箱'):self.start()
        with self.assertRaisesRegex(ValueError,'无效参数'):self.service.call('mail-operations:startInboxSync',{})
        self.assertEqual(self.service.operations.jobs(),[])

    def test_all_connected_accounts_ignore_saved_filters_and_continue_401_without_duplicate_skips(self):
        first=self.add_mailbox(401);second=self.add_mailbox(3,'second');self.add_mailbox(2,'offline',False)
        narrow={**DEFAULT_FILTER,'subject':'never matches','sender':'nobody@example.invalid','folderIds':['Archive'],'unreadOnly':True}
        self.service.call('mail-workspace:saveFilter',first,narrow)
        self.seed(first,401);self.seed(first,400,deleted=True);self.reads.clear()
        job=self.start();self.assertEqual(job['label'],'收取最近邮件')
        work=self.service.operations.job(job['id'])['work']
        self.assertEqual(work['input']['bindingIds'],[first,second])
        self.assertEqual(work['input']['filter'],{**DEFAULT_FILTER,'dateMode':'range','since':'2026-11-15','until':'2026-11-21'})
        self.assertEqual(self.start()['id'],job['id'])
        result=self.finish(job)
        self.assertEqual((result['report']['added'],result['report']['skipped'],result['report']['failed'],result['report']['limited']),(402,2,0,False))
        self.assertEqual((result['done'],result['total']),(404,404))
        self.assertEqual(len(self.reads),402);self.assertEqual(len(set(self.reads)),402)
        self.assertEqual(self.service.mail.store.list_local({'limit':1000})['total'],403)
        self.assertEqual(self.service.mail.manager.get(first)['state']['filter'],narrow)
        self.reads.clear();repeated=self.finish(self.start())
        self.assertEqual((repeated['report']['added'],repeated['report']['skipped']),(0,404))
        self.assertEqual(self.reads,[])

    def test_transfer_failure_is_attempted_once_then_other_pages_and_accounts_continue(self):
        first=self.add_mailbox(401);self.add_mailbox(2,'other')
        broken=json.dumps(['INBOX','71',401],separators=(',',':'));self.fail_ids.add((first,broken))
        result=self.finish(self.start())
        self.assertEqual((result['report']['added'],result['report']['failed'],result['report']['skipped'],result['report']['limited']),(402,1,0,False))
        self.assertEqual(self.reads.count((first,broken)),1)
        self.assertEqual(result['report']['failures'][0]['remoteId'],broken)
        self.assertEqual(result['done'],result['total'])

    def test_pause_at_page_boundary_survives_restart_and_reuses_completed_bodies(self):
        self.add_mailbox(401);job=self.start();save=self.service.operations.save_job
        def checkpoint(value,force=False):
            save(value,force)
            if value['id']==job['id'] and value['state']=='running' and value['done']==200:
                self.service.call('mail-operations:pauseJob',job['id'])
        with patch.object(self.service.operations,'save_job',checkpoint):self.service.operations.run_next()
        paused=self.service.call('mail-operations:getJob',job['id']);self.assertEqual(paused['state'],'paused');self.assertEqual(paused['done'],200)
        self.assertEqual(self.start()['id'],job['id'])
        self.service.close();self.open_service()
        self.service.call('mail-operations:resumeJob',job['id']);result=self.finish(job)
        self.assertEqual(result['report']['added'],401);self.assertFalse(result['report']['limited'])
        self.assertEqual(len(self.reads),401);self.assertEqual(len(set(self.reads)),401)

    def test_cancel_retains_imported_data_and_next_click_starts_new_sync(self):
        self.add_mailbox(3);job=self.start();save=self.service.operations.save_job
        def checkpoint(value,force=False):
            save(value,force)
            if value['id']==job['id'] and value['state']=='running' and value['done']==1:
                self.service.call('mail-operations:cancelJob',job['id'])
        with patch.object(self.service.operations,'save_job',checkpoint):self.service.operations.run_next()
        self.assertEqual(self.service.operations.job(job['id'])['state'],'canceled')
        next_job=self.start();self.assertNotEqual(next_job['id'],job['id'])
        result=self.finish(next_job);self.assertEqual((result['report']['added'],result['report']['skipped']),(2,1))
        self.assertEqual(len(self.reads),3)

    def test_reader_that_ignores_exclusions_stops_with_limited_error(self):
        binding=self.add_mailbox(401);self.misbehaving.add(binding)
        result=self.finish(self.start())
        self.assertEqual((result['report']['added'],result['report']['skipped'],result['report']['failed']),(200,0,1))
        self.assertTrue(result['report']['limited'])
        self.assertEqual(result['report']['mailboxErrors'][0]['code'],'NO_IMPORT_PROGRESS')
        self.assertEqual(len(self.reads),200)

    def test_mailbox_identity_change_stops_that_account_without_blocking_another(self):
        first=self.add_mailbox(3);second=self.add_mailbox(2,'other');called=False
        def change(binding,id):
            nonlocal called
            if binding==first and not called:
                called=True
                self.service.call('mail-workspace:configureImap',first,dict(email='replacement@example.invalid',host='imap.example.invalid',port=993,security='tls',password='fixture-only'))
        self.on_read=change;result=self.finish(self.start())
        self.assertEqual(result['report']['added'],2)
        self.assertEqual(result['report']['failed'],1)
        self.assertEqual(result['report']['mailboxErrors'][0]['code'],'INBOX_IDENTITY_CHANGED')
        self.assertEqual(self.service.mail.store.list_local({'bindingIds':[second]})['total'],2)
        self.assertEqual(len([b for b,id in self.reads if b==first]),1)

    def test_two_hosts_return_same_active_sync_job(self):
        self.add_mailbox(1);first=self.start()
        other=BackendService(self.directory,worker=True)
        try:
            self.assertEqual(other.call('mail-operations:startInboxSync')['id'],first['id'])
        finally:other.close()
        self.assertEqual(len(self.service.operations.jobs()),1)

    def test_owned_worker_exit_preserves_progress_and_requires_explicit_resume(self):
        import io
        self.add_mailbox(1);created=self.start();operations=self.service.operations
        job=operations.job(created['id']);job.update(state='running',done=7,total=9);operations.save_job(job,force=True)
        stdin=io.BytesIO();operations.process=SimpleNamespace(poll=lambda:-9,stdin=stdin)
        # A worker-mode service must not reconcile another host's state.
        self.assertEqual(self.service.call('mail-operations:getJob',job['id'])['state'],'running')
        operations.is_worker=False;schedules=[];operations.schedule=lambda:schedules.append(True)
        current=self.service.call('mail-operations:getJob',job['id'])
        self.assertEqual((current['state'],current['done'],current['total']),('paused',7,9))
        self.assertTrue(stdin.closed);self.assertIsNone(operations.process);self.assertEqual(schedules,[])
        self.assertEqual(self.start()['id'],job['id']);self.assertEqual(schedules,[])
        self.service.call('mail-operations:resumeJob',job['id']);self.assertEqual(schedules,[True])
        current=operations.job(job['id']);current['state']='canceling';operations.save_job(current,force=True)
        operations.process=SimpleNamespace(poll=lambda:1,stdin=io.BytesIO())
        self.assertEqual(self.service.call('mail-operations:getJob',job['id'])['state'],'canceled')


class GraphSyncReaderTests(unittest.TestCase):
    def test_graph_continues_beyond_200_after_excluding_already_attempted_ids(self):
        manager=SimpleNamespace(identity=lambda _:dict(identity='graph-user'),check_identity=lambda *a:None)
        reader=GraphReader(manager,'graph-binding');pages=[]
        raw=[dict(id=str(n),subject='Graph course mail '+str(n),receivedDateTime='2026-11-20T12:00:00Z',
                  **{'from':{'emailAddress':{'address':'teacher@example.invalid'}}}) for n in range(401)]
        def fake_pages(url):
            pages.append(url)
            for offset in range(0,len(raw),100):yield raw[offset:offset+100]
        reader.pages=fake_pages
        filters={**DEFAULT_FILTER,'dateMode':'range','since':'2026-11-15','until':'2026-11-21'}
        first=reader.read_for_import(filters,set());self.assertEqual(len(first['messages']),200);self.assertTrue(first['limited'])
        excluded={m['id'] for m in first['messages']};second=reader.read_for_import(filters,excluded)
        self.assertEqual((len(second['messages']),second['skippedKnown']),(200,200));self.assertEqual(set(second['skippedRemoteIds']),excluded)
        excluded.update(m['id'] for m in second['messages']);third=reader.read_for_import(filters,excluded)
        self.assertEqual((len(third['messages']),third['skippedKnown'],third['limited']),(1,400,False))
        self.assertTrue(all('/mailFolders/inbox/messages?' in u for u in pages))
        self.assertNotIn('skippedRemoteIds',reader.read(filters))
