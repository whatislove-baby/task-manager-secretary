"""Real parser/store/SMTP assembly with synthetic protocol bytes; no network.

These tests validate sender/thread routing and frozen reply previews, not SMTP
sender authentication or live delivery. A second real account is still needed
to accept genuine independent-sender behaviour at the provider boundary.
"""
from copy import deepcopy
from datetime import datetime,timezone
from email import policy
from email.message import EmailMessage
from email.parser import BytesParser
import json
from pathlib import Path
import re
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from task_backend.mail_identity import header_metadata,metadata_from_bytes,normalize_mail,reply_context
from task_backend.mail_readers import ImapReader,GraphReader
from task_backend.mail_store import MailStore,compact
from task_backend.mail_types import DEFAULT_FILTER,matches_filter
import test_mail_compose as compose_fixture


def fixture_message():
    message=EmailMessage(policy=policy.SMTP)
    for key,value in {
        'From':'"Chan, Iris" <Iris.Chan@Campus.Example.Invalid>',
        'To':'Lin <lin@campus.example.invalid>',
        'Cc':'TA <ta@campus.example.invalid>',
        'Reply-To':'Course Office <office@campus.example.invalid>',
        'Subject':'Re: CS5021 examination arrangements',
        'Date':'Wed, 18 Nov 2026 11:00:00 +0800',
        'Message-ID':'<Exam-Update@campus.example.invalid>',
        'In-Reply-To':'<exam-first@campus.example.invalid>',
        'References':'<course-root@campus.example.invalid> <exam-first@campus.example.invalid>',
    }.items():message[key]=value
    message.set_content('Dear Lin,\nThe exam is now 2 December at 09:30. Please reply to the course office.\n\n'
                        '--- quoted old message ---\nFrom: fake@other.example.invalid\n'
                        'Reply-To: attacker@other.example.invalid\nMessage-ID: <quoted@other.example.invalid>\n')
    return message


class ProtocolClient:
    def __init__(self,message,large=False):
        self.message=message;self.raw=message.as_bytes();self.large=large;self.calls=[]
    def login(self,*args):pass
    def logout(self):pass
    def select_folder(self,folder,readonly=True):
        assert readonly;return {b'UIDVALIDITY':34}
    def fetch(self,uids,fields):
        self.calls.append(fields);uid=uids[0]
        if fields==['UID']:return {uid:{b'UID':uid}}
        if 'ENVELOPE' in fields:
            envelope=SimpleNamespace(subject=b'Re: CS5021 examination arrangements',
                from_=[SimpleNamespace(name=b'Chan, Iris',mailbox=b'Iris.Chan',host=b'Campus.Example.Invalid')],
                reply_to=[SimpleNamespace(name=b'Old Office',mailbox=b'old-office',host=b'campus.example.invalid')],
                message_id=b'<Exam-Update@campus.example.invalid>',in_reply_to=b'<exam-first@campus.example.invalid>')
            return {uid:{b'ENVELOPE':envelope,b'INTERNALDATE':datetime(2026,11,18,3,tzinfo=timezone.utc),b'FLAGS':(),
                b'RFC822.SIZE':3000000 if self.large else len(self.raw),
                b'BODYSTRUCTURE':(b'TEXT',b'PLAIN',(b'CHARSET',b'UTF-8'),None,None,b'8BIT',300,5)}}
        if fields[0].startswith('BODY.PEEK[HEADER.FIELDS'):
            return {uid:{b'BODY[HEADER.FIELDS (FROM REPLY-TO TO CC MESSAGE-ID IN-REPLY-TO REFERENCES)]':self.raw.split(b'\r\n\r\n',1)[0]+b'\r\n\r\n'}}
        if fields==['BODY.PEEK[1.MIME]']:
            return {uid:{b'BODY[1.MIME]':b'Content-Type: text/plain; charset="utf-8"\r\nContent-Transfer-Encoding: 8bit\r\n'}}
        match=re.fullmatch(r'BODY.PEEK\[(1?)\]<(\d+)\.(\d+)>',fields[0])
        if match:
            section,offset,size=match.groups();offset=int(offset);size=int(size)
            raw=self.message.get_content().encode() if section else self.raw
            return {uid:{f'BODY[{section}]<{offset}>'.encode():raw[offset:offset+size]}}
        raise AssertionError('unexpected IMAP request: '+repr(fields))


class IdentityReaderStoreTests(unittest.TestCase):
    def setUp(self):
        for method in ('connect','connect_ex'):
            p=patch('socket.socket.'+method,side_effect=AssertionError('network forbidden'));p.start();self.addCleanup(p.stop)
    def reader(self,large=False):
        client=ProtocolClient(fixture_message(),large)
        manager=SimpleNamespace(read=lambda _:dict(config=dict(host='imap.example.invalid',port=993,security='tls',username='unused',password='unused')),
            identity=lambda _:dict(identity='identity-a'),check_identity=lambda *args:None)
        return ImapReader(manager,'binding-a',client_factory=lambda *args,**kwargs:client),client
    def test_real_imap_headers_override_envelope_and_ignore_quoted_body(self):
        reader,client=self.reader()
        value=reader.get_message(compact(['INBOX','34',7]))
        self.assertEqual(value['fromAddress'],'iris.chan@campus.example.invalid')
        self.assertEqual(value['fromName'],'Chan, Iris')
        self.assertEqual(value['replyToAddresses'],['office@campus.example.invalid'])
        self.assertEqual(value['messageId'],'<Exam-Update@campus.example.invalid>')
        self.assertEqual(value['inReplyTo'],'<exam-first@campus.example.invalid>')
        self.assertEqual(value['references'],['<course-root@campus.example.invalid>','<exam-first@campus.example.invalid>'])
        self.assertEqual(value['toAddresses'],['lin@campus.example.invalid'])
        self.assertEqual(value['ccAddresses'],['ta@campus.example.invalid'])
        self.assertEqual(value['identityWarnings'],[])
        self.assertIn('attacker@other.example.invalid',value['body'])
        self.assertEqual(reply_context(value)['to'],'office@campus.example.invalid')
    def test_large_mail_fetches_top_level_headers_without_downloading_attachments(self):
        reader,client=self.reader(large=True)
        value=reader.get_message(compact(['INBOX','34',7]))
        self.assertEqual(value['bodyStatus'],'complete')
        self.assertEqual(value['references'],['<course-root@campus.example.invalid>','<exam-first@campus.example.invalid>'])
        self.assertEqual(value['replyToAddresses'],['office@campus.example.invalid'])
        self.assertTrue(any(v[0].startswith('BODY.PEEK[HEADER.FIELDS') for v in client.calls))
        self.assertFalse(any(v[0].startswith('BODY.PEEK[]') for v in client.calls))
    def test_graph_preserves_resource_account_identity_and_actual_headers(self):
        value=dict(id='graph-1',subject='Exam revision',receivedDateTime='2026-11-18T03:00:00Z',
            **{'from':{'emailAddress':{'name':'Iris','address':'IRIS.CHAN@campus.example.invalid'}}},
            replyTo=[{'emailAddress':{'address':'office@campus.example.invalid'}}],
            toRecipients=[{'emailAddress':{'address':'lin@campus.example.invalid'}}],
            internetMessageId='<Exam-Update@campus.example.invalid>',
            internetMessageHeaders=[{'name':'References','value':'<course-root@campus.example.invalid> <exam-first@campus.example.invalid>'},
                {'name':'In-Reply-To','value':'<exam-first@campus.example.invalid>'}])
        result=GraphReader(None,'binding-a').summary(value)
        self.assertEqual(result['fromAddress'],'iris.chan@campus.example.invalid')
        self.assertEqual(result['replyToAddresses'],['office@campus.example.invalid'])
        self.assertEqual(result['references'],['<course-root@campus.example.invalid>','<exam-first@campus.example.invalid>'])
        self.assertEqual(result['identityWarnings'],[])
    def test_malformed_headers_do_not_fall_back_to_body_or_display_name(self):
        base=b'From: Iris <iris@campus.example.invalid>\r\n'
        for header in (b'Reply-To: <>',b'Reply-To: a@example.invalid\r\nReply-To: b@example.invalid',
                       b'Message-ID: invalid',b'References: <a@example.invalid> not-an-id',
                       b'Message-ID: <a:bad@example.invalid>',b'Message-ID: <a..bad@example.invalid>',
                       b'From: Another <other@example.invalid>'):
            with self.subTest(header=header):
                value=metadata_from_bytes(base+header+b'\r\n\r\nReply-To: quoted@example.invalid')
                self.assertTrue(value['identityWarnings'])
                with self.assertRaises(ValueError):reply_context(value)
        with self.assertRaises(ValueError):reply_context(dict(sender='iris@example.invalid',messageId='<id@example.invalid>\r\nBcc: bad@example.invalid'))
    def test_legacy_records_safe_fallback_exact_address_and_account_search(self):
        with tempfile.TemporaryDirectory() as tmp:
            store=MailStore(tmp);self.addCleanup(store.close)
            ids=[]
            for binding,remote,sender in [('binding-a','1','Amy <amy@client.example.invalid>'),('binding-a','2','Amy Purchasing <amy@client.example.invalid>'),
                                          ('binding-a','3','Amy <fakeamy@client.example.invalid>'),('binding-b','4','Amy <amy@client.example.invalid>')]:
                item=dict(id=remote,sender=sender,subject='Quotation Q-2411',body='Please review revised quantity.',preview='',receivedAt='2026-11-19T01:00:00Z',unread=True)
                ids.append(store.put(binding,'identity-'+binding,'owner-'+binding+'@example.invalid',item)['localId'])
            # Simulate an unchanged old DB payload with no normalized columns.
            row=store.get_local(ids[0]);row={k:v for k,v in row.items() if k not in ('identitySource','identityWarnings','fromAddress','fromName','replyToAddresses','toAddresses','ccAddresses','messageId','inReplyTo','references')}
            store.db.execute('UPDATE local_mail SET payload=? WHERE local_id=?',(compact(row),ids[0]))
            exact=store.search_agent_mail('revised quantity',sender='AMY@client.example.invalid',binding_id='binding-a')
            self.assertEqual(exact['total'],2)
            self.assertEqual({v['localId'] for v in exact['messages']},set(ids[:2]))
            self.assertTrue(all(v['bindingId']=='binding-a' and v['accountEmail']=='owner-binding-a@example.invalid' for v in exact['messages']))
            self.assertEqual(store.search_agent_mail('amy@client.example.invalid')['total'],4) # free query remains fuzzy
            self.assertEqual(store.search_agent_mail(sender='amy')['total'],4) # display name remains fuzzy
            self.assertEqual(store.search_agent_mail(sender='amy@client.example.invalid',limit=1)['nextOffset'],1)
            self.assertEqual(store.get_local(ids[0])['fromAddress'],'amy@client.example.invalid')
    def test_refresh_summary_does_not_erase_full_reply_headers(self):
        with tempfile.TemporaryDirectory() as tmp:
            store=MailStore(tmp);self.addCleanup(store.close)
            reader,client=self.reader();mail=reader.get_message(compact(['INBOX','34',7]))
            local=store.put('binding-a','identity-a','lin@campus.example.invalid',mail)['localId']
            summary=reader.summary(7,client.fetch([7],['ENVELOPE'])[7],'INBOX',34)
            store.put_summary('binding-a','identity-a','lin@campus.example.invalid',summary)
            saved=store.get_local(local)
            self.assertEqual(saved['references'],mail['references'])
            self.assertEqual(saved['replyToAddresses'],['office@campus.example.invalid'])
            self.assertEqual(saved['bodyStatus'],'complete')
    def test_multiple_reply_targets_require_selection_and_missing_headers_never_guess_from_subject(self):
        mail=dict(sender='Iris <iris@campus.example.invalid>',subject='Re: repeated subject',
            replyToAddresses=['office@campus.example.invalid','ta@campus.example.invalid'])
        with self.assertRaises(ValueError):reply_context(mail)
        context=reply_context(mail,'ta@campus.example.invalid')
        self.assertEqual(context['to'],'ta@campus.example.invalid')
        self.assertFalse(context['threadAvailable'])
        for value in (None,17,'\noffice@campus.example.invalid'):
            with self.assertRaises(ValueError):reply_context(mail,value)
    def test_remote_filter_exact_address_keeps_display_name_search(self):
        real=dict(sender='Amy <amy@client.example.invalid>',subject='Quotation',unread=True)
        impostor=dict(sender='Amy <fakeamy@client.example.invalid>',subject='Quotation',unread=True)
        self.assertTrue(matches_filter(real,dict(DEFAULT_FILTER,sender='amy@client.example.invalid')))
        self.assertFalse(matches_filter(impostor,dict(DEFAULT_FILTER,sender='amy@client.example.invalid')))
        self.assertTrue(matches_filter(impostor,dict(DEFAULT_FILTER,sender='Amy')))
        self.assertTrue(matches_filter(impostor,dict(DEFAULT_FILTER,excludeSenders=['amy@client.example.invalid'])))


class ReplySnapshotTests(unittest.TestCase):
    # Reuse test-only account setup (ephemeral encryption); no duplicate tests.
    request=compose_fixture.MailComposeTests.request
    connect=compose_fixture.MailComposeTests.connect
    prepare=compose_fixture.MailComposeTests.prepare
    def setUp(self):
        compose_fixture.MailComposeTests.setUp(self)
        for method in ('connect','connect_ex'):
            p=patch('socket.socket.'+method,side_effect=AssertionError('network forbidden'));p.start();self.addCleanup(p.stop)
    def source(self,**updates):
        value=dict(id='course-revision',sender='Iris <iris.chan@campus.example.invalid>',subject='Re: CS5021 examination arrangements',
            receivedAt='2026-11-18T03:00:00Z',body='Please send your leave form to the course office.',preview='',unread=True,
            **header_metadata(fixture_message()))
        value.update(updates)
        return self.service.mail.store.put(self.binding,'synthetic-identity',self.imap['email'],value)['localId']
    def test_reply_to_frozen_preview_survives_source_deletion_and_send_is_single_attempt(self):
        source=self.source()
        receipt=self.prepare(to='',replyToLocalId=source,subject='Re: CS5021 examination arrangements')
        self.assertEqual(receipt['to'],'office@campus.example.invalid')
        self.assertEqual(receipt['replyContext']['inReplyTo'],'<Exam-Update@campus.example.invalid>')
        self.assertEqual(receipt['replyContext']['references'],['<course-root@campus.example.invalid>','<exam-first@campus.example.invalid>','<Exam-Update@campus.example.invalid>'])
        self.assertEqual(self.connections,[])
        self.service.mail.store.delete_local([source])
        sent=self.compose.send(receipt['id'])
        self.assertEqual(sent['status'],'sent')
        message=BytesParser(policy=policy.default).parsebytes(self.smtp.payloads[0])
        self.assertEqual(str(message['To']),receipt['to'])
        self.assertEqual(str(message['In-Reply-To']),receipt['replyContext']['inReplyTo'])
        self.assertEqual(str(message['References']).split(),receipt['replyContext']['references'])
        self.assertEqual(str(message['Message-ID']),receipt['messageId'])
        self.compose.send(receipt['id']);self.assertEqual(len(self.smtp.payloads),1)
    def test_changed_recipient_or_other_binding_cannot_prepare_reply(self):
        source=self.source()
        for recipient in ('iris.chan@campus.example.invalid','attacker@example.invalid','office@campus.example.invalid\r\nBcc: attacker@example.invalid'):
            with self.subTest(recipient=recipient),self.assertRaises(ValueError):self.prepare(to=recipient,replyToLocalId=source)
        original=self.service.mail.store.get_local(source)
        for extra in (dict(bindingId='other-binding'),dict(accountEmail='other-owner@example.invalid')):
            self.service.mail.store.db.execute('UPDATE local_mail SET payload=? WHERE local_id=?',(compact(original|extra),source))
            with self.assertRaisesRegex(ValueError,'邮箱不一致'):self.prepare(to='office@campus.example.invalid',replyToLocalId=source)
        with self.assertRaises(ValueError):self.prepare(to='office@campus.example.invalid',replyToLocalId='missing-source')
        self.assertEqual(self.compose.list_recent(),[]);self.assertEqual(self.connections,[])
    def test_invalid_thread_or_reply_header_rejected_before_preview(self):
        for fields in (dict(messageId='bad-id'),dict(messageId='<a@example.invalid>\r\nBcc: b@example.invalid'),
                       dict(references=['<a@example.invalid>','body text']),dict(replyToAddresses=['x@example.invalid\nBcc: y@example.invalid'])):
            with self.subTest(fields=fields):
                source=self.source(**fields)
                with self.assertRaises(ValueError):self.prepare(to='office@campus.example.invalid',replyToLocalId=source)
        self.assertEqual(self.compose.list_recent(),[]);self.assertEqual(self.connections,[])
    def test_legacy_without_message_id_replies_to_parsed_from_without_guessing_thread(self):
        item=dict(id='old',sender='Teacher <teacher@example.invalid>',subject='Re: CS5021',body='Please reply.',receivedAt='2026-11-18T00:00:00Z')
        source=self.service.mail.store.put(self.binding,'synthetic-identity',self.imap['email'],item)['localId']
        receipt=self.prepare(to='',replyToLocalId=source)
        self.assertEqual(receipt['to'],'teacher@example.invalid')
        self.assertFalse(receipt['replyContext']['threadAvailable'])
        self.assertIn('缺少 Message-ID',receipt['detail'])
        self.assertEqual(self.compose.send(receipt['id'])['status'],'sent')
        message=BytesParser(policy=policy.default).parsebytes(self.smtp.payloads[0])
        self.assertIsNone(message['In-Reply-To']);self.assertIsNone(message['References'])
    def test_corrupt_snapshot_rejected_before_smtp_connect(self):
        source=self.source()
        for field,value in [('inReplyTo','<a@example.invalid>\r\nBcc: b@example.invalid'),('to','other@example.invalid'),
                            ('bindingId','other-binding'),('references',['<wrong@example.invalid>']),('threadAvailable',False)]:
            with self.subTest(field=field):
                receipt=self.prepare(to='',replyToLocalId=source)
                receipt['replyContext'][field]=value;self.compose.save_receipt(receipt)
                self.assertEqual(self.compose.send(receipt['id'])['status'],'failed')
        self.assertEqual(self.connections,[])


if __name__=='__main__':unittest.main()
