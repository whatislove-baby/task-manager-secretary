"""Bundle LibreOffice Windows from its verified official administrative image.

Source and SHA-256:
https://download.documentfoundation.org/libreoffice/stable/26.8.3/win/x86_64/LibreOffice_26.8.3_Win_x86-64.msi.mirrorlist
"""
from pathlib import Path
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import urllib.request
from urllib.parse import urlparse

VERSION = '26.8.3'
FILENAME = f'LibreOffice_{VERSION}_Win_x86-64.msi'
URL = f'https://download.documentfoundation.org/libreoffice/stable/{VERSION}/win/x86_64/{FILENAME}'
SHA256 = '0e6918c273eabbdbbd800ee0dacbbf130a14c13c2e4cec861b06dd59e015bf94'


def sha256(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


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
                    extraction='Windows Installer administrative image; original runtime files retained')
    (target / 'runtime-manifest.json').write_text(json.dumps(manifest, indent=2) + '\n', encoding='utf-8')
    print('WINDOWS_DOCUMENT_RENDERER_READY', probe.stdout.strip())


if __name__ == '__main__':
    main()
