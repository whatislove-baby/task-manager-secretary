import base64
import json
import os
from pathlib import Path
import runpy
import stat
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from task_backend import skill_execution as execution


class SkillExecutionContractTests(unittest.TestCase):
    def test_package_resources_are_checked_before_docker(self):
        with patch.object(execution,'get_status') as status:
            for resources in ({'../host':b'x'},{'a':b'x','a/b':b'y'},{'A':b'x','a':b'y'},
                              {'module.py':'not bytes'},{'asset':b'x'*(execution.MAX_TOTAL+1)}):
                with self.subTest(resources=list(resources)),self.assertRaises(ValueError):
                    execution.run_python('pass',{},package_files=resources)
            with self.assertRaises(ValueError):execution.run_python('pass',{'input':b'a'},package_files={'asset':b'x'*execution.MAX_TOTAL})
        status.assert_not_called()

    def test_package_bytes_are_staged_readonly_separately_from_input_and_wrapper(self):
        status=dict(available=True,imageId='sha256:sample',runtimeHash='sample',localDockerHost='unix:///local.sock')
        seen=[]
        def capture(command,timeout):
            mount=next(v for v in command if 'dst=/work,readonly' in v)
            root=Path(mount.split('src=',1)[1].split(',dst=',1)[0])
            seen.append((root/'package/app/module.py').read_bytes())
            file_stat=(root/'package/app/module.py').stat()
            if os.name=='nt':
                # Windows chmod maps to the file's native read-only attribute;
                # the Docker mount above must still be explicitly readonly.
                self.assertTrue(file_stat.st_file_attributes & stat.FILE_ATTRIBUTE_READONLY)
                self.assertEqual(file_stat.st_mode & stat.S_IWRITE,0)
            else:
                self.assertEqual(file_stat.st_mode&0o777,0o444)
            self.assertEqual((root/'main.py').read_bytes(),b'# original\r\npass\r\n')
            self.assertTrue((root/'runner.py').exists())
            return 0,json.dumps(dict(protocol=1,exitCode=0,stdout='',stderr='',outputs={})).encode(),b'',None
        with patch.object(execution,'get_status',return_value=status),patch.object(execution,'_docker',return_value='/bin/docker'), \
             patch.object(execution,'_capture',side_effect=capture),patch.object(execution,'_control',return_value=subprocess.CompletedProcess([],0,'','')):
            result=execution.run_python('# original\r\npass\r\n',{},package_files={'app/module.py':b'# unchanged\r\nx=1\r\n'})
        self.assertTrue(result['ok']);self.assertEqual(seen,[b'# unchanged\r\nx=1\r\n'])

    def test_unavailable_docker_never_runs_code_on_host(self):
        with patch.object(execution, '_docker', return_value=None), patch.object(execution, '_capture') as capture:
            result = execution.run_python('raise SystemExit(0)', {})
        self.assertFalse(result['ok']); self.assertIsNone(result['exitCode'])
        self.assertIn('Docker', result['error']); capture.assert_not_called()

    def test_source_input_and_path_boundaries_are_checked_before_docker(self):
        cases = [('print(1)', {'../outside': b'x'}), ('print(1)', {'/etc/passwd': b'x'}),
                 ('print(1)', {'a\\b': b'x'}), ('print(1)', {'a.csv': b'a', 'A.csv': b'b'}),
                 ('print(1)', {'a': b'a', 'a/b': b'b'}), ('', {}), ('x'*128001, {}),
                 ('print(1)', {'x': 'not bytes'}), ('print(1)', {'x': b'x'*(execution.MAX_TOTAL+1)})]
        with patch.object(execution, 'get_status') as status:
            for source, data in cases:
                with self.subTest(data=list(data)):
                    with self.assertRaises(ValueError): execution.run_python(source, data)
            for timeout in (True, 0, 61):
                with self.assertRaises(ValueError): execution.run_python('pass', {}, timeout_seconds=timeout)
        status.assert_not_called()

    def test_remote_docker_context_is_rejected(self):
        with patch.object(execution, '_docker', return_value='/bin/docker'), patch.object(execution, '_control', return_value=subprocess.CompletedProcess([], 0, 'ssh://remote.example', '')) as call:
            status = execution.get_status()
        self.assertFalse(status['available']); self.assertEqual(call.call_count, 1)

    def test_runtime_version_mismatch_is_rejected(self):
        endpoint = 'npipe:////./pipe/docker_engine' if os.name == 'nt' else 'unix:///local.sock'
        replies = [subprocess.CompletedProcess([], 0, endpoint, ''),
                   subprocess.CompletedProcess([], 0, 'linux', ''),
                   subprocess.CompletedProcess([], 0, 'sha256:abc|outdated', '')]
        with patch.object(execution, '_docker', return_value='/bin/docker'), patch.object(execution, '_control', side_effect=replies) as control:
            status = execution.get_status()
        self.assertFalse(status['available'])
        self.assertIn('版本与本应用不一致', status['reason'])
        self.assertEqual(control.call_count, 3)

    def test_business_exit_two_cannot_hide_partial_files_in_transport(self):
        response = dict(protocol=1, exitCode=2, stdout='', stderr='PRICE missing',
                        outputs={'partial.csv': base64.b64encode(b'bad').decode()})
        result = execution._decode(0, json.dumps(response).encode(), b'')
        self.assertEqual(result['exitCode'], 2); self.assertEqual(result['outputs'], {})
        self.assertEqual(result['stderr'], 'PRICE missing'); self.assertIn('部分输出', result['error'])

    def test_transport_is_not_trusted_as_paths_or_unlimited_bytes(self):
        base = dict(protocol=1, exitCode=0, stdout='', stderr='')
        for outputs in ({'../outside': 'eA=='}, {'/tmp/other': 'eA=='}, {'a': 'not base64'},
                        {'a': 'eA==', 'A': 'eQ=='}, []):
            result = execution._decode(0, json.dumps(dict(base, outputs=outputs)).encode(), b'')
            self.assertFalse(result['ok']); self.assertEqual(result['outputs'], {})
        for data in (b'invalid', json.dumps({'protocol': 1}).encode()):
            self.assertFalse(execution._decode(0, data, b'')['ok'])

    def test_nonzero_container_exit_does_not_import_outputs(self):
        result = execution._decode(137, b'{"outputs": {"file": "eA=="}}', b'')
        self.assertFalse(result['ok']); self.assertEqual(result['outputs'], {})
        self.assertIn('资源', result['error'])

    def test_cli_environment_does_not_forward_keys_or_remote_overrides(self):
        with patch.dict(os.environ, {'OPENROUTER_API_KEY': 'private', 'SMTP_PASSWORD': 'private',
                                     'DOCKER_HOST': 'tcp://remote:1234', 'DOCKER_CONTEXT': 'remote'}):
            env = execution._cli_env()
        for key in ('OPENROUTER_API_KEY', 'SMTP_PASSWORD', 'DOCKER_HOST', 'DOCKER_CONTEXT'):
            self.assertNotIn(key, env)

    def test_lost_daemon_during_cleanup_does_not_claim_completion(self):
        status = dict(available=True, imageId='sha256:sample', runtimeHash='sample', localDockerHost='unix:///local.sock')
        payload = json.dumps(dict(protocol=1, exitCode=0, stdout='', stderr='', outputs={'ok.txt':'eA=='})).encode()
        with patch.object(execution, 'get_status', return_value=status), patch.object(execution, '_docker', return_value='/bin/docker'), \
             patch.object(execution, '_capture', return_value=(0,payload,b'',None)), \
             patch.object(execution, '_control', return_value=subprocess.CompletedProcess([],1,'','Cannot connect to daemon')):
            result = execution.run_python('pass', {})
        self.assertFalse(result['ok']); self.assertFalse(result['cleanupConfirmed'])
        self.assertEqual(result['outputs'], {}); self.assertIn('清理', result['error'])

    def test_wrapper_refuses_host_execution_before_spawn_or_process_signals(self):
        wrapper = runpy.run_path(str(execution.RUNTIME / 'runner.py'), run_name='test_skill_wrapper')
        with patch.object(wrapper['os'], 'getpid', return_value=12345), \
             patch.object(wrapper['os'], 'kill') as kill, \
             patch.object(wrapper['subprocess'], 'Popen') as spawn:
            for function in ('main', 'stop_background_processes'):
                with self.assertRaisesRegex(RuntimeError, 'PID 1'): wrapper[function]()
        kill.assert_not_called(); spawn.assert_not_called()


@unittest.skipUnless(os.environ.get('TM_RUN_SKILL_DOCKER_TESTS') == '1', 'Opt-in real Docker runtime acceptance')
class SkillExecutionDockerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        state = execution.get_status()
        if not state['available']: raise AssertionError(state['reason'])

    def test_real_data_processing_and_preinstalled_document_libraries(self):
        source = '''import os
from pathlib import Path
from decimal import Decimal
import pandas, openpyxl, docx, pypdf, PIL, reportlab
root = Path(os.environ['TM_SKILL_INPUT_DIR'])
out = Path(os.environ['TM_SKILL_OUTPUT_DIR'])
table = pandas.read_csv(root/'orders.csv', dtype=str, keep_default_na=False)
workbook = openpyxl.Workbook(); sheet = workbook.active
sheet.append(['ITEM','Amount'])
for row in table.to_dict('records'):
    sheet.append([row['ITEM'], str(Decimal(row['QTY'])*Decimal(row['PRICE']))])
workbook.save(out/'result.xlsx')
print('processed',len(table))
'''
        result = execution.run_python(source, {'orders.csv': b'ITEM,QTY,PRICE\n00127,125,0.095\n'})
        self.assertTrue(result['ok'], result); self.assertEqual(set(result['outputs']), {'result.xlsx'})
        import io, openpyxl
        book = openpyxl.load_workbook(io.BytesIO(result['outputs']['result.xlsx']))
        self.assertEqual(list(book.active.values), [('ITEM','Amount'),('00127','11.875')])

    def test_real_isolation_no_host_file_network_or_credentials_and_readonly_input(self):
        with tempfile.TemporaryDirectory() as directory:
            sentinel = Path(directory)/'host-secret.txt'; sentinel.write_text('private sentinel')
            source = '''import os, json, socket
from pathlib import Path
facts={}
try: Path(HOST_PATH).read_bytes(); facts['hostDenied']=False
except OSError: facts['hostDenied']=True
try: socket.create_connection(('1.1.1.1',443),timeout=.5); facts['networkDenied']=False
except OSError: facts['networkDenied']=True
try: Path('/input/original.txt').write_text('changed'); facts['inputReadonly']=False
except OSError: facts['inputReadonly']=True
try: Path('/etc/skill-created').write_text('changed'); facts['rootReadonly']=False
except OSError: facts['rootReadonly']=True
facts['noSecretEnv']='OPENROUTER_API_KEY' not in os.environ
facts['nonroot']=os.getuid()!=0
facts['noDockerSocket']=not Path('/var/run/docker.sock').exists()
facts['memoryLimit']=Path('/sys/fs/cgroup/memory.max').read_text().strip()=='536870912'
facts['pidsLimit']=Path('/sys/fs/cgroup/pids.max').read_text().strip()=='64'
Path('/output/proof.json').write_text(json.dumps(facts))
'''.replace('HOST_PATH', repr(str(sentinel)))
            with patch.dict(os.environ, {'OPENROUTER_API_KEY': 'synthetic-not-secret'}):
                result = execution.run_python(source, {'original.txt': b'unchanged'})
            self.assertTrue(result['ok'], result)
            self.assertEqual(sentinel.read_text(), 'private sentinel')
            facts = json.loads(result['outputs']['proof.json'])
            self.assertTrue(all(facts.values()), facts)

    def test_business_rejection_requires_empty_output_directory(self):
        source = "import sys\nprint('PRICE missing',file=sys.stderr)\nsys.exit(2)"
        result = execution.run_python(source, {})
        self.assertEqual(result['exitCode'], 2, result); self.assertEqual(result['outputs'], {})
        self.assertNotIn('error', result); self.assertIn('PRICE missing', result['stderr'])

    def test_business_failure_with_partial_file_or_link_is_not_valid_rejection(self):
        for action in ("Path('/output/partial.csv').write_text('wrong')", "Path('/output/link').symlink_to('/input/missing')"):
            with self.subTest(action=action):
                source = "from pathlib import Path\nimport sys\n"+action+"\nprint('PRICE missing',file=sys.stderr)\nsys.exit(2)"
                result = execution.run_python(source, {})
                self.assertEqual(result['exitCode'], 2, result); self.assertEqual(result['outputs'], {})
                self.assertIn('部分输出', result['error'])

    def test_output_symlink_cannot_be_imported(self):
        result = execution.run_python("from pathlib import Path\nPath('/output/link').symlink_to('/input/data')", {'data': b'private'})
        self.assertFalse(result['ok']); self.assertEqual(result['outputs'], {})
        self.assertIn('链接', result['error'])

    def test_output_log_is_capped(self):
        result = execution.run_python("print('x'*100000)", {})
        self.assertFalse(result['ok']); self.assertIn('日志', result['error'])

    def test_detached_background_children_are_reaped_before_output_collection(self):
        grandchild = "from pathlib import Path\nimport time\nPath('/tmp/grandchild.ready').touch()\ntime.sleep(30)"
        child = """import subprocess,sys,time
from pathlib import Path
subprocess.Popen([sys.executable,'-I','-c',GRANDCHILD],stdin=subprocess.DEVNULL,
                 stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,start_new_session=True)
Path('/tmp/child.ready').touch()
time.sleep(30)
""".replace('GRANDCHILD', repr(grandchild))
        source = """import subprocess,sys,time
from pathlib import Path
subprocess.Popen([sys.executable,'-I','-c',CHILD],stdin=subprocess.DEVNULL,
                 stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,start_new_session=True)
deadline=time.monotonic()+3
while not (Path('/tmp/child.ready').exists() and Path('/tmp/grandchild.ready').exists()):
    if time.monotonic()>deadline:raise RuntimeError('background fixture not ready')
    time.sleep(.01)
Path('/output/result.txt').write_text('complete')
""".replace('CHILD', repr(child))
        result = execution.run_python(source, {}, timeout_seconds=6)
        self.assertTrue(result['ok'], result)
        self.assertEqual(result['outputs'], {'result.txt': b'complete'})
        self.assertEqual(result['backgroundProcessesReaped'], 2)
        self.assertTrue(result['cleanupConfirmed'])

    def test_timeout_kills_owned_container(self):
        with patch.object(execution, '_command', wraps=execution._command) as command:
            result = execution.run_python('while True: pass', {}, timeout_seconds=2)
        self.assertFalse(result['ok']); self.assertIn('时间', result['error'])
        owned_name = command.call_args.args[2]
        listing = execution._control([execution._docker(), 'ps', '--filter', 'name=^/'+owned_name+'$', '--format', '{{.Names}}'])
        self.assertEqual(listing.returncode, 0); self.assertEqual(listing.stdout.strip(), '')

