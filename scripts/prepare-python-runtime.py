"""Prepare the relocatable Python 3.12 runtime family also used by the Mac app.

uv verifies Astral's python-build-standalone download. Nothing is installed into
PATH/the registry; the final app does not need uv or a system Python.
"""
from pathlib import Path
import hashlib
import json
import os
import shutil
import subprocess
import sys
from requirements_lock import read_lock

VERSION = '3.12.13'
DISTRIBUTION = f'cpython-{VERSION}-windows-x86_64-none'


def main():
    if sys.platform != 'win32' or sys.version_info[:2] != (3, 12) or sys.maxsize < 2**32:
        raise RuntimeError('Prepare the Windows runtime with a Windows x64 Python 3.12 project environment.')
    root = Path(__file__).resolve().parents[1]
    cache = root / '.cache' / 'uv'
    installs = root / '.cache' / 'managed-python'
    target = root / '.python-runtime'
    staging = root / '.python-runtime-staging'
    uv = os.environ.get('TM_UV', 'uv')
    common = [uv, '--cache-dir', str(cache), '--no-config']

    def run(args):
        subprocess.run(args, cwd=root, check=True)

    run(common + ['python', 'install', '--install-dir', str(installs),
                  '--no-bin', '--no-registry', DISTRIBUTION])
    found = subprocess.check_output(common + ['python', 'find', '--managed-python', DISTRIBUTION],
                                    cwd=root, text=True,
                                    env={**os.environ, 'UV_PYTHON_INSTALL_DIR': str(installs)}).strip()
    source = Path(found).resolve().parent
    if not source.is_relative_to(installs.resolve()):
        raise RuntimeError('Managed Python resolved outside the project install directory.')
    for required in ('python.exe', 'pythonw.exe', 'vcruntime140.dll', 'vcruntime140_1.dll', 'LICENSE.txt'):
        if not (source / required).is_file():
            raise RuntimeError(f'Managed Windows Python is missing {required}.')
    for directory in (target, staging):
        if directory.is_symlink():
            raise RuntimeError('Refusing a linked runtime output directory.')
    if staging.exists():
        shutil.rmtree(staging)
    shutil.copytree(source, staging)
    python = staging / 'python.exe'
    runtime = json.loads(subprocess.check_output(
        [str(python), '-B', '-I', '-c',
         'import ctypes,json,pathlib,sys; '
         '[ctypes.WinDLL(str(pathlib.Path(sys.executable).parent/n)) '
         'for n in ("vcruntime140.dll","vcruntime140_1.dll")]; '
         'print(json.dumps({"version": list(sys.version_info[:3]), '
         '"description": sys.version, "bits": 64 if sys.maxsize > 2**32 else 32}))'],
        cwd=root, text=True,
    ))
    if runtime['version'] != [int(part) for part in VERSION.split('.')] or runtime['bits'] != 64:
        raise RuntimeError('The copied runtime does not match the pinned Python version and architecture.')
    requirements = root / 'backend' / 'requirements.txt'
    # Native Windows wheels go directly into the copied runtime, not a copied
    # venv that can contain absolute paths or wrong-platform DLLs.
    run(common + ['pip', 'install', '--python', str(python), '--target',
                  str(staging / 'Lib' / 'site-packages'), '--only-binary', ':all:',
                  '--no-deps', '-r', str(requirements)])
    pins, locks = read_lock(requirements)
    manifest = dict(version=VERSION, architecture='x64',
                    distribution='astral-sh/python-build-standalone',
                    distributionKey=DISTRIBUTION,
                    runtimeDescription=runtime['description'],
                    visualCppRuntime={name: hashlib.sha256((staging / name).read_bytes()).hexdigest()
                                      for name in ('vcruntime140.dll', 'vcruntime140_1.dll')},
                    requirementsSha256={p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in locks},
                    dependencies=dict(pins))
    # BUILD is not part of the install-only distribution's guaranteed layout.
    # Record it if supplied; provenance metadata must not break a valid runtime.
    try:
        release = (source / 'BUILD').read_text(encoding='utf-8').strip()
    except (OSError, UnicodeError):
        release = ''
    if release:
        manifest['release'] = release
    (staging / 'runtime-manifest.json').write_text(json.dumps(manifest, indent=2) + '\n', encoding='utf-8')
    run([str(python), '-B', '-I', str(root / 'scripts' / 'verify-runtime-deps.py'),
         '--requirements', str(requirements), '--smoke'])
    # Keep the previous runtime until the candidate passed its Agent smoke.
    if target.exists():
        shutil.rmtree(target)
    staging.rename(target)
    run([str(target / 'python.exe'), '-B', '-I', '-c',
         'import sys,sqlite3,ssl,ctypes,pypdf,pypdfium2,pandas,docx,langchain_openrouter,deepagents; '
         'assert sys.version_info[:3] == (3,12,13); print("WINDOWS_RUNTIME_READY",sys.version)'])


if __name__ == '__main__':
    main()
