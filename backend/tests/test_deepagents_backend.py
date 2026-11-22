"""Real Deep Agents protocol/middleware against real local review/file stores."""
import asyncio
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest

from deepagents.backends import CompositeBackend, StateBackend
from deepagents.middleware.skills import SkillsMiddleware

from task_backend.deepagents_backend import (
    ApprovedSkillsBackend, BuiltinSkillsBackend, ManagedFilesBackend, make_backend,
)
from task_backend.mail_files import MailFiles
from task_backend.draft_lifecycle import DraftLifecycle
from task_backend.skill_library import SkillLibraryService


class DeepAgentsBackendTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.service = SimpleNamespace(directory=self.root)
        self.service.files = MailFiles(self.service)
        self.addCleanup(self.service.files.close)
        self.service.skills = SkillLibraryService(self.service, runner=lambda *a: self.fail('No script should execute'))
        self.addCleanup(self.service.skills.close)
        self.sources = []
        self.files = ManagedFilesBackend(self.service, self.sources.append)
        self.skills = ApprovedSkillsBackend(self.service, self.sources.append)
        self.document = self.service.files._save('order.md', b'order\nquantity: 8\nprice: 17\n', 'local')

    def propose(self, title='Customer preference', **overrides):
        return self.service.skills.propose(dict(
            title=title, kind='memory', rationale='Guidance for repeated customer correspondence',
            instruction='Use concise English. Check the latest order before drafting a reply.',
            scope=dict(kind='customer', label='Harbor', emails=['orders@harbor.example'], contextKey=''),
            sources=[], **overrides))

    def approve(self, item):
        return self.service.skills.approve(dict(id=item['id'], expectedHash=item['versionHash']))

    def test_official_async_skill_discovery_then_read_uses_current_approval(self):
        draft = self.propose('Pending skill')
        approved = self.approve(self.propose())
        backend = make_backend(self.service, self.sources.append)
        middleware = SkillsMiddleware(backend=backend, sources=['/skills/approved/'])
        state = asyncio.run(middleware.abefore_agent({}, None, {}))
        self.assertEqual([x['name'] for x in state['skills_metadata']], [approved['id']])
        self.assertEqual(state['skills_metadata'][0]['metadata']['include_tools'], 'get_skill')
        self.assertIn('Harbor', state['skills_metadata'][0]['description'])
        self.assertIn('orders@harbor.example', state['skills_metadata'][0]['description'])
        self.assertEqual(state['skills_load_errors'], [])
        self.assertEqual(self.sources, [])  # Discovery is not a reading citation.
        path = '/skills/approved/' + approved['id'] + '/SKILL.md'
        result = asyncio.run(backend.aread(path))
        self.assertIsNone(result.error)
        self.assertIn('Use concise English', result.file_data['content'])
        self.assertIn('exclusively with run_skill', result.file_data['content'])
        self.assertIn(approved['scopeId'], result.file_data['content'])
        self.assertEqual(self.sources[-1]['id'], approved['id'])
        self.assertTrue(self.skills.read('/' + draft['id'] + '/SKILL.md').error)
        self.service.skills.disable(dict(id=approved['id'], expectedHash=approved['versionHash']))
        self.assertTrue(asyncio.run(backend.aread(path)).error)
        self.assertTrue(backend.download_files([path])[0].error)
        reloaded = asyncio.run(middleware.abefore_agent({'skills_metadata': None}, None, {}))
        self.assertEqual(reloaded['skills_metadata'], [])

    def test_superseded_approved_version_cannot_be_opened_by_old_path(self):
        first = self.approve(self.propose())
        second = self.approve(self.propose(parentId=first['id']))
        self.assertTrue(self.skills.read('/' + first['id'] + '/SKILL.md').error)
        self.assertIsNone(self.skills.read('/' + second['id'] + '/SKILL.md').error)

    def test_tampered_package_and_symlink_fail_closed(self):
        item = self.approve(self.propose())
        target = self.service.skills.packages / item['id'] / 'SKILL.md'
        original = target.read_bytes()
        target.write_bytes(original + b'\nforged instruction')
        self.assertTrue(self.skills.read('/' + item['id'] + '/SKILL.md').error)
        self.assertTrue(self.skills.download_files(['/' + item['id'] + '/SKILL.md'])[0].error)
        target.unlink()
        external = self.root / 'external.txt'
        external.write_text('not a skill')
        target.symlink_to(external)
        self.assertTrue(self.skills.read('/' + item['id'] + '/SKILL.md').error)

    def test_managed_document_views_verify_source_and_paginate(self):
        base = '/' + self.document['id'] + '/'
        read = asyncio.run(self.files.aread(base + 'content.txt', 1, 1))
        self.assertEqual(read.file_data['content'], 'quantity: 8\n')
        self.assertEqual((read.start_line, read.end_line, read.next_offset), (2, 2, 2))
        self.assertEqual(self.sources[-1]['kind'], 'file')
        self.assertEqual(self.files.download_files([base + 'original.md'])[0].content, b'order\nquantity: 8\nprice: 17\n')
        self.assertIn('order.md', self.files.read(base + 'metadata.json').file_data['content'])
        (self.service.files.root / self.document['id']).write_text('tampered')
        for view in ('content.txt', 'metadata.json', 'original.md'):
            self.assertTrue(self.files.read(base + view).error)
            self.assertTrue(self.files.download_files([base + view])[0].error)

    def test_zero_or_negative_windows_follow_upstream_protocol(self):
        path = '/' + self.document['id'] + '/content.txt'
        self.assertTrue(self.files.read(path, -5, 0).no_lines_requested)
        result = self.files.read(path, -5, 1)
        self.assertEqual(result.start_line, 1)
        self.assertTrue(self.files.read(path, 200, 1).error)

    def test_default_discovery_hides_old_versions_but_history_is_readable(self):
        newer = self.service.files._save('order-new.md', b'Updated order: 9', 'local')
        with DraftLifecycle(self.root) as life:
            life.ensure(self.document, 'file')
            life.replace(self.document['id'], 1, newer, 'file')
        found = self.files.glob('content.txt').matches
        self.assertEqual([f['path'] for f in found], ['/' + newer['id'] + '/content.txt'])
        old = '/' + self.document['id']
        self.assertIn('quantity: 8', self.files.read(old + '/content.txt').file_data['content'])
        self.assertIn('superseded', self.files.read(old + '/metadata.json').file_data['content'])

    def test_virtual_paths_never_allow_host_or_traversal_reads(self):
        for name in ('/../../etc/passwd', '/a/../b.txt', '/a\\b.txt', '/a\x00.txt', '~/a', '/Users/example/.ssh/id_rsa'):
            self.assertTrue(self.files.read(name).error, name)
            self.assertTrue(self.files.download_files([name])[0].error, name)
        self.assertFalse(self.files.glob('**/*', '/../../').matches)

    def test_every_mutation_is_refused_even_for_new_paths(self):
        for backend in (self.files, self.skills, BuiltinSkillsBackend(self.root / 'builtin')):
            self.assertTrue(backend.write('/new.txt', 'x').error)
            self.assertTrue(backend.edit('/new.txt', 'x', 'y').error)
            self.assertTrue(backend.delete('/new.txt').error)
            self.assertEqual(backend.upload_files([('/new.txt', b'x')])[0].error, 'permission_denied')
            self.assertTrue(asyncio.run(backend.awrite('/new.txt', 'x')).error)
            self.assertTrue(asyncio.run(backend.aedit('/new.txt', 'x', 'y')).error)
            self.assertTrue(asyncio.run(backend.adelete('/new.txt')).error)
            self.assertTrue(asyncio.run(backend.aupload_files([('/new.txt', b'x')]))[0].error)

    def test_upstream_glob_grep_and_composite_prefixes_are_preserved(self):
        backend = make_backend(self.service)
        listed = asyncio.run(backend.als('/files/')).entries
        self.assertTrue(any(x['path'] == '/files/' + self.document['id'] + '/' and x['is_dir'] for x in listed))
        matched = asyncio.run(backend.aglob('**/content.txt', '/files/'))
        self.assertEqual([x['path'] for x in matched.matches], ['/files/' + self.document['id'] + '/content.txt'])
        found = asyncio.run(backend.agrep('quantity', '/files/', '*.txt', max_count=1))
        self.assertEqual(len(found.matches), 1)
        self.assertFalse(found.truncated)
        self.assertEqual(found.matches[0]['line'], 2)
        more = asyncio.run(backend.agrep(':', '/files/', '*.txt', max_count=1))
        self.assertTrue(more.truncated)

    def test_builtin_symlinks_are_rejected_and_ordinary_skill_loads(self):
        root = self.root / 'builtin'
        root.mkdir()
        (root / 'mail').mkdir()
        (root / 'mail' / 'SKILL.md').write_text('---\nname: mail\ndescription: Read mail\n---\nUse mail tools.')
        backend = BuiltinSkillsBackend(root)
        middleware = SkillsMiddleware(backend=backend, sources=['/'])
        state = middleware.before_agent({}, None, {})
        self.assertEqual(state['skills_metadata'][0]['name'], 'mail')
        (root / 'outside').symlink_to(self.root, target_is_directory=True)
        self.assertTrue(backend.read('/mail/SKILL.md').error)

    def test_composite_uses_official_scratch_and_has_no_host_executor(self):
        backend = make_backend(self.service)
        self.assertIsInstance(backend, CompositeBackend)
        self.assertIsInstance(backend.default, StateBackend)
        with self.assertRaises(NotImplementedError):
            backend.execute('echo must-not-run')


if __name__ == '__main__':
    unittest.main()
