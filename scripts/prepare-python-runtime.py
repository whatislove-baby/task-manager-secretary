"""Build a relocatable Python runtime from the verified official Windows distribution."""
from pathlib import Path
import hashlib,importlib.metadata,json,shutil,subprocess,sys,urllib.request,zipfile
from requirements_lock import read_lock
if sys.platform!='win32' or sys.version_info[:2]!=(3,14):
    raise RuntimeError('Windows runtime must be prepared with the Windows Python 3.14 project environment; use prepare-python-runtime-mac.cjs on macOS.')
root=Path(__file__).resolve().parents[1];target=root/'.python-runtime';target.mkdir(exist_ok=True)
url='https://www.python.org/ftp/python/3.14.10/python-3.14.10-embed-amd64.zip'
expected='90905e64a840557d8c1bc4f5b74db3d7ee85e3ffb217611cac1c7813acea4159'
cache=root/'.cache/python-3.14.10-embed-amd64.zip';cache.parent.mkdir(exist_ok=True)
if not cache.exists() or hashlib.sha256(cache.read_bytes()).hexdigest()!=expected:
    with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(url,timeout=60) as response:data=response.read()
    if hashlib.sha256(data).hexdigest()!=expected:raise RuntimeError('Official Python runtime checksum mismatch')
    cache.write_bytes(data)
with zipfile.ZipFile(cache) as archive:
    for item in archive.infolist():
        file=(target/item.filename).resolve()
        if not file.is_relative_to(target.resolve()):raise RuntimeError('Unsafe runtime archive path')
    archive.extractall(target)
(target/'python314._pth').write_text('python314.zip\n.\nLib/site-packages\n../backend\nimport site\n',encoding='utf-8')
site=root/'.venv/Lib/site-packages';installed=target/'Lib/site-packages'
if installed.exists():shutil.rmtree(installed)
installed.mkdir(parents=True,exist_ok=True)
versions={}
requirements=root/'backend/requirements.txt';pins,lock_files=read_lock(requirements)
for name,version in pins:
    distribution=importlib.metadata.distribution(name)
    if distribution.version!=version:raise RuntimeError('Dependency version mismatch: '+name)
    versions[name]=version
    for file in distribution.files or []:
        source=Path(distribution.locate_file(file)).resolve()
        if source.is_file() and source.is_relative_to(site.resolve()):
            destination=installed/source.relative_to(site.resolve());destination.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(source,destination)
(target/'runtime-manifest.json').write_text(json.dumps(dict(version='3.14.10',url=url,sha256=expected,dependencies=versions,requirementsSha256={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in lock_files}),indent=2),encoding='utf-8')
result=subprocess.run([str(target/'python.exe'),'-c','import pypdf,reportlab,PIL,fontTools;import sqlite3,ssl,ctypes,icalendar,imapclient,tzlocal,dateutil;import sys;print(sys.version)'],text=True)
if result.returncode:raise SystemExit(result.returncode)
raise SystemExit(subprocess.call([str(target/'python.exe'),'-B',str(root/'scripts/verify-runtime-deps.py'),'--requirements',str(requirements),'--smoke']))
