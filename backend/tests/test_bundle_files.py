"""Real ZIP/image byte paths, bounded expansion, source linkage and tool recovery."""
import asyncio
import base64
from io import BytesIO
import json
from pathlib import Path
import stat
import struct
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from zipfile import ZipFile, ZipInfo, ZIP_DEFLATED, ZIP_STORED

from PIL import Image
from task_backend.mail_files import MailFiles
from task_backend.bundle_files import archive_index,extract_archive,inspect_archive,related_files


def picture(format='PNG',size=(40,25)):
    stream=BytesIO();Image.new('RGB',size,'red').save(stream,format=format);return stream.getvalue()


def bundle(items,compression=ZIP_DEFLATED):
    out=BytesIO()
    with ZipFile(out,'w',compression=compression) as z:
        for path,value in items:z.writestr(path,value)
    return out.getvalue()


class BundleFilesTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
        self.files=MailFiles(SimpleNamespace(directory=self.root))
        self.addCleanup(self.tmp.cleanup);self.addCleanup(self.files.close)
        self.enterContext(patch('socket.socket.connect',side_effect=AssertionError('No network allowed')))

    def save(self,name,data):return self.files._save(name,data,'local')

    def test_static_image_formats_are_real_decoded_previews_without_fabricated_ocr(self):
        for ext,fmt in [('png','PNG'),('jpg','JPEG'),('jpeg','JPEG'),('webp','WEBP')]:
            with self.subTest(ext=ext):
                data=picture(fmt);record=self.save('chart.'+ext,data)
                self.assertEqual(self.files.read(record['id']),'')
                self.assertFalse(self.files.inspect(record['id'])['textExtracted'])
                preview=self.files.preview(record['id']);self.assertEqual(preview['renderer'],'Pillow')
                pixels=Image.open(BytesIO(base64.b64decode(preview['dataUrl'].split(',')[1])))
                self.assertEqual(pixels.size,(40,25));self.assertGreater(pixels.getpixel((10,10))[0],200)
                self.assertTrue(self.files.validate(record['id'])['rendered'])
                self.assertEqual(self.files.bytes(record['id']),data)

    def test_images_reject_corruption_type_mismatch_oversize_and_fake_pages(self):
        for name,data in [('broken.png',b'broken'),('wrong.jpg',picture()),('wide.png',picture(size=(16001,1)))]:
            with self.subTest(name=name),self.assertRaises(ValueError):self.save(name,data)
        self.assertEqual(self.files.list_files(),[])
        saved=self.save('valid.png',picture())
        for page in [0,2,True,-1]:
            with self.assertRaises(ValueError):self.files.preview(saved['id'],page)

    def test_transparency_is_composited_white_for_vision_without_modifying_original(self):
        data=BytesIO();Image.new('RGBA',(20,10),(0,0,0,0)).save(data,format='PNG')
        record=self.save('transparent.png',data.getvalue());preview=self.files.preview(record['id'])
        pixels=Image.open(BytesIO(base64.b64decode(preview['dataUrl'].split(',')[1])))
        self.assertEqual(pixels.getpixel((1,1)),(255,255,255))
        self.assertEqual(self.files.bytes(record['id']),data.getvalue())

    def test_directory_listing_is_not_content_and_extracts_only_selected_members(self):
        raw=bundle([('folder/guide.md',b'# Revision\nRead the chart.'),('folder/chart.png',picture()),('ignored.exe',b'never execute')])
        source=self.save('materials.zip',raw)
        listing=inspect_archive(self.files,source['id'],0,1)
        self.assertEqual(listing['memberCount'],3);self.assertEqual(listing['nextOffset'],1)
        self.assertNotIn('Read the chart',self.files.read(source['id']))
        result=extract_archive(self.files,source['id'],['folder/guide.md'])
        self.assertEqual(len(result),1);child=result[0]
        self.assertEqual(self.files.read(child['id']),'# Revision\nRead the chart.')
        self.assertEqual(child['parentIds'],[source['id']]);self.assertEqual(child['source'],'archive')
        self.assertEqual(child['archiveOrigin']['memberPath'],'folder/guide.md')
        self.assertEqual(child['archiveOrigin']['memberSha256'],child['sha256'])
        self.assertEqual(self.files.bytes(source['id']),raw)
        self.assertEqual(len(self.files.list_files()),2)
        self.assertFalse((self.root/'folder').exists())

    def test_selection_errors_and_corrupt_second_member_do_not_partially_import(self):
        source=self.save('mixed.zip',bundle([('good.md',b'# Good'),('bad.png',b'not a PNG'),('app.exe',b'x')]))
        for selected in [['good.md','bad.png'],['app.exe'],['missing.md'],['good.md','good.md'],['../good.md']]:
            with self.subTest(selected=selected),self.assertRaises(ValueError):extract_archive(self.files,source['id'],selected)
            self.assertEqual(len(self.files.list_files()),1)

    def test_duplicate_names_traversal_absolute_backslash_and_nul_are_rejected(self):
        for member in ['../escape.md','/tmp/escape.md','x/../../escape.md','C:/escape.md','x\\escape.md','x//escape.md']:
            with self.subTest(member=member),self.assertRaises(ValueError):archive_index(bundle([(member,b'body')]))
        for names in [('same.md','same.md'),('A.md','a.md')]:
            with self.assertRaises(ValueError):archive_index(bundle([(n,b'text') for n in names]))
        raw=bundle([('safeX.md',b'text')],compression=ZIP_STORED).replace(b'safeX.md',b'safe\x00.md')
        with self.assertRaises(ValueError):archive_index(raw)

    def test_symlinks_special_files_and_encrypted_metadata_are_rejected(self):
        for mode in (stat.S_IFLNK,stat.S_IFIFO,stat.S_IFSOCK):
            info=ZipInfo('link.md');info.create_system=3;info.external_attr=(mode|0o777)<<16
            with self.assertRaises(ValueError):archive_index(bundle([(info,b'/tmp/outside')]))
        raw=bytearray(bundle([('secret.md',b'data')],compression=ZIP_STORED))
        local=raw.index(b'PK\x03\x04');central=raw.index(b'PK\x01\x02')
        struct.pack_into('<H',raw,local+6,1);struct.pack_into('<H',raw,central+8,1)
        with self.assertRaisesRegex(ValueError,'加密'):archive_index(bytes(raw))

    def test_count_ratio_and_expansion_limits_apply_before_unpacking(self):
        with self.assertRaisesRegex(ValueError,'成员数'):archive_index(bundle([(f'{i}.txt',b'x') for i in range(201)]))
        with self.assertRaisesRegex(ValueError,'比例'):archive_index(bundle([('bomb.txt',b'0'*(1024*1024))]))
        with patch('task_backend.bundle_files.MAX_EXPANDED_BYTES',15):
            with self.assertRaisesRegex(ValueError,'总字节'):archive_index(bundle([('a.txt',b'1234567890'),('b.txt',b'abcdefghij')]))

    def test_actual_member_crc_failure_is_reported_without_import(self):
        raw=bytearray(bundle([('first.md',b'healthy'),('second.md',b'PAYLOAD')],compression=ZIP_STORED))
        at=raw.index(b'PAYLOAD');raw[at]=ord('X')
        source=self.save('crc.zip',bytes(raw))
        with self.assertRaisesRegex(ValueError,'完整性'):extract_archive(self.files,source['id'],['first.md','second.md'])
        self.assertEqual(len(self.files.list_files()),1)

    def test_nested_archive_depth_and_root_budget_cannot_be_reset_per_call(self):
        inner=bundle([('last.txt',b'This is the deepest text')])
        middle=bundle([('inner.zip',inner)])
        root=self.save('root.zip',bundle([('middle.zip',middle)]))
        child=extract_archive(self.files,root['id'],['middle.zip'])[0]
        grandchild=extract_archive(self.files,child['id'],['inner.zip'])[0]
        self.assertEqual(grandchild['archiveOrigin']['rootArchiveId'],root['id'])
        with self.assertRaisesRegex(ValueError,'2 层'):extract_archive(self.files,grandchild['id'],['last.txt'])
        source=self.save('budget.zip',bundle([('one.md',b'1234567890'),('two.md',b'abcdefghij')]))
        extract_archive(self.files,source['id'],['one.md'])
        # Root lineage quota is checked after directory bounds, independently.
        with patch('task_backend.bundle_files.MAX_EXPANDED_BYTES',25):
            origin=dict(archiveId=source['id'],rootArchiveId=source['id'],memberPath='already.md',depth=1)
            self.files._save('already.md',b'abcdefghijklmnop','archive',parents=[source['id']],archive_origin=origin)
            with self.assertRaisesRegex(ValueError,'累计'):extract_archive(self.files,source['id'],['two.md'])

    def test_member_ids_are_stable_across_new_calls_and_do_not_overwrite_originals(self):
        source=self.save('course.zip',bundle([('one.md',b'# First'),('two.md',b'# Second')]))
        one=extract_archive(self.files,source['id'],['one.md'])[0]
        repeated=extract_archive(self.files,source['id'],['two.md','one.md'])
        self.assertEqual(repeated[1]['id'],one['id']);self.assertEqual(len(self.files.list_files()),3)
        (self.files.root/one['id']).write_bytes(b'changed')
        with self.assertRaisesRegex(ValueError,'变化'):extract_archive(self.files,source['id'],['one.md'])

    def test_markdown_relative_images_resolve_only_inside_the_same_bundle(self):
        md=b'# Guide\n![chart](../images/chart.png)\n[remote](https://example.invalid/a)\n![outside](../../escape.png)'
        source=self.save('course.zip',bundle([('notes/guide.md',md),('images/chart.png',picture())]))
        note=extract_archive(self.files,source['id'],['notes/guide.md'])[0]
        refs=related_files(self.files,note['id'])['references']
        self.assertEqual([r['status'] for r in refs],['needs_extraction','external_not_fetched','blocked_path'])
        image=extract_archive(self.files,source['id'],['images/chart.png'])[0]
        refs=related_files(self.files,note['id'])['references']
        self.assertEqual(refs[0]['file']['id'],image['id']);self.assertEqual(refs[0]['status'],'imported')
        loose=self.save('loose.md',b'![chart](images/chart.png)')
        self.assertEqual(related_files(self.files,loose['id'])['references'][0]['status'],'no_bundle_context')

    def test_same_basename_in_two_directories_does_not_merge_members(self):
        source=self.save('reports.zip',bundle([('A/report.md',b'A report'),('B/report.md',b'B report')]))
        files=extract_archive(self.files,source['id'],['A/report.md','B/report.md'])
        self.assertNotEqual(files[0]['id'],files[1]['id'])
        self.assertEqual([self.files.read(f['id']) for f in files],['A report','B report'])


class BundleAgentTests(unittest.TestCase):
    def test_actual_image_reaches_vision_and_archive_retry_recovers_committed_members(self):
        from task_backend.service import BackendService
        from task_backend.agent_tools import AgentTools
        with tempfile.TemporaryDirectory() as temporary,patch('socket.socket.connect',side_effect=AssertionError('No network')):
            service=BackendService(Path(temporary),worker=True);ctx=AgentTools(service,'bundle-agent','chat')
            try:
                source=service.files._save('pack.zip',bundle([('guide.md',b'![chart](chart.png)'),('chart.png',picture())]),'local')
                self.assertEqual(ctx.inspect_archive(source['id'])['memberCount'],2)
                first=ctx.extract_archive(source['id'],['guide.md','chart.png'],call_id='extract')
                self.assertEqual(ctx.extract_archive(source['id'],['guide.md','chart.png'],call_id='extract'),first)
                ctx.db.execute("UPDATE agent_tool_results SET result=NULL WHERE operation='extract_archive'")
                self.assertEqual(ctx.extract_archive(source['id'],['guide.md','chart.png'],call_id='extract'),first)
                self.assertEqual(len(service.files.list_files()),3)
                image=next(f for f in first['files'] if f['extension']=='png')
                calls=[]
                class Vision:
                    async def ainvoke(self,messages):
                        calls.append(messages)
                        return SimpleNamespace(content='图中是红色矩形。',response_metadata={'finish_reason':'stop'})
                ctx.vision_model_factory=lambda config:Vision()
                service.settings.for_request=lambda:dict(apiKey='not-a-real-key',baseUrl='https://example.invalid',model='vision')
                result=asyncio.run(ctx.view_file(image['id'],[1],'What is visible?',call_id='view'))
                self.assertTrue(result['vision']);self.assertEqual(len(calls),1)
                self.assertTrue(any(p.get('type')=='image_url' and p['image_url']['url'].startswith('data:image/png;base64,') for p in calls[0][1].content))
                bad=asyncio.run(ctx.view_file(image['id'],[2],'Read',call_id='bad-page'))
                self.assertEqual(bad['error'],'vision_failed');self.assertEqual(len(calls),1)
            finally:
                ctx.close();service.close()


if __name__=='__main__':unittest.main()
