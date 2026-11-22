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
CRT_LICENSE_URL = 'https://visualstudio.microsoft.com/wp-content/uploads/2021/09/Visual-C-Runtime-2015-2022-License-1.docx'
CRT_LICENSE_SHA256 = 'f1e3d56ceb2ad68aae0711b910375009e651ac5530fa0760f0dea6e81e54fae1'


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


def pe_imports(path):
    """Read normal and delay imports from PE32/PE32+; no binary is executed."""
    data = path.read_bytes()
    if len(data) < 64 or data[:2] != b'MZ':
        return set()
    pe = struct.unpack_from('<I', data, 60)[0]
    if data[pe:pe+4] != b'PE\0\0':
        raise RuntimeError(f'Invalid PE signature: {path.name}')
    count = struct.unpack_from('<H', data, pe + 6)[0]
    size = struct.unpack_from('<H', data, pe + 20)[0]
    optional = pe + 24
    magic = struct.unpack_from('<H', data, optional)[0]
    directory = optional + (112 if magic == 0x20b else 96 if magic == 0x10b else 0)
    if directory == optional:
        raise RuntimeError(f'Unknown PE optional header: {path.name}')
    image_base = struct.unpack_from('<Q' if magic == 0x20b else '<I', data, optional + (24 if magic == 0x20b else 28))[0]
    sections = [struct.unpack_from('<IIII', data, optional + size + i * 40 + 8) for i in range(count)]

    def offset(rva):
        for virtual_size, virtual_address, raw_size, raw_pointer in sections:
            if virtual_address <= rva < virtual_address + max(virtual_size, raw_size):
                return raw_pointer + rva - virtual_address
        if rva < struct.unpack_from('<I', data, optional + 60)[0]:
            return rva
        raise RuntimeError(f'Invalid PE import address: {path.name}')

    imports = set()
    directories = struct.unpack_from('<I', data, directory - 4)[0]
    for index, stride, name_at in ((1, 20, 12), (13, 32, 4)):
        if directories <= index:
            continue
        rva, length = struct.unpack_from('<II', data, directory + index * 8)
        if not rva:
            continue
        begin = offset(rva)
        for pos in range(begin, min(len(data), begin + length), stride):
            row = data[pos:pos+stride]
            if not any(row):
                break
            name_rva = struct.unpack_from('<I', row, name_at)[0]
            if index == 13 and not struct.unpack_from('<I', row)[0] & 1:
                name_rva -= image_base
            start = offset(name_rva)
            end = data.index(b'\0', start)
            imports.add(data[start:end].decode('ascii').lower())
    return imports


def required_program_crt(source):
    evidence = {}
    for path in sorted((source / 'program').rglob('*')):
        if not path.is_file() or path.suffix.lower() not in ('.dll', '.exe', '.bin', '.com'):
            continue
        if any(part.lower().startswith('python') for part in path.relative_to(source / 'program').parts[:-1]):
            continue
        required = sorted(name for name in pe_imports(path) if CRT_NAME.fullmatch(name))
        if required:
            evidence[path.relative_to(source).as_posix()] = required
    return set().union(*map(set, evidence.values())) if evidence else set(), evidence


def visual_studio_crt():
    """Use only Microsoft's redistributable directory, never an OS DLL copy."""
    vswhere = Path(os.environ.get('ProgramFiles(x86)', 'C:/Program Files (x86)')) / 'Microsoft Visual Studio/Installer/vswhere.exe'
    installation = subprocess.check_output([str(vswhere), '-latest', '-products', '*', '-version', '[17,)',
        '-requires', 'Microsoft.VisualStudio.Component.VC.Tools.x86.x64', '-property', 'installationPath'],
        text=True, encoding='utf-8').strip()
    if not installation:
        raise RuntimeError('Visual Studio x64 C++ redistributable component is unavailable.')
    redist = Path(installation) / 'VC/Redist/MSVC'
    folders = sorted(redist.glob('*/x64/Microsoft.VC*.CRT'),
                     key=lambda p: tuple(int(n) for n in re.findall(r'\d+', p.parents[1].name)), reverse=True)
    for folder in folders:
        files = {p.name.lower(): p for p in folder.iterdir()
                 if p.is_file() and CRT_NAME.fullmatch(p.name) and pe_machine(p) == 0x8664}
        if REQUIRED_CRT <= files.keys():
            return files, dict(root=redist, version=folder.parents[1].name,
                              directory=folder.relative_to(redist).as_posix(),
                              licenseUrl=CRT_LICENSE_URL)
    raise RuntimeError('Official Visual Studio x64 VC/Redist directory has no complete CRT set.')


def copy_crt_license(target):
    relative = Path('third-party-notices/Microsoft-Visual-C-Runtime-2015-2022-License.docx')
    destination = target / relative
    with urllib.request.urlopen(CRT_LICENSE_URL, timeout=60) as response:
        if urlparse(response.url).hostname != 'visualstudio.microsoft.com':
            raise RuntimeError('Unexpected Microsoft CRT license download host.')
        data = response.read()
    if hashlib.sha256(data).hexdigest() != CRT_LICENSE_SHA256:
        raise RuntimeError('Microsoft CRT license document checksum mismatch.')
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(data)
    return relative.as_posix()


def bundle_msi_crt(extracted, source, target, *, required_crt=None, allow_visual_studio=False, inventory_path=None):
    """Keep the MSI's merge-module CRT alongside soffice, not in System32.

    Administrative MSI extraction places merge-module files outside the product
    subtree. The product also has a private Python runtime; its CRT is unrelated
    to soffice, so it is intentionally not a source for this app-local copy.
    """
    candidates = {}
    inventory = []
    for path in sorted(extracted.rglob('*')):
        match = CRT_NAME.search(path.name)
        if not path.is_file() or not match:
            continue
        machine = pe_machine(path)
        inventory.append(dict(path=path.relative_to(extracted).as_posix(), machine=hex(machine),
                              canonicalName=match.group().lower()))
        if path.is_relative_to(source) and path.parent != source / 'program':
            continue
        if machine != 0x8664:  # Merge modules may also include x86/ARM64.
            continue
        name = match.group().lower()
        if name in candidates and sha256(candidates[name]) != sha256(path):
            raise RuntimeError(f'Conflicting x64 CRT files in the verified MSI: {name}')
        candidates[name] = path
    (target / 'msi-crt-inventory.json').write_text(json.dumps(inventory, indent=2) + '\n', encoding='utf-8')
    if inventory_path is not None:
        inventory_path.parent.mkdir(parents=True, exist_ok=True)
        inventory_path.write_text(json.dumps(inventory, indent=2) + '\n', encoding='utf-8')
    print('MSI_CRT_INVENTORY', json.dumps(inventory), flush=True)
    required = REQUIRED_CRT if required_crt is None else set(required_crt)
    missing = required - candidates.keys()
    origin = extracted
    provider = dict(source='same SHA-256-verified LibreOffice MSI administrative image')
    extra_licenses = []
    if missing and allow_visual_studio:
        candidates, vs = visual_studio_crt()
        origin = vs.pop('root')
        provider = dict(source='Microsoft Visual Studio official VC/Redist/MSVC x64 directory', **vs)
        extra_licenses.append(copy_crt_license(target))
        missing = required - candidates.keys()
    if missing:
        raise RuntimeError('Runtime lacks app-local x64 CRT: ' + ', '.join(sorted(missing)))

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
    if not licenses and not extra_licenses and candidates:
        raise RuntimeError('The verified MSI is missing its Microsoft Visual C++ license notice.')
    licenses.extend(extra_licenses)

    manifest = []
    for name, path in sorted(candidates.items()):
        destination = target / 'program' / name
        if destination.exists() and sha256(destination) != sha256(path):
            raise RuntimeError(f'Conflicting app-local CRT file: {name}')
        if not destination.exists():
            shutil.copy2(path, destination)
        if pe_machine(destination) != 0x8664 or sha256(destination) != sha256(path):
            raise RuntimeError(f'App-local CRT verification failed: {name}')
        manifest.append(dict(file='program/' + name, source=path.relative_to(origin).as_posix(),
                             sha256=sha256(destination), architecture='x64'))
    return dict(**provider, files=manifest, requiredByImports=sorted(required), msiInventory=inventory,
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
        required, evidence = required_program_crt(source)
        diagnostics = root / 'handover/windows-release'
        diagnostics.mkdir(parents=True, exist_ok=True)
        (diagnostics / 'libreoffice-crt-imports.json').write_text(json.dumps(evidence, indent=2) + '\n', encoding='utf-8')
        print('LIBREOFFICE_CRT_IMPORTS', json.dumps(evidence), flush=True)
        crt = bundle_msi_crt(extracted, source, target, required_crt=required, allow_visual_studio=True,
                             inventory_path=diagnostics / 'libreoffice-msi-crt-inventory.json')
        crt['importEvidence'] = evidence
        (diagnostics / 'libreoffice-crt-bundle.json').write_text(json.dumps(crt, indent=2) + '\n', encoding='utf-8')
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
