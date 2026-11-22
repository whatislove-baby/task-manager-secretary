"""Native subprocess tests run on Windows too; Docker itself is optional here."""
import os
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
import threading
import time
import unittest
from unittest.mock import patch

from task_backend import skill_execution as execution


class WindowsDockerDiscoveryTests(unittest.TestCase):
    def test_user_install_is_found_without_refreshed_path(self):
        with TemporaryDirectory(prefix='docker user 课程 ') as temporary:
            root = Path(temporary)
            binary = root/'local'/'Programs'/'DockerDesktop'/'resources'/'bin'/'docker.exe'
            binary.parent.mkdir(parents=True); binary.write_bytes(b'fixture'); binary.chmod(0o755)
            with patch.object(execution.sys, 'platform', 'win32'), \
                 patch.object(execution.shutil, 'which', return_value=None), \
                 patch.dict(os.environ, {'LOCALAPPDATA':str(root/'local'),
                                         'ProgramFiles':str(root/'system')}, clear=True):
                self.assertEqual(execution._docker(), str(binary))

    def test_system_install_remains_available_without_localappdata(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            binary = root/'Docker'/'Docker'/'resources'/'bin'/'docker.exe'
            binary.parent.mkdir(parents=True); binary.write_bytes(b'fixture'); binary.chmod(0o755)
            with patch.object(execution.sys, 'platform', 'win32'), \
                 patch.object(execution.shutil, 'which', return_value=None), \
                 patch.dict(os.environ, {'ProgramFiles':str(root)}, clear=True):
                self.assertEqual(execution._docker(), str(binary))

    def test_existing_path_selection_keeps_priority(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            binary = root/'docker.exe'
            binary.write_bytes(b'fixture'); binary.chmod(0o755)
            user_binary = root/'Programs'/'DockerDesktop'/'resources'/'bin'/'docker.exe'
            user_binary.parent.mkdir(parents=True); user_binary.write_bytes(b'fixture'); user_binary.chmod(0o755)
            with patch.object(execution.sys, 'platform', 'win32'), \
                 patch.object(execution.shutil, 'which', return_value=str(binary)), \
                 patch.dict(os.environ, {'LOCALAPPDATA':str(root)}, clear=True):
                self.assertEqual(execution._docker(), str(binary))


class LocalDockerEndpointTests(unittest.TestCase):
    def test_windows_accepts_local_default_and_desktop_linux_pipes(self):
        with patch.object(execution.sys, 'platform', 'win32'):
            for host in ('npipe:////./pipe/docker_engine',
                         'npipe:////./pipe/dockerDesktopLinuxEngine'):
                with self.subTest(host=host):
                    self.assertTrue(execution._local_endpoint(host))

    def test_windows_refuses_remote_or_malformed_endpoints(self):
        with patch.object(execution.sys, 'platform', 'win32'):
            for host in ('npipe:////server/pipe/docker_engine', 'npipe://server/pipe/docker_engine',
                         'npipe:////localhost/pipe/docker_engine', 'tcp://localhost:2375',
                         'ssh://localhost', 'unix:///var/run/docker.sock',
                         'npipe:////./pipe/docker_engine/other', 'npipe:////./pipe/../docker_engine',
                         'npipe:////./pipe/docker_engine\nextra', 'npipe:////./pipe/docker_engine?x=1'):
                with self.subTest(host=host):
                    self.assertFalse(execution._local_endpoint(host))

    def test_unix_keeps_existing_local_socket_scope(self):
        with patch.object(execution.sys, 'platform', 'darwin'):
            self.assertTrue(execution._local_endpoint('unix:///Users/me/.docker/run/docker.sock'))
            for host in ('tcp://127.0.0.1:2375', 'ssh://remote', 'unix://remote/socket',
                         'unix:///', 'unix:///tmp/docker.sock\nmore', 'npipe:////./pipe/docker_engine'):
                self.assertFalse(execution._local_endpoint(host))

    def test_status_pins_the_checked_local_pipe_for_image_inspection(self):
        host = 'npipe:////./pipe/dockerDesktopLinuxEngine'
        replies = [subprocess.CompletedProcess([], 0, host+'\r\n', ''),
                   subprocess.CompletedProcess([], 0, 'linux\r\n', ''),
                   subprocess.CompletedProcess([], 0, 'sha256:abc|'+execution.runtime_hash(), '')]
        with patch.object(execution.sys, 'platform', 'win32'), \
             patch.object(execution, '_docker', return_value='docker.exe'), \
             patch.object(execution, '_control', side_effect=replies) as control:
            status = execution.get_status()
        self.assertTrue(status['available'])
        self.assertEqual(status['localDockerHost'], host)
        self.assertEqual(control.call_args.args[0][:3], ['docker.exe', '--host', host])

    def test_windows_container_daemon_is_rejected_before_image_inspection(self):
        host = 'npipe:////./pipe/docker_engine'
        replies = [subprocess.CompletedProcess([], 0, host, ''),
                   subprocess.CompletedProcess([], 0, 'windows\r\n', '')]
        with patch.object(execution.sys, 'platform', 'win32'), \
             patch.object(execution, '_docker', return_value='docker.exe'), \
             patch.object(execution, '_control', side_effect=replies) as control:
            status = execution.get_status()
        self.assertFalse(status['available'])
        self.assertIn('Linux containers', status['reason'])
        self.assertEqual(control.call_count, 2)

    def test_supplied_endpoint_is_still_checked_as_local(self):
        with patch.object(execution, '_docker', return_value='docker'), \
             patch.object(execution, '_control') as control:
            status = execution.get_status(endpoint='tcp://remote:2375')
        self.assertFalse(status['available'])
        control.assert_not_called()

    def test_windows_user_context_environment_without_secret_or_remote_override(self):
        environment = {'Path':'C:\\Windows', 'USERPROFILE':'C:\\Users\\Student',
                       'SystemRoot':'C:\\Windows', 'TEMP':'C:\\Temp',
                       'DOCKER_CONFIG':'C:\\Users\\Student\\.docker',
                       'DOCKER_HOST':'tcp://remote:2375', 'DOCKER_CONTEXT':'remote',
                       'OPENROUTER_API_KEY':'not-a-real-key', 'SMTP_PASSWORD':'not-a-password'}
        with patch.dict(os.environ, environment, clear=True):
            actual = execution._cli_env()
        self.assertEqual({key.upper(): value for key, value in actual.items()},
                         {key.upper():value for key,value in environment.items()
                          if key in ('Path','USERPROFILE','SystemRoot','TEMP','DOCKER_CONFIG')})


class NativeBoundedPipeTests(unittest.TestCase):
    def capture(self, source, timeout=3):
        return execution._capture([sys.executable, '-I', '-u', '-c', source], timeout)

    def test_reads_stdout_and_stderr_without_pipe_deadlock(self):
        source = "import os\nfor i in range(48):\n os.write(1,b'x'*8192)\n os.write(2,b'y'*1024)\n"
        code, out, err, error = self.capture(source)
        self.assertIsNone(error)
        self.assertEqual(code, 0)
        self.assertEqual(out, b'x'*(48*8192))
        self.assertEqual(err, b'y'*(48*1024))

    def test_binary_transport_does_not_decode_or_change_line_endings(self):
        code, out, err, error = self.capture("import os;os.write(1,b'\\xff\\x00\\r\\n');os.write(2,b'\\xfe\\r\\n')")
        self.assertIsNone(error)
        self.assertEqual((code, out, err), (0, b'\xff\x00\r\n', b'\xfe\r\n'))

    def test_stdout_limit_stops_process_without_collecting_excess(self):
        with patch.object(execution, 'MAX_TRANSPORT', 65536):
            code, out, err, error = self.capture("import os,time;os.write(1,b'x'*131072);time.sleep(30)")
        self.assertIn('传输上限', error)
        self.assertLessEqual(len(out), 65536)

    def test_stderr_has_separate_64k_limit(self):
        code, out, err, error = self.capture("import os,time;os.write(2,b'x'*131072);time.sleep(30)")
        self.assertIn('传输上限', error)
        self.assertLessEqual(len(err), 65536)

    def test_quiet_process_is_killed_at_timeout(self):
        started = time.monotonic()
        code, out, err, error = self.capture('import time;time.sleep(30)', .3)
        self.assertIn('时间上限', error)
        self.assertNotEqual(code, 0)
        self.assertLess(time.monotonic()-started, 5)

    def test_closed_pipes_do_not_skip_process_timeout(self):
        code, out, err, error = self.capture('import os,time;os.close(1);os.close(2);time.sleep(30)', .3)
        self.assertIn('时间上限', error)
        self.assertNotEqual(code, 0)

    def test_nonzero_process_exit_preserves_stderr(self):
        code, out, err, error = self.capture("import sys;sys.stderr.write('Docker is not ready');sys.exit(3)")
        self.assertEqual(code, 3)
        self.assertIsNone(error)
        self.assertEqual(err, b'Docker is not ready')

    def test_readers_are_reaped_after_success_and_timeout(self):
        previous = {thread.ident for thread in threading.enumerate()}
        self.capture('print("ok")')
        self.capture('import time;time.sleep(30)', .2)
        self.assertEqual([thread.name for thread in threading.enumerate()
                          if thread.ident not in previous and thread.name.startswith('tm-skill-')], [])


if __name__ == '__main__':
    unittest.main()
