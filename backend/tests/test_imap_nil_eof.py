"""Real QQ-compatible IMAP NIL-at-EOF regression; no network or credentials."""
from contextlib import contextmanager
import re
from types import SimpleNamespace
import unittest
from task_backend.mail_readers import ImapReader,MailReadError,read_attachment_part,read_segments,read_text_part,structure_parts

class SectionClient:
    def __init__(self,raw=b'PO,ITEM,REV,QTY\r\n00418,00127,1,240\r\n',section='2',nil_shift=0,chunk=256*1024):
        self.raw,self.section,self.nil_shift,self.chunk=raw,section,nil_shift,chunk;self.calls=[]
    def fetch(self,uids,fields):
        self.calls.append(fields);q=fields[0];uid=uids[0]
        if '.MIME]' in q:return {uid:{('BODY['+self.section+'.MIME]').encode():b'Content-Type: text/csv; charset=utf-8\r\nContent-Transfer-Encoding: 7bit\r\n'}}
        match=re.search(r'<(\d+)\.(\d+)>',q);offset,take=map(int,match.groups())
        data=self.raw[offset:offset+min(take,self.chunk)] if offset<len(self.raw) else None
        origin=offset+self.nil_shift if data is None else offset
        return {uid:{('BODY['+self.section+']<'+str(origin)+'>').encode():data}}

class NilEofTests(unittest.TestCase):
    def test_real_qq_attachment_nil_after_exact_encoded_size_is_complete(self):
        c=SectionClient();self.assertEqual(read_attachment_part(c,7,'2',100,len(c.raw)),c.raw)
        self.assertTrue(c.calls[-1][0].startswith('BODY.PEEK[2]<'+str(len(c.raw))+'.'))
        self.assertTrue(all('PEEK' in q[0] for q in c.calls))
    def test_nil_is_not_eof_without_matching_declared_size(self):
        for expected in (None,0,1,999,True):
            with self.subTest(expected=expected),self.assertRaises(MailReadError) as ctx:read_attachment_part(SectionClient(),7,'2',100,expected)
            self.assertEqual(ctx.exception.code,'SEGMENT_MISMATCH')
    def test_wrong_origin_nil_is_not_accepted_even_at_known_size(self):
        c=SectionClient(nil_shift=1)
        with self.assertRaises(MailReadError):read_attachment_part(c,7,'2',100,len(c.raw))
    def test_missing_metadata_size_cannot_masquerade_as_empty_file(self):
        for size in (None,-1,False):
            part=(b'TEXT',b'CSV',(b'NAME',b'order.csv'),None,None,b'7BIT',size,2,None,(b'ATTACHMENT',(b'FILENAME',b'order.csv')))
            detail=structure_parts(part,True)['attachments'][0]
            self.assertFalse(detail['sizeKnown'])
            with self.assertRaises(MailReadError):read_attachment_part(SectionClient(raw=b''),7,'2',100,detail['size'] if detail['sizeKnown'] else None)
    def test_short_chunks_keep_reading_until_verified_eof(self):
        c=SectionClient(chunk=7);self.assertEqual(read_attachment_part(c,7,'2',100,len(c.raw)),c.raw);self.assertGreater(len(c.calls),3)
    def test_nil_does_not_remove_decoded_or_transport_limits(self):
        c=SectionClient(raw=b'x'*101)
        with self.assertRaisesRegex(ValueError,'10 MB'):read_attachment_part(c,7,'2',100,len(c.raw))
        huge=SectionClient(raw=b'x'*70000)
        with self.assertRaisesRegex(ValueError,'上限'):read_attachment_part(huge,7,'2',1,len(huge.raw))
        raw,complete=read_segments(SectionClient(raw=b'abcdefgh',section=''),7,4,8)
        self.assertEqual(raw,b'abcd');self.assertFalse(complete)
    def test_whole_message_and_text_leaf_use_declared_sizes_for_nil(self):
        self.assertEqual(read_segments(SectionClient(raw=b'hello',section=''),7,100,5),(b'hello',True))
        with self.assertRaises(MailReadError):read_segments(SectionClient(raw=b'hello',section=''),7,100)
        c=SectionClient(raw=b'hello',section='1')
        self.assertEqual(read_text_part(c,7,{'key':'1','size':5,'type':'text/plain'},100),{'body':'hello','complete':True,'bytes':5})
        with self.assertRaises(MailReadError):read_text_part(SectionClient(raw=b'hello',section='1'),7,{'key':'1','size':6,'type':'text/plain'},100)
    def test_real_reader_passes_bodystructure_size_and_rechecks_metadata(self):
        c=SectionClient();base_fetch=c.fetch
        part=(b'TEXT',b'CSV',(b'NAME',b'order.csv'),None,None,b'7BIT',len(c.raw),2,None,(b'ATTACHMENT',(b'FILENAME',b'order.csv')))
        root=([(b'TEXT',b'PLAIN',None,None,None,b'7BIT',5,1),part],b'MIXED');metadata_fetches=[]
        def fetch(uids,fields):
            if fields==['BODYSTRUCTURE']:metadata_fetches.append(True);return {7:{b'BODYSTRUCTURE':root}}
            return base_fetch(uids,fields)
        c.fetch=fetch
        def select_folder(folder,readonly):self.assertTrue(readonly);return {b'UIDVALIDITY':4}
        c.select_folder=select_folder;manager=SimpleNamespace(identity=lambda _:dict(identity='account'),check_identity=lambda *a:None);reader=ImapReader(manager,'binding')
        @contextmanager
        def session():yield c
        reader.session=session;expected=structure_parts(root)['attachments'][0]
        self.assertEqual(reader.get_attachment('["INBOX","4",7]',0,expected,100),c.raw);self.assertEqual(len(metadata_fetches),2)

if __name__=='__main__':unittest.main()
