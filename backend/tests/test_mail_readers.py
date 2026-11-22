import unittest
from task_backend.mail_readers import GraphReader,validate_graph_url,read_segments


class ReaderTests(unittest.TestCase):
    def test_external_graph_pagination_rejected(self):
        for url in ('https://evil.example/me','http://graph.microsoft.com/v1.0/me/messages','https://user:pass@graph.microsoft.com/v1.0/me/messages'):
            with self.assertRaises(ValueError): validate_graph_url(url)
        self.assertEqual(validate_graph_url('https://graph.microsoft.com/v1.0/me/messages'), 'https://graph.microsoft.com/v1.0/me/messages')

    def test_segmented_imap_body_checks_position_and_detects_end(self):
        class Client:
            def fetch(self,uids,query):
                import re
                match=re.search(r'<(\d+)\.(\d+)>',query[0]);start,take=map(int,match.groups())
                return {1:{('BODY[]<'+str(start)+'>').encode():b'hello'[start:start+take]}}
        content,complete=read_segments(Client(),1,100)
        self.assertEqual(content,b'hello');self.assertTrue(complete)
        class Bad:
            def fetch(self,*args): return {1:{b'BODY[]<99>':b'wrong'}}
        with self.assertRaises(ValueError): read_segments(Bad(),1,100)

class StructureTests(unittest.TestCase):
    def test_large_mail_selects_plain_part_and_preserves_attachment_metadata(self):
        from task_backend.mail_readers import structure_parts
        plain=(b'TEXT',b'PLAIN',(b'CHARSET',b'utf-8'),None,None,b'BASE64',16,1,None,None)
        attachment=(b'APPLICATION',b'PDF',(b'NAME',b'paper.pdf'),None,None,b'BASE64',9000000,None,(b'ATTACHMENT',(b'FILENAME',b'paper.pdf')))
        info=structure_parts(([plain,attachment],b'MIXED'))
        self.assertEqual(info['text']['key'],'1')
        self.assertEqual(info['attachments'][0],dict(name='paper.pdf',type='application/pdf',size=9000000,inline=False))

class SummaryFlagTests(unittest.TestCase):
    def test_seen_flag_is_not_reported_as_unread(self):
        from datetime import datetime,timezone
        from types import SimpleNamespace
        from task_backend.mail_readers import ImapReader
        row={b'ENVELOPE':SimpleNamespace(subject=b'Subject',from_=None),b'INTERNALDATE':datetime.now(timezone.utc),b'FLAGS':(bytes([92])+b'Seen',)}
        result=ImapReader(None,'binding').summary(1,row,'INBOX',2)
        self.assertFalse(result['unread'])
