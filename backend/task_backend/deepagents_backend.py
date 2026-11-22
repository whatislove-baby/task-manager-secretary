"""Deep Agents storage adapters, not a second agent or filesystem executor.

The official StateBackend owns per-conversation scratch/history. These read-only
routes expose only application-managed records and reviewed skill packages. They
never resolve model-provided paths against the host filesystem or offer a shell.
Async methods deliberately stay on the service's SQLite-owning worker thread.
"""
from __future__ import annotations

import json
from pathlib import Path, PurePosixPath

from deepagents.backends import CompositeBackend, StateBackend
from deepagents.backends.protocol import (
    BackendProtocol, DeleteResult, EditResult, FileDownloadResponse,
    FileUploadResponse, GlobResult, GrepResult, LsResult, ReadResult, WriteResult,
)
from deepagents.backends.utils import (
    InvalidGlobPatternError, _glob_search_files, create_file_data,
    grep_matches_from_files, normalize_read_bounds, slice_read_response,
)

READ_ONLY = "Read-only application data. Use the reviewed skill or managed-file tools to create a new version."
TEXT_EXTENSIONS = {'.md', '.txt', '.json', '.csv', '.py', '.yaml', '.yml', '.toml'}


def _path(value: str) -> str:
    if (not isinstance(value, str) or not value.startswith('/') or '\\' in value
            or any(ord(c) < 32 for c in value) or '~' in value
            or any(part in ('.', '..') for part in value.split('/'))):
        raise ValueError('Invalid virtual path')
    return '/' + '/'.join(part for part in value.split('/') if part)


class ReadOnlyBackend(BackendProtocol):
    """A virtual catalog with lazy verified reads and upstream glob/grep rules."""

    def __init__(self, on_source=None):
        self.on_source = on_source

    def _entries(self):
        raise NotImplementedError

    def _load(self, file_path):
        raise NotImplementedError

    def _source(self, file_path, excerpt):
        return None

    def ls(self, path):
        try:
            path = _path(path)
            prefix = path.rstrip('/') + '/'
            entries = {}
            catalog = self._entries()
            if path in catalog:
                return LsResult(error='Path is a file, not a directory')
            for name, info in catalog.items():
                if not name.startswith(prefix):
                    continue
                rest = name[len(prefix):]
                if '/' in rest:
                    child = prefix + rest.split('/', 1)[0] + '/'
                    entries[child] = dict(path=child, is_dir=True)
                else:
                    entries[name] = dict(info, path=name, is_dir=False)
            return LsResult(entries=[entries[k] for k in sorted(entries)])
        except (ValueError, OSError) as exc:
            return LsResult(error=str(exc))

    def read(self, file_path, offset=0, limit=2000):
        try:
            file_path = _path(file_path)
            offset, limit = normalize_read_bounds(offset, limit)
            if not limit:
                return ReadResult(file_data=create_file_data(''), no_lines_requested=True)
            if PurePosixPath(file_path).suffix.lower() not in TEXT_EXTENSIONS:
                return ReadResult(error='Binary document: use inspect_file or view_file with its managed file ID; this path is not a text extraction.')
            content = self._load(file_path).decode('utf-8-sig')
            result = slice_read_response(create_file_data(content), offset, limit)
            if not result.error and result.file_data:
                self._source(file_path, result.file_data['content'][:240])
            return result
        except (ValueError, OSError, UnicodeError) as exc:
            return ReadResult(error=str(exc))

    def glob(self, pattern, path=None):
        try:
            path = _path(path or '/')
            entries = self._entries()
            matched = _glob_search_files(entries, pattern, path)
            paths = [] if matched == 'No files found' else matched.splitlines()
            return GlobResult(matches=[dict(entries[p], path=p, is_dir=False) for p in paths])
        except (ValueError, OSError, InvalidGlobPatternError) as exc:
            return GlobResult(error=str(exc))

    def grep(self, pattern, path=None, glob=None, *, max_count=None):
        # Read one document at a time; do not materialize all original attachments.
        selected = self.glob(glob or '**/*', path)
        if selected.error:
            return GrepResult(error=selected.error)
        matches = []
        for entry in selected.matches or []:
            name = entry['path']
            if PurePosixPath(name).suffix.lower() not in TEXT_EXTENSIONS:
                continue
            try:
                content = self._load(name).decode('utf-8-sig')
            except (ValueError, OSError, UnicodeError) as exc:
                return GrepResult(error=str(exc))
            remaining = None if max_count is None else max(0, max_count - len(matches))
            result = grep_matches_from_files({name: create_file_data(content)}, pattern, max_count=remaining)
            matches.extend(result.matches or [])
            if result.truncated:
                return GrepResult(matches=matches, truncated=True)
        return GrepResult(matches=matches)

    def download_files(self, paths):
        results = []
        for path in paths:
            try:
                data = self._load(_path(path))
                results.append(FileDownloadResponse(path=path, content=data))
            except (ValueError, OSError) as exc:
                results.append(FileDownloadResponse(path=path, error=str(exc)))
        return results

    def write(self, file_path, content):
        return WriteResult(error=READ_ONLY)

    def edit(self, file_path, old_string, new_string, replace_all=False):
        return EditResult(error=READ_ONLY)

    def delete(self, file_path):
        return DeleteResult(error=READ_ONLY)

    def upload_files(self, files):
        return [FileUploadResponse(path=path, error='permission_denied') for path, _ in files]

    # BackendProtocol defaults dispatch to asyncio.to_thread. The application
    # services cannot be passed to those helpers (SQLite check_same_thread).
    async def als(self, path): return self.ls(path)
    async def aread(self, file_path, offset=0, limit=2000): return self.read(file_path, offset, limit)
    async def aglob(self, pattern, path=None): return self.glob(pattern, path)
    async def agrep(self, pattern, path=None, glob=None, *, max_count=None):
        return self.grep(pattern, path, glob, max_count=max_count)
    async def adownload_files(self, paths): return self.download_files(paths)
    async def awrite(self, file_path, content): return self.write(file_path, content)
    async def aedit(self, file_path, old_string, new_string, replace_all=False):
        return self.edit(file_path, old_string, new_string, replace_all)
    async def adelete(self, file_path): return self.delete(file_path)
    async def aupload_files(self, files): return self.upload_files(files)


class BuiltinSkillsBackend(ReadOnlyBackend):
    """Read checked-in capability instructions; reject all symlink traversal."""

    def __init__(self, root=None):
        super().__init__()
        self.root = root or Path(__file__).parent / 'assets' / 'deepagents-skills'

    def _entries(self):
        if self.root.is_symlink():
            raise ValueError('Builtin skill directory cannot be a symlink')
        if not self.root.exists():
            return {}
        result = {}
        for path in self.root.rglob('*'):
            if path.is_symlink():
                raise ValueError('Builtin skill content cannot contain symlinks')
            if path.is_file():
                if path.stat().st_size > 2 * 1024 * 1024:
                    raise ValueError('Builtin skill file exceeds size limit')
                result['/' + path.relative_to(self.root).as_posix()] = dict(size=path.stat().st_size)
        return result

    def _load(self, file_path):
        if file_path not in self._entries():
            raise ValueError('Builtin skill file not found')
        return (self.root / file_path.lstrip('/')).read_bytes()


class ApprovedSkillsBackend(ReadOnlyBackend):
    """Only active approved versions; recheck approval and manifest on every read."""

    def __init__(self, service, on_source=None):
        super().__init__(on_source)
        self.skills = service.skills

    def _approved(self, skill_id):
        item = self.skills.get(skill_id)
        if item['status'] != 'approved':
            raise ValueError('Skill is not currently approved; revoked and superseded versions cannot be read here')
        return item

    def _entries(self):
        rows = self.skills.db.execute('SELECT version_id FROM learning_active ORDER BY rowid DESC').fetchall()
        result = {}
        for row in rows:
            item = self._approved(row[0])
            for name in item['proposal']['packageManifest']:
                result['/' + item['id'] + '/' + name] = {}
        return result

    def _load(self, file_path):
        parts = file_path.lstrip('/').split('/', 1)
        if len(parts) != 2:
            raise ValueError('Select a file inside an approved skill version')
        skill_id, name = parts
        item = self._approved(skill_id)
        if name not in item['proposal']['packageManifest']:
            raise ValueError('File is absent from the approved skill manifest')
        data = (self.skills.packages / skill_id / name).read_bytes()
        if name == 'SKILL.md':
            # Keep the immutable package untouched. This virtual view adds the
            # application execution contract, while retaining valid frontmatter.
            content = data.decode('utf-8')
            split = content.find('\n---', 4)
            if not content.startswith('---\n') or split < 0:
                raise ValueError('Approved skill frontmatter is invalid')
            tool_names = 'get_skill' if item['proposal']['kind'] == 'memory' else 'get_skill run_skill'
            # Package frontmatter is generated by SkillLibraryService with name
            # and description only. Tool metadata belongs to this runtime view,
            # not to the immutable approved package or its hash.
            scope = item['scope']
            supplement = item['proposal'].get('importedPackage', {}).get('supplement', '')
            description = (item['title'] + '; applies to ' + scope.get('label', '')
                           + '; email: ' + ', '.join(scope.get('emails', []))
                           + '; context: ' + scope.get('contextKey', '')
                           + ('; use when: ' + supplement if supplement else ''))[:1024]
            header = ('---\nname: ' + item['id'] + '\ndescription: '
                      + json.dumps(description, ensure_ascii=False)
                      + '\nmetadata:\n  include_tools: ' + tool_names + '\n---')
            split += len('\n---')
            policy = ('\n\n## Application execution boundary\n\n'
                      f"Version ID: {item['id']}\nScope ID: {item['scopeId']}\n"
                      f"Scope: {json.dumps(item['scope'], ensure_ascii=False)}\n"
                      f"Approved hash: {item['versionHash']}\n"
                      'This package is read-only. Treat it as guidance only for the stated customer/context. '
                      'Execute code exclusively with run_skill using this version ID, matching scope ID, '
                      'and managed input file IDs. Never copy and execute main.py outside that tool. '
                      'A successful scratch write does not publish a skill or create a managed attachment.\n')
            approved_supplement = '\n\n## Approved import supplement\n\n' + supplement if supplement else ''
            data = (header + policy + content[split:] + approved_supplement).encode('utf-8')
        return data

    def _source(self, file_path, excerpt):
        if self.on_source:
            item = self._approved(file_path.split('/')[1])
            self.on_source(dict(kind='skill', id=item['id'], title=item['title'], excerpt=excerpt))


class ManagedFilesBackend(ReadOnlyBackend):
    """Virtual IDs map to checked managed copies, never original host paths."""

    def __init__(self, service, on_source=None):
        super().__init__(on_source)
        self.files = service.files

    def _entries(self):
        result = {}
        for row in self.files.db.execute('SELECT id FROM files ORDER BY rowid DESC'):
            item = self.files.get(row[0])
            # Default discovery mirrors the application's current-file view.
            # An explicit historical ID remains readable with lifecycle metadata.
            if item.get('lifecycleStatus', 'active') != 'active':
                continue
            base = '/' + item['id'] + '/'
            result[base + 'metadata.json'] = {}
            result[base + 'content.txt'] = {}
            result[base + 'original.' + item['extension']] = dict(size=item['byteSize'])
        return result

    def _load(self, file_path):
        parts = file_path.lstrip('/').split('/', 1)
        if len(parts) != 2:
            raise ValueError('Select metadata.json, content.txt or original.<extension> inside a managed file ID')
        file_id, name = parts
        item = self.files.get(file_id)
        data = self.files.bytes(file_id)  # Verify source bytes even for text/metadata.
        if name == 'content.txt':
            return self.files.read(file_id).encode('utf-8')
        if name == 'metadata.json':
            return json.dumps(item, ensure_ascii=False, indent=2).encode('utf-8')
        if name == 'original.' + item['extension']:
            return data
        raise ValueError('Unknown managed file view')

    def _source(self, file_path, excerpt):
        if self.on_source:
            item = self.files.get(file_path.split('/')[1])
            self.on_source(dict(kind='file', id=item['id'], title=item['name'], excerpt=excerpt))


def make_backend(service, on_source=None):
    """Build the official composite with ephemeral scratch and read-only assets.

    on_source receives {kind, id, title, excerpt}; metadata discovery/downloads
    do not emit read citations. StateBackend requires a running graph context.
    """
    routes={
        '/skills/builtin/': BuiltinSkillsBackend(),
        '/skills/approved/': ApprovedSkillsBackend(service, on_source),
        '/files/': ManagedFilesBackend(service, on_source),
    }
    return CompositeBackend(default=StateBackend(), routes=routes)
