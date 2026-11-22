"""Renderer discovery must work after relocation, without requiring PATH setup."""
from pathlib import Path
import os
import tempfile
import unittest
from unittest.mock import patch

from task_backend import document_render


class DocumentRuntimePaths(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='tm-runtime-path-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve() / 'Moved app 中文 with spaces'
        self.module_path = self.root / 'backend' / 'task_backend' / 'document_render.py'
        self.module = patch.object(document_render, '__file__', str(self.module_path))
        self.module.start()
        self.addCleanup(self.module.stop)
        self.environment = patch.dict(os.environ, {}, clear=True)
        self.environment.start()
        self.addCleanup(self.environment.stop)

    def executable(self, path):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b'synthetic executable - never run')
        path.chmod(0o755)
        return str(path)

    def test_windows_relocated_bundle_needs_no_global_install(self):
        command = self.executable(self.root / 'libreoffice' / 'program' / 'soffice.com')
        self.executable(self.root / 'libreoffice' / 'program' / 'soffice.exe')
        with patch.object(document_render.sys, 'platform', 'win32'):
            self.assertEqual(document_render.soffice_path(), command)

    def test_windows_gui_launcher_is_supported_when_no_console_wrapper_exists(self):
        command = self.executable(self.root / 'libreoffice' / 'program' / 'soffice.exe')
        with patch.object(document_render.sys, 'platform', 'win32'):
            self.assertEqual(document_render.soffice_path(), command)

    def test_explicit_renderer_overrides_bundled_renderer(self):
        self.executable(self.root / 'libreoffice' / 'program' / 'soffice.com')
        override = self.executable(self.root / 'Custom renderer' / 'soffice.exe')
        with patch.object(document_render.sys, 'platform', 'win32'), patch.dict(os.environ, {'TM_SOFFICE': override}):
            self.assertEqual(document_render.soffice_path(), override)

    def test_prepared_windows_runtime_precedes_machine_install(self):
        prepared = self.executable(self.root / '.libreoffice-runtime-windows' / 'program' / 'soffice.com')
        program_files = self.root / 'Program Files'
        self.executable(program_files / 'LibreOffice' / 'program' / 'soffice.com')
        with patch.object(document_render.sys, 'platform', 'win32'), patch.dict(os.environ, {'PROGRAMFILES': str(program_files)}):
            self.assertEqual(document_render.soffice_path(), prepared)

    def test_windows_developer_can_use_program_files_install(self):
        program_files = self.root / 'Program Files'
        command = self.executable(program_files / 'LibreOffice' / 'program' / 'soffice.exe')
        with patch.object(document_render.sys, 'platform', 'win32'), patch.dict(os.environ, {'ProgramW6432': str(program_files)}):
            self.assertEqual(document_render.soffice_path(), command)

    def test_missing_runtime_reports_configuration_error(self):
        with patch.object(document_render.sys, 'platform', 'win32'), patch.object(Path, 'is_file', return_value=False):
            with self.assertRaisesRegex(ValueError, 'LibreOffice.*TM_SOFFICE'):
                document_render.soffice_path()

    def test_mac_bundled_runtime_is_unchanged(self):
        command = self.executable(self.root / 'libreoffice' / 'LibreOffice.app' / 'Contents' / 'MacOS' / 'soffice')
        with patch.object(document_render.sys, 'platform', 'darwin'):
            self.assertEqual(document_render.soffice_path(), command)


if __name__ == '__main__':
    unittest.main()
