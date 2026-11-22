"""Business draft revision adapter; immutable history, atomic current pointers.

This does not implement an agent runtime. Every writer/consumer uses the same
family lock, including SMTP, so a stale preview cannot race a replacement.
"""
from contextlib import contextmanager
import json
from pathlib import Path
import sqlite3
from .tree import identifier, instant
from .mail_store import compact


class DraftLifecycle:
    def __init__(self, directory):
        self.directory = Path(directory)
        self.db = sqlite3.connect(self.directory / 'draft-lifecycle.sqlite', isolation_level=None, timeout=10)
        self.db.row_factory = sqlite3.Row
        self.db.executescript('''
          CREATE TABLE IF NOT EXISTS draft_versions(
            id TEXT PRIMARY KEY,kind TEXT NOT NULL,family_id TEXT NOT NULL,
            revision INTEGER NOT NULL,status TEXT NOT NULL,superseded_by TEXT,
            payload TEXT NOT NULL,created_at TEXT NOT NULL,
            UNIQUE(family_id,revision));
          CREATE UNIQUE INDEX IF NOT EXISTS draft_one_active ON draft_versions(family_id)
            WHERE status='active';
        ''')
    def close(self): self.db.close()
    def __enter__(self): return self
    def __exit__(self, *args): self.close()
    def get(self, id):
        identifier(id)
        row = self.db.execute('SELECT * FROM draft_versions WHERE id=?',(id,)).fetchone()
        if not row: raise ValueError('找不到草稿版本，请重新加载待确认事项')
        return dict(json.loads(row['payload']), familyId=row['family_id'],revision=row['revision'],
                    lifecycleStatus=row['status'],supersededBy=row['superseded_by'])
    def exists(self, id):
        return bool(self.db.execute('SELECT 1 FROM draft_versions WHERE id=?',(id,)).fetchone())
    def ensure(self, payload, kind):
        """Lazy migration of historical generated drafts; never change old payload."""
        identifier(payload['id'])
        if self.exists(payload['id']):return self.get(payload['id'])
        self.db.execute('BEGIN IMMEDIATE')
        try:
            self._check_attachments(payload,kind)
            self.db.execute('INSERT OR IGNORE INTO draft_versions VALUES(?,?,?,?,?,?,?,?)',
                            (payload['id'],kind,payload['id'],1,'active',None,compact(payload),instant()))
            self.db.execute('COMMIT')
        except BaseException:
            self.db.execute('ROLLBACK');raise
        return self.get(payload['id'])
    def _check_attachments(self,payload,kind):
        """Same SQLite write transaction as the draft; cannot race archiving."""
        if kind!='mail_draft':return
        for id in payload.get('attachmentIds',[]):
            row=self.db.execute('SELECT status FROM draft_versions WHERE id=?',(id,)).fetchone()
            if row and row['status']!='active':
                raise ValueError('附件已被替代或归档，请选择当前文件版本')
    @contextmanager
    def lock(self, id):
        from .mail_compose import receipt_lock
        row=self.get(id)
        with receipt_lock(self.directory/'draft-locks'/(row['familyId']+'.lock')) as acquired:
            if not acquired: raise ValueError('草稿正在修改、保存或发送，请等待后刷新')
            yield self.get(id)
    def require_active(self, id, revision, kind=None):
        value=self.get(id)
        if type(revision) is not int or value['revision']!=revision:
            raise ValueError('草稿版本已变化，请重新打开当前版本')
        if kind and self.db.execute('SELECT kind FROM draft_versions WHERE id=?',(id,)).fetchone()[0]!=kind:
            raise ValueError('草稿类型不匹配')
        if value['lifecycleStatus']!='active':
            raise ValueError('此草稿已被替代、撤回、保存或正在发送，请打开当前版本')
        return value
    def replace(self, old_id, expected_revision, payload, kind):
        # Caller holds family lock from validation through payload persistence.
        if self.exists(payload['id']):
            value=self.get(payload['id'])
            if value.get('replacesId')!=old_id: raise ValueError('草稿修订标识冲突')
            return value
        old=self.require_active(old_id,expected_revision,kind)
        body=dict(payload,replacesId=old_id)
        self.db.execute('BEGIN IMMEDIATE')
        try:
            self.require_active(old_id,expected_revision,kind)
            self._check_attachments(payload,kind)
            self.db.execute("UPDATE draft_versions SET status='superseded',superseded_by=? WHERE id=?",(payload['id'],old_id))
            self.db.execute('INSERT INTO draft_versions VALUES(?,?,?,?,?,?,?,?)',
                (payload['id'],kind,old['familyId'],expected_revision+1,'active',None,compact(body),instant()))
            self.db.execute('COMMIT')
        except BaseException:
            self.db.execute('ROLLBACK');raise
        return self.get(payload['id'])
    def set_status(self,id,status):
        if status not in ('active','archived','dismissed','sending','sent','uncertain','saving','saved'):
            raise ValueError('草稿状态无效')
        self.db.execute('UPDATE draft_versions SET status=? WHERE id=?',(status,id))
    def all(self,kind=None):
        rows=self.db.execute('SELECT id FROM draft_versions ORDER BY created_at DESC,id').fetchall()
        return [self.get(row['id']) for row in rows if not kind or self.db.execute('SELECT kind FROM draft_versions WHERE id=?',(row['id'],)).fetchone()[0]==kind]
