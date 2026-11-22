"""MSI layout tests use tiny PE headers; they never execute Windows binaries."""
import importlib.util
from pathlib import Path
import shutil
import struct
import tempfile
import unittest
from unittest.mock import patch

SCRIPT = Path(__file__).resolve().parents[2] / 'scripts/prepare-document-runtime-windows.py'
SPEC = importlib.util.spec_from_file_location('prepare_document_windows', SCRIPT)
runtime = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(runtime)


def pe(path, machine=0x8664, marker=b'fixture'):
    path.parent.mkdir(parents=True, exist_ok=True)
    data = bytearray(128)
    data[:2] = b'MZ'
    struct.pack_into('<I', data, 60, 64)
    data[64:68] = b'PE\0\0'
    struct.pack_into('<H', data, 68, machine)
    path.write_bytes(data + marker)


class WindowsCrtBundleTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.image = self.root / '行政镜像 with spaces'
        self.source = self.image / 'LibreOffice'
        (self.source / 'program').mkdir(parents=True)
        (self.source / 'LICENSE.html').write_text(
            '<h2>Microsoft Visual C++ 2015 Runtime Libraries</h2>\nMICROSOFT SOFTWARE LICENSE TERMS', encoding='utf-8')
        for name in runtime.REQUIRED_CRT | {'msvcp140_atomic_wait.dll'}:
            pe(self.image / 'System64Folder' / name)
        self.target = self.root / 'bundle'
        shutil.copytree(self.source, self.target)

    def test_merge_module_crt_copied_with_architecture_hash_and_original_license(self):
        # All can coexist in an administrative image. Only the x64 merge-module
        # files and soffice's immediate directory belong in its runtime bundle.
        pe(self.image / 'SystemFolder/vcruntime140.dll', 0x14c, b'x86')
        pe(self.image / 'arm64/vcruntime140.dll', 0xaa64, b'arm64')
        pe(self.source / 'program/python-core/vcruntime140.dll', marker=b'private Python')
        manifest = runtime.bundle_msi_crt(self.image, self.source, self.target)
        self.assertEqual(len(manifest['files']), 4)
        self.assertEqual(manifest['licenses'], ['LICENSE.html'])
        for entry in manifest['files']:
            self.assertEqual(runtime.pe_machine(self.target / entry['file']), 0x8664)
            self.assertEqual(runtime.sha256(self.target / entry['file']), entry['sha256'])
            self.assertTrue(entry['source'].startswith('System64Folder/'))

    def test_conflicting_x64_merge_modules_are_rejected(self):
        pe(self.image / 'another/vcruntime140.dll', marker=b'conflict')
        with self.assertRaisesRegex(RuntimeError, 'Conflicting x64 CRT'):
            runtime.bundle_msi_crt(self.image, self.source, self.target)

    def test_missing_required_dll_is_not_hidden_by_wrong_architecture(self):
        pe(self.image / 'System64Folder/vcruntime140_1.dll', 0xaa64)
        with self.assertRaisesRegex(RuntimeError, 'lacks app-local x64 CRT.*vcruntime140_1'):
            runtime.bundle_msi_crt(self.image, self.source, self.target)

    def test_existing_target_dll_must_match_msi(self):
        pe(self.target / 'program/msvcp140.dll', marker=b'changed')
        with self.assertRaisesRegex(RuntimeError, 'Conflicting app-local CRT'):
            runtime.bundle_msi_crt(self.image, self.source, self.target)

    def test_license_must_identify_the_microsoft_runtime(self):
        (self.source / 'LICENSE.html').write_text('Only unrelated library licenses.')
        with self.assertRaisesRegex(RuntimeError, r'missing its Microsoft Visual C\+\+ license'):
            runtime.bundle_msi_crt(self.image, self.source, self.target)

    def test_external_vendor_notice_is_retained_verbatim(self):
        content = (self.source / 'LICENSE.html').read_bytes()
        (self.source / 'LICENSE.html').unlink()
        notice = self.image / 'Vendor Notices/license.html'
        notice.parent.mkdir()
        notice.write_bytes(content)
        manifest = runtime.bundle_msi_crt(self.image, self.source, self.target)
        self.assertEqual((self.target / manifest['licenses'][0]).read_bytes(), content)

    def test_guid_suffixed_msi_filename_keeps_the_canonical_dll_name(self):
        dll = self.image / 'System64Folder/vcruntime140.dll'
        dll.rename(dll.with_name(dll.name + '.ABCDEF01_0123_4567_89AB_0123456789AB'))
        manifest = runtime.bundle_msi_crt(self.image, self.source, self.target)
        self.assertTrue((self.target / 'program/vcruntime140.dll').is_file())
        self.assertTrue(any('.dll.ABCDEF' in item['source'] for item in manifest['files']))

    def test_static_runtime_does_not_force_unreferenced_crt(self):
        shutil.rmtree(self.image / 'System64Folder')
        with patch.object(runtime, 'visual_studio_crt', side_effect=AssertionError('unneeded fallback')):
            manifest = runtime.bundle_msi_crt(self.image, self.source, self.target,
                                               required_crt=set(), allow_visual_studio=True)
        self.assertEqual(manifest['files'], [])
        self.assertEqual(manifest['requiredByImports'], [])

    def test_missing_dynamic_runtime_uses_complete_official_redist_set(self):
        redist = self.root / 'Visual Studio/VC/Redist/MSVC'
        folder = redist / '14.44.35211/x64/Microsoft.VC143.CRT'
        shutil.copytree(self.image / 'System64Folder', folder)
        shutil.rmtree(self.image / 'System64Folder')
        files = {p.name: p for p in folder.iterdir()}
        vendor = dict(root=redist, version='14.44.35211', directory=folder.relative_to(redist).as_posix())
        with patch.object(runtime, 'visual_studio_crt', return_value=(files, vendor)), \
                patch.object(runtime, 'copy_crt_license', return_value='third-party-notices/CRT.docx'):
            manifest = runtime.bundle_msi_crt(self.image, self.source, self.target,
                required_crt={'vcruntime140.dll'}, allow_visual_studio=True)
        self.assertIn('VC/Redist', manifest['source'])
        self.assertEqual(manifest['version'], '14.44.35211')
        self.assertEqual(len(manifest['files']), 4)

    def test_real_import_and_delay_import_tables_determine_requirements(self):
        file = self.source / 'program/example.dll'
        data = bytearray(1024)
        data[:2] = b'MZ'; struct.pack_into('<I', data, 60, 128)
        data[128:132] = b'PE\0\0'; struct.pack_into('<H', data, 134, 1)
        struct.pack_into('<H', data, 148, 240)
        optional = 152
        struct.pack_into('<H', data, optional, 0x20b)
        struct.pack_into('<I', data, optional + 60, 512)
        struct.pack_into('<I', data, optional + 108, 16)
        struct.pack_into('<II', data, optional + 112 + 8, 0x1000, 40)
        struct.pack_into('<II', data, optional + 112 + 13 * 8, 0x1080, 64)
        struct.pack_into('<IIII', data, optional + 240 + 8, 512, 0x1000, 512, 512)
        struct.pack_into('<I', data, 512 + 12, 0x1060)
        data[608:627] = b'VCRUNTIME140_1.dll\0'
        struct.pack_into('<II', data, 640, 1, 0x10c0)
        data[704:717] = b'msvcp140.dll\0'
        file.write_bytes(data)
        self.assertEqual(runtime.pe_imports(file), {'vcruntime140_1.dll', 'msvcp140.dll'})
