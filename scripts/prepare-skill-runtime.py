"""Explicit one-time setup: official pinned base digest + fixed Python libraries."""
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'backend'))
from task_backend.skill_execution import (
    IMAGE, RUNTIME, _docker, _cli_env, _process_options, _local_daemon,
    runtime_hash, get_status,
)

def main():
    docker = _docker()
    if not docker: raise SystemExit('Install/start Docker Desktop first.')
    try:
        endpoint = _local_daemon(docker)
    except (ValueError, OSError, subprocess.TimeoutExpired) as exc:
        raise SystemExit('Skill runtime setup stopped: ' + str(exc)) from None
    # Pin both mutations to the checked daemon, regardless of shell overrides
    # or a context change while the image is being prepared.
    prefix = [docker, '--host', endpoint]
    options = dict(check=True, env=_cli_env(), **_process_options())
    # Python 3.12 slim-bookworm multi-platform manifest, pinned at implementation.
    base = 'python@sha256:935c3c5b12357a7e10caa9c1e6ca5692344d97cf229832ef584be2cec9fc03df'
    subprocess.run(prefix + ['pull', base], **options)
    subprocess.run(prefix + ['build', '--build-arg', 'PYTHON_BASE='+base, '--label',
                    'org.taskmanager.skill-runtime='+runtime_hash(), '-t', IMAGE, str(RUNTIME)], **options)
    result = get_status(endpoint=endpoint)
    if not result['available']: raise SystemExit(result['reason'])
    print('SKILL_RUNTIME_READY', result['imageId'], result['runtimeHash'])

if __name__ == '__main__': main()
