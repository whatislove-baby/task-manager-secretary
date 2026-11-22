import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from task_backend.repository import TaskRepository


class MigrationTests(unittest.TestCase):
    def test_old_versions_preserve_tasks_and_completion(self):
        for version in (2,3,4,5):
            with self.subTest(version=version), tempfile.TemporaryDirectory() as folder:
                path=Path(folder)/'tasks.sqlite'
                repo=TaskRepository(path)
                node=repo.call('createTodo',{'title':'legacy'})['value']
                repo.call('setCompleted',node['id'],True,node['revision'])
                repo.close()
                with sqlite3.connect(path) as db:
                    db.execute('ALTER TABLE todos DROP COLUMN completed_at')
                    if version<=3: db.execute('DROP TABLE canvas_items')
                    if version==4:
                        db.execute('ALTER TABLE canvas_items DROP COLUMN description')
                        db.execute('ALTER TABLE canvas_items DROP COLUMN url')
                    if version==2: db.execute('DROP TABLE day_orders')
                    db.execute('PRAGMA user_version='+str(version))
                db.close()
                migrated=TaskRepository(path)
                try:
                    loaded=migrated.call('listTodos')[0]
                    self.assertEqual(loaded['id'],node['id'])
                    self.assertTrue(loaded['completed'])
                    self.assertEqual(loaded['completedAt'],loaded['updatedAt'])
                    self.assertEqual(migrated.db.execute('PRAGMA user_version').fetchone()[0],6)
                finally: migrated.close()

    def test_bad_legacy_schema_refused_before_any_migration(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'tasks.sqlite'
            with sqlite3.connect(path) as db:
                db.execute('CREATE TABLE todos(id TEXT)')
                db.execute('PRAGMA user_version=3')
            db.close()
            before=path.read_bytes()
            with self.assertRaises(ValueError): TaskRepository(path)
            self.assertEqual(before,path.read_bytes())
