import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class RpcTests(unittest.TestCase):
    def run_rpc(self, lines):
        with tempfile.TemporaryDirectory() as folder:
            result = subprocess.run([sys.executable, '-m', 'task_backend', '--data-dir', folder], input='\n'.join(lines)+'\n', text=True, encoding='utf-8', capture_output=True, cwd=ROOT, timeout=15)
            self.assertEqual(result.returncode, 0, result.stderr)
            return [json.loads(line) for line in result.stdout.splitlines()]

    def test_request_ids_and_utf8_roundtrip(self):
        results = self.run_rpc([
            json.dumps({'id': 7, 'method': 'createTodo', 'args': [{'title': '中文乌鸦'}]}),
            json.dumps({'id': 8, 'method': 'listTodos', 'args': []}),
        ])
        self.assertEqual([r['id'] for r in results], [7, 8])
        self.assertEqual(results[1]['result']['data'][0]['title'], '中文乌鸦')
        self.assertTrue(results[0]['result']['ok'])

    def test_invalid_json_and_unknown_method_do_not_kill_server(self):
        results = self.run_rpc(['broken', json.dumps({'id':'a','method':'close','args':[]}), json.dumps({'id':'b','method':'listTodos','args':[]})])
        self.assertFalse(results[0]['result']['ok'])
        self.assertFalse(results[1]['result']['ok'])
        self.assertEqual(results[2]['result'], {'ok': True, 'data': []})

    def test_stale_revision_error_then_successful_read(self):
        results = self.run_rpc([json.dumps({'id':1,'method':'createTodo','args':[{'title':'a'}]}), json.dumps({'id':2,'method':'updateTodo','args':['00000000-0000-4000-8000-000000000001',{'title':'b'},1]}), json.dumps({'id':3,'method':'listTodos','args':[]})])
        self.assertFalse(results[1]['result']['ok'])
        self.assertEqual(results[2]['result']['data'][0]['title'], 'a')

    def test_non_list_arguments_and_extra_request_fields_rejected(self):
        results = self.run_rpc([json.dumps({'id':1,'method':'listTodos','args':{}}), json.dumps({'id':2,'method':'listTodos','args':[],'extra':True})])
        self.assertTrue(all(not r['result']['ok'] for r in results))


if __name__ == '__main__':
    unittest.main()

class ConcurrentRpcTests(unittest.TestCase):
    def test_slow_network_request_does_not_block_local_queries(self):
        code='''
import time
from task_backend.__main__ import serve
class Backend:
    METHODS={'extract','listTodos'}
    def __init__(self,*args,**kwargs): pass
    def call(self,method,*args):
        if method=='extract': time.sleep(.3)
        return method
    def close(self): pass
serve('.',factory=Backend)
'''
        result=subprocess.run([sys.executable,'-c',code],input='{"id":1,"method":"extract","args":[]}\n{"id":2,"method":"listTodos","args":[]}\n',text=True,encoding='utf-8',capture_output=True,cwd=ROOT,timeout=10)
        self.assertEqual(result.returncode,0,result.stderr)
        responses=[json.loads(v) for v in result.stdout.splitlines()]
        self.assertEqual([v['id'] for v in responses],[2,1])
