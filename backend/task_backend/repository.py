"""SQLite v6 adapter. One transaction owns each complete business command."""
from copy import deepcopy
import json
import sqlite3
from .tree import TodoTree, DEFAULT_CONFIG, calendar, config_value, deadline_day, parse_instant

SCHEMA = """
CREATE TABLE todos (
 id TEXT PRIMARY KEY NOT NULL, parent_id TEXT REFERENCES todos(id) DEFERRABLE INITIALLY DEFERRED,
 title TEXT NOT NULL CHECK(length(trim(title)) BETWEEN 1 AND 200), notes TEXT NOT NULL,
 completed INTEGER NOT NULL CHECK(completed IN (0,1)), due_json TEXT NOT NULL CHECK(json_valid(due_json)),
 created_at TEXT NOT NULL, updated_at TEXT NOT NULL, revision INTEGER NOT NULL CHECK(revision > 0),
 deleted_at TEXT, trash_batch_id TEXT, completed_at TEXT,
 CHECK((deleted_at IS NULL) = (trash_batch_id IS NULL)));
CREATE INDEX todos_parent ON todos(parent_id, deleted_at);
CREATE INDEX todos_trash ON todos(trash_batch_id);
CREATE TABLE metadata (key TEXT PRIMARY KEY NOT NULL, value TEXT NOT NULL CHECK(json_valid(value)));
CREATE TABLE draft_confirmations (token TEXT PRIMARY KEY NOT NULL, fingerprint TEXT NOT NULL, count INTEGER NOT NULL CHECK(count >= 0));
CREATE TABLE day_orders (day TEXT PRIMARY KEY NOT NULL, value TEXT NOT NULL CHECK(json_valid(value)));
CREATE TABLE canvas_items (source_id TEXT NOT NULL, item_key TEXT NOT NULL, todo_id TEXT NOT NULL UNIQUE REFERENCES todos(id) DEFERRABLE INITIALLY DEFERRED, description TEXT NOT NULL DEFAULT '', url TEXT NOT NULL DEFAULT '', PRIMARY KEY(source_id,item_key));
"""
COLUMNS = {
 'todos': ['id','parent_id','title','notes','completed','due_json','created_at','updated_at','revision','deleted_at','trash_batch_id','completed_at'],
 'metadata': ['key','value'], 'draft_confirmations': ['token','fingerprint','count'],
 'day_orders': ['day','value'], 'canvas_items': ['source_id','item_key','todo_id','description','url'],
}


class TaskRepository:
    METHODS = {'listTodos', 'effectiveDue', 'createTodo', 'updateTodo', 'editTodo', 'moveTodo', 'setCompleted', 'trashTodo', 'restoreBatch', 'getCompletionConfig', 'setCompletionConfig', 'getDayOrder', 'setDayOrder', 'saveDrafts', 'getCanvasSource', 'exportBackupFile', 'importBackupFile'}

    def __init__(self, path):
        self.db = sqlite3.connect(str(path), isolation_level=None, timeout=3)
        self.db.row_factory = sqlite3.Row
        self.closed = False
        try:
            self.db.execute('PRAGMA foreign_keys=ON')
            version = self.db.execute('PRAGMA user_version').fetchone()[0]
            tables = self._tables()
            if version == 0 and not tables:
                self.db.executescript('BEGIN IMMEDIATE;\n' + SCHEMA)
                try:
                    self.db.execute('INSERT INTO metadata VALUES (?,?)', ('completionConfig', json.dumps(DEFAULT_CONFIG)))
                    self.db.execute('PRAGMA user_version=6')
                    self.db.execute('COMMIT')
                except BaseException:
                    self.db.execute('ROLLBACK')
                    raise
            elif version in (2,3,4,5):
                self.db.execute('BEGIN IMMEDIATE')
                try:
                    self._verify(version)
                    if version == 2:
                        self.db.execute("CREATE TABLE day_orders (day TEXT PRIMARY KEY NOT NULL, value TEXT NOT NULL CHECK(json_valid(value)))")
                    if version in (2,3):
                        self.db.execute("CREATE TABLE canvas_items (source_id TEXT NOT NULL, item_key TEXT NOT NULL, todo_id TEXT NOT NULL UNIQUE REFERENCES todos(id) DEFERRABLE INITIALLY DEFERRED, description TEXT NOT NULL DEFAULT '', url TEXT NOT NULL DEFAULT '', PRIMARY KEY(source_id,item_key))")
                    if version == 4:
                        self.db.execute("ALTER TABLE canvas_items ADD COLUMN description TEXT NOT NULL DEFAULT ''")
                        self.db.execute("ALTER TABLE canvas_items ADD COLUMN url TEXT NOT NULL DEFAULT ''")
                    self.db.execute('ALTER TABLE todos ADD COLUMN completed_at TEXT')
                    self.db.execute('UPDATE todos SET completed_at=updated_at WHERE completed=1')
                    self.db.execute('PRAGMA user_version=6')
                    self._verify()
                    self.db.execute('COMMIT')
                except BaseException:
                    self.db.execute('ROLLBACK');raise
            elif version != 6:
                raise ValueError('当前 Python 迁移阶段仅接受 v6 数据库；请先使用旧程序迁移副本，原库未修改。')
            self._verify()
            self._tree()
        except BaseException:
            self.close()
            raise

    def export_backup(self):
        from .archive import export_backup
        return export_backup(self)

    def import_backup(self,value):
        from .archive import import_backup
        return import_backup(self,value)

    def close(self):
        if not self.closed:
            self.db.close()
            self.closed = True

    def _tables(self):
        return sorted(r[0] for r in self.db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"))

    def _verify(self,version=6):
        expected={k:list(v) for k,v in COLUMNS.items()}
        if version<6: expected['todos'].remove('completed_at')
        if version==2: expected.pop('day_orders')
        if version<4: expected.pop('canvas_items')
        elif version==4: expected['canvas_items']=expected['canvas_items'][:3]
        if self._tables() != sorted(expected):
            raise ValueError('数据库表结构不匹配，拒绝写入')
        if self.db.execute("SELECT name FROM sqlite_master WHERE type='trigger'").fetchone():
            raise ValueError('数据库包含未知触发器，拒绝写入')
        for table, columns in expected.items():
            fields = self.db.execute('PRAGMA table_info(' + table + ')').fetchall()
            if [r['name'] for r in fields] != columns:
                raise ValueError('数据库字段不匹配')
            for field in fields:
                name = field['name']
                expected_type = 'INTEGER' if name in ('completed', 'revision', 'count') else 'TEXT'
                optional = name in ('parent_id', 'deleted_at', 'trash_batch_id', 'completed_at')
                pk = (1 if name == 'source_id' else 2 if name == 'item_key' else 0) if table == 'canvas_items' else int(name in ('id','key','token','day'))
                if field['type'].upper() != expected_type or field['notnull'] != int(not optional) or field['pk'] != pk:
                    raise ValueError('数据库字段约束不匹配')
        for table, column, ref in (('todos','parent_id','todos'), ('canvas_items','todo_id','todos')):
            if table not in expected: continue
            foreign = self.db.execute('PRAGMA foreign_key_list(' + table + ')').fetchall()
            if len(foreign) != 1 or foreign[0]['from'] != column or foreign[0]['table'] != ref or foreign[0]['to'] != 'id':
                raise ValueError('数据库引用约束不匹配')
        if self.db.execute('PRAGMA foreign_key_check').fetchone() or self.db.execute('PRAGMA quick_check').fetchone()[0] != 'ok':
            raise ValueError('数据库完整性检查失败')
        if [r[0] for r in self.db.execute('SELECT key FROM metadata')] != ['completionConfig']:
            raise ValueError('数据库配置不匹配')
        tree = self._tree()
        roots = {n['id'] for n in tree.list(True) if n['parentId'] is None}
        for row in self.db.execute('SELECT day,value FROM day_orders') if 'day_orders' in expected else ():
            calendar(row['day'])
            ids = json.loads(row['value'])
            if not isinstance(ids, list) or any(not isinstance(i, str) for i in ids) or len(ids) != len(set(ids)) or not set(ids) <= roots:
                raise ValueError('排序记录无效')

        from .archive import source_value, validate_relations, text
        import re
        for row in self.db.execute('SELECT * FROM draft_confirmations'):
            text(row['token'],200,True)
            if not row['token'].strip() or not re.fullmatch('[a-f0-9]{64}',row['fingerprint']) or type(row['count']) is not int or not 1<=row['count']<=100: raise ValueError('草稿确认记录无效')
        if 'canvas_items' in expected:
            mappings=[]
            for row in self.db.execute('SELECT * FROM canvas_items'):
                value=dict(sourceId=row['source_id'],key=row['item_key'],todoId=row['todo_id'])
                if version>=5: value.update(description=row['description'],url=row['url'])
                mappings.append(source_value(value,version>=5))
            validate_relations([],mappings,tree)

    def _config(self):
        return config_value(json.loads(self.db.execute("SELECT value FROM metadata WHERE key='completionConfig'").fetchone()[0]))

    def _tree(self):
        nodes = []
        for r in self.db.execute('SELECT * FROM todos ORDER BY rowid'):
            if r['completed'] not in (0, 1):
                raise ValueError('数据库完成状态无效')
            nodes.append(dict(id=r['id'], parentId=r['parent_id'], title=r['title'], notes=r['notes'], completed=bool(r['completed']), completedAt=r['completed_at'] if 'completed_at' in r.keys() else r['updated_at'] if r['completed'] else None, due=json.loads(r['due_json']), createdAt=r['created_at'], updatedAt=r['updated_at'], revision=r['revision'], deletedAt=r['deleted_at'], trashBatchId=r['trash_batch_id']))
        return TodoTree(nodes, self._config())

    def _persist(self, tree, before):
        for node in tree.list(True):
            old = before.get(node['id'])
            values = (node['parentId'], node['title'], node['notes'], int(node['completed']), json.dumps(node['due'], ensure_ascii=False), node['createdAt'], node['updatedAt'], node['revision'], node['deletedAt'], node['trashBatchId'], node.get('completedAt'))
            if old is None:
                self.db.execute('INSERT INTO todos VALUES (?,?,?,?,?,?,?,?,?,?,?,?)', (node['id'], *values))
            elif old != node:
                if node['revision'] <= old['revision']:
                    raise ValueError('领域变更缺少版本递增')
                changed = self.db.execute('UPDATE todos SET parent_id=?,title=?,notes=?,completed=?,due_json=?,created_at=?,updated_at=?,revision=?,deleted_at=?,trash_batch_id=?,completed_at=? WHERE id=? AND revision=?', (*values, node['id'], old['revision'])).rowcount
                if changed != 1:
                    raise ValueError('版本冲突')
        roots = {n['id'] for n in tree.list(True) if n['parentId'] is None}
        for row in self.db.execute('SELECT day,value FROM day_orders').fetchall():
            ids = json.loads(row['value'])
            kept = [id for id in ids if id in roots]
            if kept != ids:
                self.db.execute('UPDATE day_orders SET value=? WHERE day=?', (json.dumps(kept), row['day']))
        self.db.execute("UPDATE metadata SET value=? WHERE key='completionConfig'", (json.dumps(tree.config),))

    def _write(self, action):
        self.db.execute('BEGIN IMMEDIATE')
        try:
            tree = self._tree()
            before = {n['id']: n for n in tree.list(True)}
            value = action(tree)
            self._persist(tree, before)
            warnings = tree.warnings()
            self.db.execute('COMMIT')
            return {'value': value, 'warnings': warnings}
        except BaseException:
            self.db.execute('ROLLBACK')
            raise

    def call(self, method, *args):
        if method not in self.METHODS:
            raise ValueError('接口尚未迁移或不在白名单中：' + str(method))
        if method == 'saveDrafts':
            from .archive import save_drafts
            return save_drafts(self,*args)
        if method in ('exportBackupFile','importBackupFile'):
            from .archive import backup_file
            path,=args
            return backup_file(self,path,method=='exportBackupFile')
        if method == 'getCanvasSource':
            from .tree import identifier
            id,=args
            identifier(id)
            row=self.db.execute('SELECT description,url FROM canvas_items WHERE todo_id=?',(id,)).fetchone()
            return dict(row) if row else None
        if method == 'listTodos':
            if len(args) > 1 or (args and args[0] is not None and type(args[0]) is not bool):
                raise ValueError('无效查询参数')
            return self._tree().list(args[0] if args and args[0] is not None else False)
        if method == 'effectiveDue':
            return self._tree().effective_due(*args)
        if method == 'getCompletionConfig':
            if args:
                raise ValueError('无效参数')
            return self._config()
        if method == 'setCompletionConfig':
            def set_config(tree):
                tree.set_config(*args)
                return deepcopy(tree.config)
            return self._write(set_config)['value']
        if method == 'getDayOrder':
            day, = args
            calendar(day)
            row = self.db.execute('SELECT value FROM day_orders WHERE day=?', (day,)).fetchone()
            return json.loads(row[0]) if row else []
        if method == 'setDayOrder':
            day, ids = args
            calendar(day)
            if not isinstance(ids, list) or any(not isinstance(i, str) for i in ids) or len(ids) != len(set(ids)):
                raise ValueError('无效排序')
            self.db.execute('BEGIN IMMEDIATE')
            try:
                expected = set()
                for node in self._tree().list():
                    if node['parentId'] is not None:
                        continue
                    due = node['due'].get('value')
                    visible = deadline_day(due) == day if due else (not node['completed'] or parse_instant(node.get('completedAt') or node['updatedAt']).astimezone().date().isoformat() == day)
                    if visible:
                        expected.add(node['id'])
                if set(ids) != expected:
                    raise ValueError('当日任务列表已变化，请刷新后重新排序')
                self.db.execute('INSERT INTO day_orders VALUES (?,?) ON CONFLICT(day) DO UPDATE SET value=excluded.value', (day, json.dumps(ids)))
                self.db.execute('COMMIT')
                return ids[:]
            except BaseException:
                self.db.execute('ROLLBACK')
                raise
        if method == 'editTodo':
            id, patch, complete, revision = args
            if type(complete) is not bool:
                raise ValueError('完成状态必须是布尔值')
            def edit(tree):
                saved = tree.update(id, patch, revision)
                if saved['completed'] != complete:
                    tree.set_completed(id, complete, saved['revision'])
                return tree.get(id)
            return self._write(edit)
        operations = {'createTodo':'create', 'updateTodo':'update', 'moveTodo':'move', 'setCompleted':'set_completed', 'trashTodo':'trash', 'restoreBatch':'restore'}
        return self._write(lambda tree: getattr(tree, operations[method])(*args))
