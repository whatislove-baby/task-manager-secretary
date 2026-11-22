"""Install pinned compatible wheels from PyPI with published SHA256 verification."""
from pathlib import Path
import hashlib,json,shutil,subprocess,sys,urllib.request
from requirements_lock import read_lock
root=Path(__file__).resolve().parents[1]
cache=root/'.cache/wheels';cache.mkdir(parents=True,exist_ok=True)
opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
try:
    from packaging.tags import sys_tags
    from packaging.utils import parse_wheel_filename
except ImportError:
    from pip._vendor.packaging.tags import sys_tags
    from pip._vendor.packaging.utils import parse_wheel_filename
compatible={tag:index for index,tag in enumerate(sys_tags())}
paths=[]
requirements=root/'backend'/('requirements-mac.txt' if sys.platform=='darwin' else 'requirements.txt')
pins,_=read_lock(requirements)
for name,version in pins:
    with opener.open(f'https://pypi.org/pypi/{name}/{version}/json',timeout=20) as response: manifest=json.load(response)
    candidates=[]
    for value in manifest['urls']:
        if not value['filename'].endswith('.whl'): continue
        tags=parse_wheel_filename(value['filename'])[3]
        ranks=[compatible[tag] for tag in tags if tag in compatible]
        if ranks:candidates.append((min(ranks),value))
    if not candidates:raise RuntimeError('No compatible wheel: '+name+' '+version)
    item=min(candidates,key=lambda pair:pair[0])[1]
    target=cache/item['filename']
    if not target.exists() or hashlib.sha256(target.read_bytes()).hexdigest()!=item['digests']['sha256']:
        with opener.open(item['url'],timeout=30) as response: data=response.read()
        if hashlib.sha256(data).hexdigest()!=item['digests']['sha256']: raise RuntimeError('Dependency hash mismatch')
        target.write_bytes(data)
    paths.append(str(target))
python=root/'.venv'/('Scripts/python.exe' if sys.platform=='win32' else 'bin/python')
has_pip=subprocess.run([str(python),'-c','import pip'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL).returncode==0
if has_pip:
    subprocess.run([str(python),'-m','pip','install','--no-index','--no-deps',*paths],cwd=root,check=True)
    subprocess.run([str(python),'-m','pip','check'],cwd=root,check=True)
else:
    uv=shutil.which('uv')
    if not uv:raise RuntimeError('Project environment has no pip. Install uv or run .venv Python with -m ensurepip first.')
    base=[uv,'--cache-dir',str(root/'.cache/uv'),'--no-config','pip']
    subprocess.run([*base,'install','--python',str(python),'--no-index','--no-deps',*paths],cwd=root,check=True)
    subprocess.run([*base,'check','--python',str(python)],cwd=root,check=True)
subprocess.run([str(python),str(root/'scripts/verify-runtime-deps.py'),'--requirements',str(requirements),'--smoke'],cwd=root,check=True)
