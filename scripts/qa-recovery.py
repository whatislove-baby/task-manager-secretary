"""Reproducible OS-process recovery checks with synthetic transport boundaries.

Run: .venv/bin/python scripts/qa-recovery.py
No live network, accounts, OS Keychain, or user data are used. Child processes
run production BackendService/MailCompose/MailOperations against temporary DBs.
"""
import argparse
import base64
from contextlib import ExitStack
from datetime import datetime, timezone
import json
from pathlib import Path
import platform
import selectors
import subprocess
import sys
import tempfile
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'backend'))
from cryptography.fernet import Fernet
from task_backend.service import BackendService


def emit(value):
    print(json.dumps(value, ensure_ascii=False), flush=True)


def require(condition, message):
    if not condition:
        raise AssertionError(message)


def reply(marker):
    value = {'drafts': [{'tempId': '1', 'parentTempId': None,
                        'title': marker, 'notes': 'Synthetic recovery test',
                        'source': marker, 'due': {'mode': 'none'}}], 'notes': marker}
    return json.dumps({'choices': [{'finish_reason': 'stop', 'message': {
        'content': json.dumps(value)}}]}).encode()


def child(mode, directory):
    incoming = json.loads(sys.stdin.readline())
    cipher = Fernet(incoming['fixtureKey'].encode())
    with ExitStack() as patches:
        patches.enter_context(patch('task_backend.vault.encrypt', lambda value:
            base64.b64encode(cipher.encrypt(value.encode())).decode()))
        patches.enter_context(patch('task_backend.vault.decrypt', lambda value:
            cipher.decrypt(base64.b64decode(value)).decode()))
        patches.enter_context(patch('socket.create_connection', side_effect=AssertionError('Live network forbidden')))
        counts = {'smtp_connections': 0, 'model_requests': 0}

        def forbidden_request(*args):
            counts['model_requests'] += 1
            raise AssertionError('Unexpected model request during passive recovery')

        def forbidden_smtp(*args):
            counts['smtp_connections'] += 1
            raise AssertionError('Unexpected SMTP connection during recovery/replay')

        if mode == 'smtp_block':
            service = BackendService(directory, request=forbidden_request, worker=True)
            try:
                binding = service.call('mail-workspace:addAccount', 'imap')['id']
                service.call('mail-workspace:configureImap', binding, {
                    'email': 'sender@example.invalid', 'host': 'imap.example.invalid',
                    'port': 993, 'security': 'tls', 'password': 'synthetic-only-secret'})
                service.call('mail-compose:saveConfig', binding, {
                    'host': 'smtp.example.invalid', 'port': 465, 'security': 'ssl'})
                preview = service.call('mail-compose:prepare', {
                    'bindingId': binding, 'to': 'recipient@example.invalid',
                    'subject': 'Synthetic interrupted send', 'body': 'No real email is sent.'})

                class BlockingSMTP:
                    def login(self, *args): pass
                    def mail(self, *args): return 250, b'ok'
                    def rcpt(self, *args): return 250, b'ok'
                    def data(self, payload):
                        emit({'event': 'smtp_data_entered', 'receiptId': preview['id'],
                              'status': service.call('mail-compose:listRecent')[0]['status'],
                              'simulated_data_submissions': 1, 'payload_bytes': len(payload)})
                        # Parent deliberately terminates this process before any
                        # simulated final DATA acknowledgement can be returned.
                        sys.stdin.readline()
                        raise AssertionError('Blocking sender should have been terminated')
                    def close(self): pass

                service.compose.smtp_factory = lambda config: BlockingSMTP()
                service.call('mail-compose:send', preview['id'])
                raise AssertionError('Sender unexpectedly returned before process interruption')
            finally:
                service.close()
        elif mode == 'smtp_probe':
            service = BackendService(directory, request=forbidden_request, worker=True)
            try:
                service.compose.smtp_factory = forbidden_smtp
                before = service.call('mail-compose:listRecent')[0]['status']
                first = service.call('mail-compose:send', incoming['receiptId'])['status']
                second = service.call('mail-compose:send', incoming['receiptId'])['status']
                emit({'status': before, 'replay_statuses': [first, second],
                      'tasks': len(service.call('listTodos')), **counts})
            finally:
                service.close()
        elif mode == 'analysis_block':
            service = None
            job = None

            def controlled_request(url, body, headers, limit, timeout):
                counts['model_requests'] += 1
                if counts['model_requests'] == 1:
                    return reply('QA_FIRST')
                state = service.call('mail-operations:getJob', job['id'])
                emit({'event': 'second_model_request_entered', 'jobId': job['id'],
                      'state': state['state'], 'done': state['done'], 'total': state['total'],
                      'checkpointIds': state['analysisIds'], 'tasks': len(service.call('listTodos'))})
                sys.stdin.readline()
                raise AssertionError('Blocked analysis should have been killed')

            service = BackendService(directory, request=controlled_request, worker=True)
            try:
                target = {'baseUrl': 'https://fixture.invalid/v1', 'model': 'controlled-recovery'}
                service.call('saveSettings', {**target, 'apiKey': 'synthetic-model-secret'})
                ids = []
                for marker in ('QA_FIRST', 'QA_SECOND'):
                    local = service.mail.store.put('fixture-binding', 'fixture-identity', 'user@example.invalid', {
                        'id': marker, 'subject': marker, 'sender': 'client@example.invalid',
                        'receivedAt': '2026-11-20T00:00:00Z', 'body': marker,
                        'preview': marker, 'unread': True, 'webUrl': '', 'bodyStatus': 'complete'})
                    ids.append(local['localId'])
                job = service.call('mail-operations:startAnalysis', {
                    'messageIds': ids, 'instructions': 'Extract the synthetic task.', 'target': target})
                service.operations.run_next()
                raise AssertionError('Analysis unexpectedly returned before interruption')
            finally:
                service.close()
        elif mode == 'analysis_reopen':
            # worker=False is the normal main-service startup recovery path.
            service = BackendService(directory, request=forbidden_request, worker=False)
            try:
                service.compose.smtp_factory = forbidden_smtp
                job = service.call('mail-operations:getJob', incoming['jobId'])
                emit({'state': job['state'], 'done': job['done'], 'total': job['total'],
                      'checkpointIds': job['analysisIds'], 'tasks': len(service.call('listTodos')),
                      'worker_started': service.operations.process is not None, **counts})
            finally:
                service.close()
        elif mode == 'analysis_resume':
            requested_markers = []

            def remaining_request(url, body, headers, limit, timeout):
                counts['model_requests'] += 1
                content = json.loads(body)['messages'][1]['content']
                marker = 'QA_SECOND' if 'QA_SECOND' in content else 'QA_FIRST'
                requested_markers.append(marker)
                return reply(marker)

            # Explicitly execute the production worker engine in this harness
            # process; do not spawn the live worker entrypoint with real adapters.
            service = BackendService(directory, request=remaining_request, worker=True)
            try:
                service.compose.smtp_factory = forbidden_smtp
                service.call('mail-operations:resumeJob', incoming['jobId'])
                service.operations.run_next()
                job = service.call('mail-operations:getJob', incoming['jobId'])
                emit({'state': job['state'], 'done': job['done'], 'total': job['total'],
                      'checkpointIds': job['analysisIds'], 'tasks': len(service.call('listTodos')),
                      'requested_markers': requested_markers, **counts})
            finally:
                service.close()
        else:
            raise ValueError('Unknown harness child mode')


def command(mode, directory):
    return [sys.executable, '-B', '-u', str(Path(__file__).resolve()), '--child', mode, '--data-dir', str(directory)]


def start_blocked(mode, directory, payload):
    process = subprocess.Popen(command(mode, directory), stdin=subprocess.PIPE,
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        process.stdin.write(json.dumps(payload) + '\n')
        process.stdin.flush()
        with selectors.DefaultSelector() as selector:
            selector.register(process.stdout, selectors.EVENT_READ)
            require(selector.select(20), 'Child did not reach the synchronized boundary within 20 seconds')
        line = process.stdout.readline()
        if not line:
            raise AssertionError('Child exited before the synchronized boundary: ' + process.stderr.read()[-1000:])
        return process, json.loads(line)
    except BaseException:
        cleanup(process)
        raise


def once(mode, directory, payload):
    result = subprocess.run(command(mode, directory), input=json.dumps(payload) + '\n',
                            capture_output=True, text=True, timeout=20)
    require(result.returncode == 0, 'Probe child failed: ' + result.stderr[-1000:])
    return json.loads(result.stdout)


def cleanup(process):
    if process.poll() is None:
        process.kill()
    process.wait(timeout=5)
    for stream in (process.stdin, process.stdout, process.stderr):
        if stream:
            stream.close()


def smtp_case():
    with tempfile.TemporaryDirectory(prefix='tm-qa-smtp-') as folder:
        payload = {'fixtureKey': Fernet.generate_key().decode()}
        process, reached = start_blocked('smtp_block', Path(folder), payload)
        try:
            require(reached['event'] == 'smtp_data_entered' and reached['status'] == 'sending', 'Expected durable sending at DATA boundary')
            payload['receiptId'] = reached['receiptId']
            active = once('smtp_probe', Path(folder), payload)
            require(active['status'] == 'sending' and active['replay_statuses'] == ['sending', 'sending'], 'Live sender lock was incorrectly recovered or replayed')
            require(active['smtp_connections'] == 0, 'Duplicate send attempted while original process was alive')
            process.terminate()
            exit_code = process.wait(timeout=5)
            require(exit_code != 0, 'Blocked sender was not interrupted')
            recovered = once('smtp_probe', Path(folder), payload)
            require(recovered['status'] == 'uncertain', 'Interrupted SMTP receipt did not recover as uncertain')
            require(recovered['replay_statuses'] == ['uncertain', 'uncertain'], 'Replay changed interrupted SMTP outcome')
            require(recovered['smtp_connections'] == recovered['model_requests'] == recovered['tasks'] == 0, 'Recovery or replay caused unwanted side effects')
            return {'boundary': 'Simulated SMTP DATA received, final acknowledgement withheld',
                    'active_process_probe': active, 'interruption': 'Popen.terminate (SIGTERM on POSIX)',
                    'interrupted_child_exit_code': exit_code, 'fresh_process_probe': recovered,
                    'simulated_data_submissions_before_interruption': reached['simulated_data_submissions']}
        finally:
            cleanup(process)


def analysis_case():
    with tempfile.TemporaryDirectory(prefix='tm-qa-analysis-') as folder:
        payload = {'fixtureKey': Fernet.generate_key().decode()}
        process, reached = start_blocked('analysis_block', Path(folder), payload)
        try:
            require(reached['event'] == 'second_model_request_entered', 'Wrong analysis interruption boundary')
            require((reached['state'], reached['done'], reached['total'], reached['tasks']) == ('running', 1, 2, 0), 'First analysis checkpoint was not durable before interruption')
            payload['jobId'] = reached['jobId']
            process.kill()
            exit_code = process.wait(timeout=5)
            require(exit_code != 0, 'Analysis process was not killed')
            reopened = once('analysis_reopen', Path(folder), payload)
            require((reopened['state'], reopened['done'], reopened['total']) == ('paused', 1, 2), 'Main startup did not pause interrupted analysis at checkpoint')
            require(reopened['checkpointIds'] == reached['checkpointIds'], 'Completed checkpoint was lost during restart')
            require(not reopened['worker_started'], 'Passive recovery started a worker')
            require(reopened['model_requests'] == reopened['smtp_connections'] == reopened['tasks'] == 0, 'Passive recovery ran external work or committed tasks')
            resumed = once('analysis_resume', Path(folder), payload)
            require((resumed['state'], resumed['done'], resumed['total']) == ('completed', 2, 2), 'Explicit resume did not finish remaining work')
            require(resumed['requested_markers'] == ['QA_SECOND'] and resumed['model_requests'] == 1, 'Explicit resume reran completed first input')
            require(resumed['checkpointIds'][:1] == reached['checkpointIds'] and len(resumed['checkpointIds']) == 2, 'Resume did not preserve the original completed checkpoint')
            require(resumed['tasks'] == resumed['smtp_connections'] == 0, 'Analysis committed tasks or sent email without confirmation')
            for observation in (reopened, resumed):
                observation['completed_checkpoint_count'] = len(observation.pop('checkpointIds'))
            return {'boundary': 'First analysis completed, second controlled model call blocked',
                    'before_interrupt': {'state': reached['state'], 'done': 1, 'total': 2, 'tasks': 0},
                    'interruption': 'Popen.kill (SIGKILL on POSIX)', 'interrupted_child_exit_code': exit_code,
                    'fresh_main_process': reopened, 'explicit_resume_process': resumed,
                    'first_checkpoint_preserved': True}
        finally:
            cleanup(process)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--child')
    parser.add_argument('--data-dir', type=Path)
    parser.add_argument('--output', type=Path, default=ROOT / 'handover/qa-2026-11-20/recovery.json')
    args = parser.parse_args()
    if args.child:
        child(args.child, args.data_dir)
        return 0
    results = []
    for name, function, inputs, expected in [
        ('SMTP interruption: lock, uncertain outcome and replay guard', smtp_case,
         'One prepared synthetic message; block the controlled SMTP adapter after DATA submission.',
         'Live process remains sending; terminated sender recovers uncertain in a new process; repeated send makes no SMTP connection.'),
        ('Analysis interruption: pause, durable checkpoint and explicit resume', analysis_case,
         'Two synthetic local messages; first controlled response succeeds, second request blocks.',
         'Killed process reopens paused at 1/2 with no automatic requests/tasks; explicit resume processes only second message and finishes 2/2 without saving tasks.'),
    ]:
        record = {'name': name, 'input': inputs, 'expected': expected}
        try:
            record.update(status='PASS', observed=function())
        except Exception as error:
            record.update(status='FAIL', error=type(error).__name__ + ': ' + str(error))
        results.append(record)
        print(record['status'] + ' · ' + name, flush=True)
    report = {'recorded_at': datetime.now(timezone.utc).isoformat(),
              'command': '.venv/bin/python scripts/qa-recovery.py', 'platform': platform.platform(),
              'python': platform.python_version(),
              'scope': 'Real isolated OS child-process interruption/restart and production services/SQLite/file locks; SMTP and model responses are controlled substitutes, not real providers.',
              'safety': 'Temporary databases and ephemeral fixture encryption key; live network blocked; no real accounts, Keychain access, user data or external messages.',
              'limits': ['Does not test a live SMTP server, TLS, actual delivery or live model correctness.',
                         'Uses the production worker engine inside harness children, not the desktop UI or installed app lifecycle.',
                         'Does not simulate power loss, filesystem corruption or student handover.'],
              'results': results, 'passed': sum(r['status'] == 'PASS' for r in results),
              'failed': sum(r['status'] == 'FAIL' for r in results)}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    return int(bool(report['failed']))


if __name__ == '__main__':
    raise SystemExit(main())
