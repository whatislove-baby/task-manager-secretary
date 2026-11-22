"""Bundle LibreOffice Windows from its verified official administrative image.

Source and SHA-256:
https://download.documentfoundation.org/libreoffice/stable/26.8.3/win/x86_64/LibreOffice_26.8.3_Win_x86-64.msi.mirrorlist
"""
from pathlib import Path
import hashlib
import json
import os
import re
import shutil
import struct
import subprocess
import sys
import tempfile
import urllib.request
from urllib.parse import urlparse

VERSION = '26.8.3'
FILENAME = f'LibreOffice_{VERSION}_Win_x86-64.msi'
URL = f'https://download.documentfoundation.org/libreoffice/stable/{VERSION}/win/x86_64/{FILENAME}'
SHA256 = '0e6918c273eabbdbbd800ee0dacbbf130a14c13c2e4cec861b06dd59e015bf94'
CRT_NAME = re.compile(r'(?:msvcp140|vcruntime140|concrt140|vccorlib140)(?:_[a-z0-9_]+)?\.dll', re.I)
REQUIRED_CRT = {'msvcp140.dll', 'vcruntime140.dll', 'vcruntime140_1.dll'}


def sha256(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def pe_machine(path):
    """Read only the documented DOS/PE headers; never execute an installer DLL."""
    with path.open('rb') as stream:
        header = stream.read(64)
        if len(header) != 64 or header[:2] != b'MZ':
            raise RuntimeError(f'Runtime is not a PE binary: {path.name}')
        stream.seek(struct.unpack_from('<I', header, 60)[0])
        signature = stream.read(6)
    if len(signature) != 6 or signature[:4] != b'PE\0\0':
        raise RuntimeError(f'Runtime has an invalid PE header: {path.name}')
    return struct.unpack_from('<H', signature, 4)[0]


def bundle_msi_crt(extracted, source, target):
    """Keep the MSI's merge-module CRT alongside soffice, not in System32.

    Administrative MSI extraction places merge-module files outside the product
    subtree. The product also has a private Python runtime; its CRT is unrelated
    to soffice, so it is intentionally not a source for this app-local copy.
    """
    candidates = {}
    for path in sorted(extracted.rglob('*')):
        if not path.is_file() or not CRT_NAME.fullmatch(path.name):
            continue
        if path.is_relative_to(source) and path.parent != source / 'program':
            continue
        if pe_machine(path) != 0x8664:  # Merge modules may also include x86/ARM64.
            continue
        name = path.name.lower()
        if name in candidates and sha256(candidates[name]) != sha256(path):
            raise RuntimeError(f'Conflicting x64 CRT files in the verified MSI: {name}')
        candidates[name] = path
    missing = REQUIRED_CRT - candidates.keys()
    if missing:
        raise RuntimeError('Verified MSI lacks app-local x64 CRT: ' + ', '.join(sorted(missing)))

    licenses = []
    for path in sorted(extracted.rglob('*')):
        if not path.is_file() or not any(word in path.name.lower() for word in ('license', 'copying', 'notice', 'eula')):
            continue
        if path.stat().st_size > 5 * 1024 * 1024:
            continue
        content = path.read_text(encoding='utf-8', errors='replace').lower()
        if 'microsoft software license terms' not in content or not any(word in content for word in ('visual c++', 'c++ redistributable')):
            continue
        relative = path.relative_to(source) if path.is_relative_to(source) else Path('third-party-notices/msi') / path.relative_to(extracted)
        destination = target / relative
        if destination.exists() and sha256(destination) != sha256(path):
            raise RuntimeError('Conflicting Microsoft license file: ' + str(relative))
        if not destination.exists():
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, destination)
        licenses.append(relative.as_posix())
    if not licenses:
        raise RuntimeError('The verified MSI is missing its Microsoft Visual C++ license notice.')

    manifest = []
    for name, path in sorted(candidates.items()):
        destination = target / 'program' / name
        if destination.exists() and sha256(destination) != sha256(path):
            raise RuntimeError(f'Conflicting app-local CRT file: {name}')
        if not destination.exists():
            shutil.copy2(path, destination)
        if pe_machine(destination) != 0x8664 or sha256(destination) != sha256(path):
            raise RuntimeError(f'App-local CRT verification failed: {name}')
        manifest.append(dict(file='program/' + name, source=path.relative_to(extracted).as_posix(),
                             sha256=sha256(destination), architecture='x64'))
    return dict(source='same SHA-256-verified LibreOffice MSI administrative image', files=manifest,
                licenses=licenses, universalCRT='Windows 10/11 OS component; no system files copied')


def main():
    if sys.platform != 'win32' or sys.maxsize < 2**32:
        raise RuntimeError('The document runtime must be prepared on Windows x64.')
    root = Path(__file__).resolve().parents[1]
    cache = root / '.cache' / 'libreoffice-windows'
    cache.mkdir(parents=True, exist_ok=True)
    msi = cache / FILENAME
    if not msi.is_file() or sha256(msi) != SHA256:
        partial = cache / (FILENAME + '.partial')
        with urllib.request.urlopen(URL, timeout=120) as response:
            if urlparse(response.url).scheme != 'https':
                raise RuntimeError('LibreOffice download redirected to non-HTTPS transport.')
            with partial.open('wb') as output:
                shutil.copyfileobj(response, output, length=1024 * 1024)
        if sha256(partial) != SHA256:
            raise RuntimeError('LibreOffice MSI checksum mismatch; nothing was executed.')
        os.replace(partial, msi)
    target = root / '.libreoffice-runtime-windows'
    if target.is_symlink():
        raise RuntimeError('Refusing a linked document runtime output.')
    with tempfile.TemporaryDirectory(prefix='tm-lo-admin-', dir=cache) as work:
        work = Path(work)
        extracted = work / 'extracted'
        extracted.mkdir()
        log = cache / 'administrative-extraction.log'
        # /a extracts files without registering the application or associations.
        result = subprocess.run(['msiexec.exe', '/a', str(msi), '/qn', '/norestart',
                                 f'TARGETDIR={extracted}', '/l*v', str(log)],
                                timeout=600, check=False)
        if result.returncode not in (0, 3010):
            raise RuntimeError(f'LibreOffice administrative extraction failed ({result.returncode}); see {log.name}.')
        executables = list(extracted.rglob('program/soffice.exe'))
        if len(executables) != 1:
            raise RuntimeError('Could not identify the extracted LibreOffice runtime.')
        source = executables[0].parent.parent
        if not (source / 'share').is_dir() or not (source / 'program' / 'soffice.bin').is_file():
            raise RuntimeError('Incomplete LibreOffice administrative image.')
        if target.exists():
            shutil.rmtree(target)
        shutil.copytree(source, target)
        crt = bundle_msi_crt(extracted, source, target)
    command = target / 'program' / 'soffice.com'
    if not command.is_file():
        command = target / 'program' / 'soffice.exe'
    with tempfile.TemporaryDirectory(prefix='tm-lo-version-') as profile:
        probe = subprocess.run([str(command), '-env:UserInstallation=' + Path(profile).as_uri(),
                                '--headless', '--version'], capture_output=True, text=True,
                               encoding='utf-8', errors='replace', timeout=60, check=True)
    if 'LibreOffice' not in probe.stdout or VERSION not in probe.stdout:
        raise RuntimeError('Bundled LibreOffice did not report the expected version.')
    licenses = [str(p.relative_to(target)).replace('\\', '/') for p in target.rglob('*')
                if p.is_file() and any(word in p.name.lower() for word in ('license', 'copying', 'notice'))]
    if not licenses:
        raise RuntimeError('Vendor license notices are missing from the extracted runtime.')
    manifest = dict(product='LibreOffice', version=probe.stdout.strip(), architecture='x64',
                    url=URL, sha256=SHA256, licenses=licenses,
                    upstream='https://www.libreoffice.org/',
                    visualCppRuntime=crt,
                    extraction='Windows Installer administrative image; original runtime files retained')
    (target / 'runtime-manifest.json').write_text(json.dumps(manifest, indent=2) + '\n', encoding='utf-8')
    print('WINDOWS_DOCUMENT_RENDERER_READY', probe.stdout.strip())


if __name__ == '__main__':
    main()
