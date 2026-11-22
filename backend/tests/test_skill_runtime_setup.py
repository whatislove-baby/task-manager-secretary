"""Runtime preparation must use the same checked local daemon as execution."""
import contextlib
import io
import os
from pathlib import Path
import runpy
import subprocess
import unittest
from unittest.mock import patch

from task_backend import skill_execution as execution


class SkillRuntimeSetupTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.setup = staticmethod(runpy.run_path(str(Path(__file__).resolve().parents[2] /
                                                   'scripts/prepare-skill-runtime.py'))['main'])

    def test_windows_setup_and_final_check_pin_same_local_pipe_without_shell_overrides(self):
        host = 'npipe:////./pipe/dockerDesktopLinuxEngine'
        runtime_hash = execution.runtime_hash()
        replies = [subprocess.CompletedProcess([], 0, host+'\r\n', ''),
                   subprocess.CompletedProcess([], 0, 'linux\r\n', ''),
                   subprocess.CompletedProcess([], 0, 'linux\r\n', ''),
                   subprocess.CompletedProcess([], 0, 'sha256:sample|'+runtime_hash, '')]
        environment = {'PATH': 'docker-path', 'USERPROFILE': 'C:\\Users\\Student',
                       'SystemRoot': 'C:\\Windows', 'DOCKER_HOST': 'tcp://remote:2375',
                       'DOCKER_CONTEXT': 'remote', 'OPENROUTER_API_KEY': 'synthetic-secret'}
        with patch.dict(os.environ, environment, clear=True), \
             patch.object(execution.sys, 'platform', 'win32'), \
             patch.object(execution.subprocess, 'CREATE_NO_WINDOW', 0x08000000, create=True), \
             patch.dict(self.setup.__globals__, {'_docker': lambda: 'docker.exe'}), \
             patch.object(execution, '_docker', return_value='docker.exe'), \
             patch.object(execution, '_control', side_effect=replies) as control, \
             patch.object(subprocess, 'run') as run, contextlib.redirect_stdout(io.StringIO()) as output:
            self.setup()
        self.assertIn('SKILL_RUNTIME_READY sha256:sample '+runtime_hash, output.getvalue())
        self.assertEqual([call.args[0][3] for call in run.call_args_list], ['pull', 'build'])
        for call in run.call_args_list:
            self.assertEqual(call.args[0][:3], ['docker.exe', '--host', host])
            self.assertEqual(call.kwargs['env'], {key:value for key,value in environment.items()
                                                if key in ('PATH', 'USERPROFILE', 'SystemRoot')})
            self.assertEqual(call.kwargs['creationflags'], 0x08000000)
            self.assertTrue(call.kwargs['check'])
        self.assertIn('org.taskmanager.skill-runtime='+runtime_hash, run.call_args_list[1].args[0])
        # Final verification does not re-read a potentially changed context.
        self.assertEqual(control.call_args_list[0].args[0][1:3], ['context', 'inspect'])
        for call in control.call_args_list[1:]:
            self.assertEqual(call.args[0][:3], ['docker.exe', '--host', host])

    def test_remote_windows_container_or_unavailable_daemon_never_pull_or_build(self):
        host = 'npipe:////./pipe/docker_engine'
        cases = [
            ([subprocess.CompletedProcess([], 0, 'ssh://remote', '')], '本机'),
            ([subprocess.CompletedProcess([], 0, host, ''),
              subprocess.CompletedProcess([], 0, 'windows', '')], 'Linux containers'),
            ([subprocess.CompletedProcess([], 0, host, ''),
              subprocess.CompletedProcess([], 1, '', 'daemon unavailable')], '启动 Docker'),
        ]
        for replies, reason in cases:
            with self.subTest(reason=reason), \
                 patch.object(execution.sys, 'platform', 'win32'), \
                 patch.dict(self.setup.__globals__, {'_docker': lambda: 'docker.exe'}), \
                 patch.object(execution, '_control', side_effect=replies), \
                 patch.object(subprocess, 'run') as run:
                with self.assertRaisesRegex(SystemExit, reason): self.setup()
                run.assert_not_called()


if __name__ == '__main__':
    unittest.main()
