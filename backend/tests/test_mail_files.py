import base64,json,re,tempfile,unittest,zipfile
from contextlib import contextmanager
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4
from unittest.mock import patch
from task_backend.mail_files import MailFiles,make_docx,make_pdf,extract_document,MAX_BYTES
from task_backend.mail_readers import ImapReader,GraphReader,read_attachment_part,structure_parts


class FileTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.home=Path(self.tmp.name);self.directory=self.home/'data';self.directory.mkdir();self.files=MailFiles(SimpleNamespace(directory=self.directory))
    def tearDown(self):self.files.close();self.tmp.cleanup()
    def local(self,name='notes.txt',data=None):
        p=self.home/name;p.write_bytes('课程项目要求\n提交修订报告。'.encode() if data is None else data);return p
    def test_local_copy_is_immutable_and_original_edits_do_not_change_context(self):
        p=self.local();record=self.files.import_local(p);p.write_text('later',encoding='utf-8')
        self.assertIn('课程',self.files.read(record['id']));self.assertNotIn(str(self.home),json.dumps(record));self.assertEqual(self.files.call('read',record['id']),self.files.read(record['id']))
    def test_tampered_copy_rejected_before_read_or_export(self):
        r=self.files.import_local(self.local());(self.files.root/r['id']).write_bytes(b'tampered')
        with self.assertRaisesRegex(ValueError,'变化'):self.files.read(r['id'])
        with self.assertRaises(ValueError):self.files.export_file(r['id'],self.home/'export.txt')
    def test_symlink_folder_unsupported_encoding_and_size_rejected(self):
        p=self.local();link=self.home/'link.txt';link.symlink_to(p)
        for path in (link,self.home,self.local('bad.txt',b'\xff\xfe'),self.local('bad.exe',b'test')):
            with self.assertRaises(ValueError):self.files.import_local(path)
        with patch('task_backend.mail_files.MAX_BYTES',3):
            with self.assertRaisesRegex(ValueError,'10 MB'):self.files.import_local(p)
    def test_docx_reads_paragraph_and_table_text_without_executing_relationships(self):
        data=make_docx('项目计划','# 任务\n明确负责人。')
        self.assertIn('明确负责人',extract_document(data,'docx')[0])
        source=BytesIO()
        with zipfile.ZipFile(source,'w') as z:z.writestr('word/document.xml','<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:tbl><w:tr><w:tc><w:p><w:r><w:t>表格内容</w:t></w:r></w:p></w:tc></w:tr></w:tbl></w:body></w:document>')
        self.assertEqual(extract_document(source.getvalue(),'docx')[0],'表格内容')
    def test_docx_macro_entities_zip_bomb_and_corrupt_rejected(self):
        for extra,xml in [('word/vbaProject.bin','<x/>'),('extra','<!DOCTYPE foo [<!ENTITY x "bad">]><foo>&x;</foo>')]:
            stream=BytesIO()
            with zipfile.ZipFile(stream,'w') as z:z.writestr(extra,b'x');z.writestr('word/document.xml',xml)
            with self.assertRaises(ValueError):extract_document(stream.getvalue(),'docx')
        stream=BytesIO()
        with zipfile.ZipFile(stream,'w',zipfile.ZIP_DEFLATED) as z:z.writestr('word/document.xml','a'*(8*1024*1024+1))
        with self.assertRaisesRegex(ValueError,'解压'):extract_document(stream.getvalue(),'docx')
        with self.assertRaises(ValueError):extract_document(b'notdocx','docx')
    def test_pdf_chinese_roundtrip_pages_and_encryption(self):
        data=make_pdf('课程项目计划','第一步 整理附件\n第二步 完成报告')
        value,warnings=extract_document(data,'pdf');self.assertIn('整理附件',value);self.assertIn('第 1 页',value);self.assertTrue(warnings)
        from pypdf import PdfReader,PdfWriter
        writer=PdfWriter();writer.append(PdfReader(BytesIO(data)));writer.encrypt('secret');out=BytesIO();writer.write(out)
        with self.assertRaisesRegex(ValueError,'加密'):extract_document(out.getvalue(),'pdf')
    def test_scanned_blank_pdf_can_be_viewed_and_overpage_is_rejected(self):
        from pypdf import PdfWriter
        writer=PdfWriter();writer.add_blank_page(100,100);out=BytesIO();writer.write(out)
        value,warnings=extract_document(out.getvalue(),'pdf')
        self.assertEqual(value,'');self.assertTrue(warnings)
        for _ in range(80):writer.add_blank_page(100,100)
        out=BytesIO();writer.write(out)
        with self.assertRaisesRegex(ValueError,'80 页'):extract_document(out.getvalue(),'pdf')
    def test_generation_new_version_parents_and_deterministic_retry(self):
        original=self.files.import_local(self.local());id=str(uuid4());args=('修订.docx','docx','修订计划','负责人张三。',[original['id']])
        record=self.files.create_document(*args,artifact_id=id);again=self.files.create_document(*args,artifact_id=id)
        self.assertEqual(record,again);self.assertIn('张三',self.files.read(id));self.assertEqual(original['sha256'],self.files.get(original['id'])['sha256'])
        with self.assertRaisesRegex(ValueError,'不一致'):self.files.create_document('new','docx','different','other',[original['id']],artifact_id=id)
        for fmt in ('md','txt','pdf'):
            r=self.files.create_document('../../report',fmt,'安全文件名','执行任务。',[id]);self.assertNotIn('/',r['name']);self.assertIn('执行任务',self.files.read(r['id']))
    def test_export_preserves_original_and_blocks_internal_paths(self):
        source=self.local();r=self.files.import_local(source)
        for dest in (source,self.files.root/'copy.txt'):
            with self.assertRaises(ValueError):self.files.export_file(r['id'],dest)
        out=self.home/'export.txt';self.assertTrue(self.files.export_file(r['id'],out));self.assertEqual(out.read_bytes(),source.read_bytes())
    def test_selected_mail_source_identity_and_metadata_checked(self):
        attachment=dict(name='lesson.txt',type='text/plain',size=12,inline=False)
        mail=dict(id='remote',bindingId='account',mailboxIdentity='identity',attachments=[attachment])
        calls=[]
        reader=SimpleNamespace(get_attachment=lambda *args:(calls.append(args) or '课程要求'.encode()))
        manager=SimpleNamespace(check_identity=lambda *args:None,reader=lambda _:reader)
        self.files.service.mail=SimpleNamespace(manager=manager,store=SimpleNamespace(get_local=lambda _:mail))
        r=self.files.import_attachment('local',0);self.assertEqual(r['sourceMailId'],'local');self.assertEqual(calls[0],('remote',0,attachment,MAX_BYTES))
        manager.check_identity=lambda *args:(_ for _ in ()).throw(ValueError('身份变化'))
        with self.assertRaises(ValueError):self.files.import_attachment('local',0)
        self.assertEqual(len(calls),1)
    def test_limit_does_not_silently_truncate(self):
        with patch('task_backend.mail_files.MAX_TEXT',3):
            with self.assertRaisesRegex(ValueError,'没有截断'):self.files.import_local(self.local())

    def test_generated_crash_orphan_is_recovered_but_link_not_followed(self):
        artifact=str(uuid4());(self.files.root/artifact).write_bytes(b'orphan')
        result=self.files.create_document('retry','txt','重试结果','完整输出。',[],artifact_id=artifact)
        self.assertEqual(result['id'],artifact);self.assertIn('完整输出',self.files.read(artifact))
        second=str(uuid4());target=self.local();(self.files.root/second).symlink_to(target)
        with self.assertRaisesRegex(ValueError,'路径异常'):self.files.create_document('retry','txt','标题','正文',[],artifact_id=second)
        self.assertIn('课程',target.read_text())
    def test_docx_font_is_embedded_with_valid_obfuscation(self):
        from uuid import UUID
        from xml.etree import ElementTree as ET
        data=make_docx('中文标题','中文正文')
        with zipfile.ZipFile(BytesIO(data)) as z:
            root=ET.fromstring(z.read('word/fontTable.xml'));entry=root.find('.//{http://schemas.openxmlformats.org/wordprocessingml/2006/main}embedRegular');guid=entry.attrib['{http://schemas.openxmlformats.org/wordprocessingml/2006/main}fontKey']
            key=UUID(guid).bytes[::-1];font=bytearray(z.read('word/fonts/NotoSansSC.odttf'))
            for i in range(32):font[i]^=key[i%16]
            self.assertEqual(font[:4],b'\x00\x01\x00\x00')
            self.assertLess(len(data),200000)
            from fontTools.ttLib import TTFont
            subset=TTFont(BytesIO(font));cmap=subset.getBestCmap()
            self.assertTrue(all(ord(character) in cmap for character in '中文标题正文'))
            self.assertEqual(subset['OS/2'].fsType,0)
            subset.close()
            self.assertIn('中文正文',extract_document(data,'docx')[0])


class AttachmentReaderTests(unittest.TestCase):
    def client(self,payload=b'content',encoded=None,position_shift=0):
        raw=encoded if encoded is not None else base64.encodebytes(payload)
        class Client:
            def fetch(self,uids,queries):
                q=queries[0]
                if '.MIME]' in q:return {7:{b'BODY[2.MIME]':b'Content-Type: text/plain\r\nContent-Transfer-Encoding: base64\r\n'}}
                match=re.search(r'<(\d+)\.(\d+)>',q);offset,take=map(int,match.groups())
                return {7:{f'BODY[2]<{offset+position_shift}>'.encode():raw[offset:offset+take]}}
        return Client()
    def test_rfc2231_chinese_names_single_and_continuation(self):
        for params in [(b'FILENAME*',b"utf-8''%E4%B8%AD%E6%96%87.pdf"),(b'FILENAME*0*',b"utf-8''%E4%B8%AD",b'FILENAME*1*',b'%E6%96%87.pdf')]:
            root=(b'APPLICATION',b'PDF',None,None,None,b'BASE64',12,None,(b'ATTACHMENT',params))
            self.assertEqual(structure_parts(root)['attachments'][0]['name'],'中文.pdf')
        root=(b'APPLICATION',b'PDF',(b'NAME',b'=?utf-8?b?5Lit5paHLnBkZg==?='),None,None,b'BASE64',12)
        self.assertEqual(structure_parts(root)['attachments'][0]['name'],'中文.pdf')
    def test_selected_mime_decodes_unicode_bytes_and_checks_boundaries(self):
        data='中文附件'.encode();self.assertEqual(read_attachment_part(self.client(data),7,'2',100),data)
        with self.assertRaisesRegex(ValueError,'分段'):read_attachment_part(self.client(position_shift=1),7,'2',100)
        with self.assertRaisesRegex(ValueError,'编码'):read_attachment_part(self.client(encoded=b'not valid %%base64'),7,'2',100)
        with self.assertRaisesRegex(ValueError,'10 MB'):read_attachment_part(self.client(b'x'*101),7,'2',100)
    def test_imap_validity_stale_selection_and_readonly_fetch(self):
        attachment=(b'TEXT',b'PLAIN',(b'NAME',b'lesson.txt'),None,None,b'BASE64',12,1,None,(b'ATTACHMENT',(b'FILENAME',b'lesson.txt')))
        root=([(b'TEXT',b'PLAIN',None,None,None,b'7BIT',5,1),attachment],b'MIXED');expected=structure_parts(root)['attachments'][0]
        manager=SimpleNamespace(identity=lambda _:dict(identity='mail'),check_identity=lambda *args:None)
        reader=ImapReader(manager,'account');base=self.client();queries=[]
        class Client:
            validity=4
            def select_folder(self,folder,readonly):
                assert readonly;return {b'UIDVALIDITY':self.validity}
            def fetch(self,uids,query):
                queries.extend(query)
                if query==['BODYSTRUCTURE']:return {7:{b'BODYSTRUCTURE':root}}
                return base.fetch(uids,query)
        client=Client()
        @contextmanager
        def session():yield client
        reader.session=session
        self.assertEqual(reader.get_attachment('["INBOX","4",7]',0,expected,100),b'content')
        self.assertTrue(all(q=='BODYSTRUCTURE' or q.startswith('BODY.PEEK[') for q in queries))
        with self.assertRaisesRegex(ValueError,'信息已变化'):reader.get_attachment('["INBOX","4",7]',0,{**expected,'name':'other.txt'},100)
        client.validity=5
        with self.assertRaisesRegex(ValueError,'标识已变化'):reader.get_attachment('["INBOX","4",7]',0,expected,100)
    def test_graph_attachment_bytes_and_unsupported_reference(self):
        manager=SimpleNamespace(identity=lambda _:dict(identity='mail'),check_identity=lambda *args:None)
        reader=GraphReader(manager,'account');metadata=dict(id='a1',name='a.txt',contentType='text/plain',size=5,isInline=False)
        reader.pages=lambda _:iter([[metadata]])
        reader.get=lambda *args:{**metadata,'@odata.type':'#microsoft.graph.fileAttachment','contentBytes':base64.b64encode(b'hello').decode()}
        expected=dict(name='a.txt',type='text/plain',size=5,inline=False)
        self.assertEqual(reader.get_attachment('message',0,expected),b'hello')
        reader.get=lambda *args:{**metadata,'@odata.type':'#microsoft.graph.referenceAttachment'}
        with self.assertRaisesRegex(ValueError,'云链接'):reader.get_attachment('message',0,expected)
        reader.pages=lambda _:iter([[metadata,metadata]])
        with self.assertRaisesRegex(ValueError,'重复'):reader.get_attachment('message',0,expected)

if __name__=='__main__':unittest.main()
