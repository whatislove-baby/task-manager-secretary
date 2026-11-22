"""Reversible, explicit retirement of generated artifacts using DraftLifecycle.

Archival is not a semantic replacement: no filename, shared template, sender or
order number is used to decide that another file supersedes this one. Callers
select exact generated IDs. Source documents and immutable bytes are retained.
"""
from contextlib import ExitStack
import json
from pathlib import Path
import sqlite3

from .archive import text
from .draft_lifecycle import DraftLifecycle
from .mail_store import compact, digest
from .tree import identifier, instant


def _rows(directory, filename, sql):
    path = Path(directory) / filename
    if not path.is_file():
        return []
    with sqlite3.connect(path.as_uri() + '?mode=ro', uri=True) as db:
        return db.execute(sql).fetchall()


def _references(files, life, selected):
    """Include lazy pre-lifecycle drafts and standalone send previews."""
    directory = files.service.directory
    referenced = []
    known = set()
    for draft in life.all('mail_draft'):
        known.add(draft['id'])
        if draft['lifecycleStatus'] in ('active', 'sending', 'uncertain') and selected.intersection(draft.get('attachmentIds', [])):
            referenced.append(dict(kind='mail_draft', id=draft['id']))
    for raw, resolution in _rows(directory, 'agent-tools.sqlite', """
        SELECT t.result,r.resolution FROM agent_tool_results t
        LEFT JOIN agent_mail_resolutions r ON r.draft_id=t.id
        WHERE t.operation IN ('create_mail_draft','replace_mail_draft') AND t.result IS NOT NULL
    """):
        draft = json.loads(raw)
        if draft['id'] not in known and not resolution and selected.intersection(draft.get('attachmentIds', [])):
            referenced.append(dict(kind='mail_draft', id=draft['id']))
    for (raw,) in _rows(directory, 'mail-compose.sqlite', 'SELECT payload FROM compose_receipts'):
        receipt = json.loads(raw)
        if receipt.get('status') not in ('prepared', 'sending', 'uncertain'):
            continue
        if not selected.intersection(a['id'] for a in receipt.get('attachments', [])):
            continue
        origin = receipt.get('originDraftId')
        # A prepared preview for an already withdrawn/replaced draft is already
        # unusable by send(); retain that history without blocking cleanup.
        if receipt['status'] == 'prepared' and origin and life.exists(origin) and life.get(origin)['lifecycleStatus'] != 'active':
            continue
        referenced.append(dict(kind='send_preview', id=receipt['id']))
    return referenced


def _audit_table(life):
    life.db.execute('''CREATE TABLE IF NOT EXISTS file_archive_actions(
        id TEXT PRIMARY KEY,request_hash TEXT NOT NULL,result TEXT NOT NULL,created_at TEXT NOT NULL)''')


def _previous(life, operation_id, signature):
    row = life.db.execute('SELECT request_hash,result FROM file_archive_actions WHERE id=?', (operation_id,)).fetchone()
    if not row:
        return None
    if row[0] != signature:
        raise ValueError('文件归档操作标识冲突')
    return json.loads(row[1])


def archive_generated(files, file_ids, reason, expected_revisions=None, *, operation_id):
    if not isinstance(file_ids, list) or not 1 <= len(file_ids) <= 20 or len(set(file_ids)) != len(file_ids):
        raise ValueError('每次归档需要 1 至 20 个不同的生成文件 ID')
    for file_id in file_ids:
        identifier(file_id)
    reason = text(reason, 1000, True).strip()
    if not reason:
        raise ValueError('请说明需要归档这些生成文件的原因')
    expected_revisions = {} if expected_revisions is None else expected_revisions
    if not isinstance(expected_revisions, dict) or set(expected_revisions) - set(file_ids):
        raise ValueError('预期版本只可包含本次明确选择的文件')
    if any(type(v) is not int or v < 1 for v in expected_revisions.values()):
        raise ValueError('文件预期版本必须为正整数')
    identifier(operation_id)
    signature = digest(['archive', file_ids, reason, expected_revisions])
    with DraftLifecycle(files.service.directory) as life:
        _audit_table(life)
        previous = _previous(life, operation_id, signature)
        if previous:
            return previous
        records = [files.get(id) for id in file_ids]
        if any(r['source'] != 'generated' for r in records):
            raise ValueError('只能归档应用生成的文件；原始邮件附件、本地资料和参考文件保留')
        for record in records:
            files.bytes(record['id'])
            life.ensure(record, 'file')
        with ExitStack() as stack:
            # Several explicitly selected versions can share a family. Taking
            # the same process lock twice would falsely report another writer.
            family_ids={life.get(id)['familyId']:id for id in file_ids}
            for family,id in sorted(family_ids.items()):
                stack.enter_context(life.lock(id))
            life.db.execute('BEGIN IMMEDIATE')
            try:
                active=[];inactive=[]
                for record in records:
                    current = life.get(record['id'])
                    expected=expected_revisions.get(record['id'], current['revision'])
                    if current['revision']!=expected:
                        raise ValueError('文件版本已变化，请重新读取')
                    if current['lifecycleStatus'] in ('superseded','archived'):
                        inactive.append(record['id'])
                    else:
                        life.require_active(record['id'], expected, 'file')
                        active.append(record['id'])
                references = _references(files, life, set(active))
                if references:
                    ids = ', '.join(r['kind'] + ':' + r['id'] for r in references)
                    raise ValueError('文件仍被当前邮件草稿或发送预览引用，请先替换或撤回这些草稿/预览，再归档：' + ids)
                for id in active:
                    life.set_status(id, 'archived')
                result = dict(kind='file_archive', id=operation_id, status='archived' if active else 'unchanged', reason=reason,
                              archivedFileIds=active,alreadyInactiveFileIds=inactive,
                              files=[dict(id=id, name=files.get(id)['name'], lifecycleStatus=life.get(id)['lifecycleStatus'],
                                          revision=life.get(id)['revision']) for id in file_ids],
                              detail='明确选择的活动生成稿已归档；已经归档或被替代的历史稿保持原状态，不重复修改。默认列表不展示这些旧稿，字节和历史保留；仅 archived 稿可恢复，superseded 稿不能恢复为当前版。未自动归档参考资料，也未推断文件间的替代关系。')
                life.db.execute('INSERT INTO file_archive_actions VALUES(?,?,?,?)', (operation_id, signature, compact(result), instant()))
                life.db.execute('COMMIT')
                return result
            except BaseException:
                life.db.execute('ROLLBACK')
                raise


def restore_generated(files, file_id, expected_revision, *, operation_id):
    identifier(file_id); identifier(operation_id)
    if type(expected_revision) is not int or expected_revision < 1:
        raise ValueError('文件预期版本必须为正整数')
    signature = digest(['restore', file_id, expected_revision])
    with DraftLifecycle(files.service.directory) as life:
        _audit_table(life)
        previous = _previous(life, operation_id, signature)
        if previous:
            return previous
        source = files.get(file_id)
        if source['source'] != 'generated' or source.get('lifecycleStatus') != 'archived':
            raise ValueError('只能恢复已归档的生成文件；已被正式修订替代的旧版不能重新激活')
        files.bytes(file_id)
        with life.lock(file_id):
            life.db.execute('BEGIN IMMEDIATE')
            try:
                current = life.get(file_id)
                if current['lifecycleStatus'] != 'archived' or current['revision'] != expected_revision:
                    raise ValueError('文件状态或版本已变化，请重新读取')
                active = life.db.execute("SELECT id FROM draft_versions WHERE family_id=? AND status='active'", (current['familyId'],)).fetchone()
                if active:
                    raise ValueError('同一文件已有当前版本，不能恢复历史文件为另一个当前版本')
                life.set_status(file_id, 'active')
                result = dict(kind='file_archive', id=operation_id, status='restored',
                              files=[dict(id=file_id, name=source['name'], lifecycleStatus='active', revision=current['revision'])],
                              detail='已恢复生成文件到当前资料列表；未添加到任何邮件草稿，未发送。')
                life.db.execute('INSERT INTO file_archive_actions VALUES(?,?,?,?)', (operation_id, signature, compact(result), instant()))
                life.db.execute('COMMIT')
                return result
            except BaseException:
                life.db.execute('ROLLBACK')
                raise
