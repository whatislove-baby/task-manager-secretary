"""MSI layout tests use tiny PE headers; they never execute Windows binaries."""
import importlib.util
from pathlib import Path
import shutil
import struct
import tempfile
import unittest

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
