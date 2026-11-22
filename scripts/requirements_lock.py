"""Read this project's complete, recursively included exact-version lock files."""
from pathlib import Path
import re


def normalized(name):
    return re.sub(r'[-_.]+', '-', name).lower()


def read_lock(filename):
    """Return ordered (name, version) pins and all files used, rejecting drift."""
    pins = {}
    files = []
    visiting = set()

    def visit(path):
        path = Path(path).resolve()
        if path in visiting:
            raise ValueError('Cyclic requirements include: ' + path.name)
        if path in files:
            return
        visiting.add(path)
        files.append(path)
        for number, line in enumerate(path.read_text(encoding='utf-8-sig').splitlines(), 1):
            line = line.strip()
            if not line or line.startswith('#'):
                continue
            if line.startswith('-r '):
                visit(path.parent / line[3:].strip())
                continue
            match = re.fullmatch(r'([A-Za-z0-9][A-Za-z0-9_.-]*)==([A-Za-z0-9][A-Za-z0-9_.+!-]*)', line)
            if not match:
                raise ValueError(f'{path.name}:{number}: expected an exact version pin or -r include')
            name, version = match.groups()
            key = normalized(name)
            if key in pins and pins[key][1] != version:
                raise ValueError('Conflicting dependency versions: ' + name)
            pins[key] = (name, version)
        visiting.remove(path)

    visit(filename)
    return list(pins.values()), files
