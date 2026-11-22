from contextlib import closing
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from task_backend.repository import TaskRepository


class RepositoryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / 'tasks.sqlite'
        self.repo = TaskRepository(self.path)

    def tearDown(self):
        self.repo.close()
        self.temp.cleanup()

    def create(self, **kwargs):
        return self.repo.call('createTodo', {'title': 'task', **kwargs})['value']

    def test_reopen_preserves_tasks_completion_and_config(self):
        root = self.create()
        self.repo.call('setCompleted', root['id'], True, root['revision'])
        self.repo.call('setCompletionConfig', {'cascadeComplete': False, 'autoCompleteParent': False})
        self.repo.close()
        self.repo = TaskRepository(self.path)
        self.assertTrue(self.repo.call('listTodos')[0]['completed'])
        self.assertFalse(self.repo.call('getCompletionConfig')['cascadeComplete'])
        with closing(sqlite3.connect(self.path)) as db, db:
            self.assertEqual(db.execute('PRAGMA user_version').fetchone()[0], 6)

    def test_failed_combined_edit_rolls_back_content_and_revision(self):
        root = self.create()
        with self.assertRaises(ValueError):
            self.repo.call('editTodo', root['id'], {'title': 'must rollback'}, 'invalid', root['revision'])
        saved = self.repo.call('listTodos')[0]
        self.assertEqual(saved['title'], 'task')
        self.assertEqual(saved['revision'], 1)

    def test_combined_edit_is_atomic_and_returns_latest_revision(self):
        root = self.create()
        saved = self.repo.call('editTodo', root['id'], {'title': 'edited'}, True, root['revision'])['value']
        self.assertEqual(saved['title'], 'edited')
        self.assertTrue(saved['completed'])
        self.assertEqual(saved, self.repo.call('listTodos')[0])

    def test_order_requires_exact_visible_roots_and_survives_restart(self):
        a = self.create()
        b = self.create()
        child = self.create(parentId=a['id'])
        self.repo.call('setDayOrder', '2026-11-11', [b['id'], a['id']])
        with self.assertRaises(ValueError):
            self.repo.call('setDayOrder', '2026-11-11', [b['id'], child['id']])
        self.repo.close()
        self.repo = TaskRepository(self.path)
        self.assertEqual(self.repo.call('getDayOrder', '2026-11-11'), [b['id'], a['id']])

    def test_unknown_schema_refused_without_mutation(self):
        self.repo.close()
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute('CREATE TABLE unexpected(x TEXT)')
        before = self.path.read_bytes()
        with self.assertRaises(ValueError):
            TaskRepository(self.path)
        self.assertEqual(self.path.read_bytes(), before)

    def test_unsupported_version_refused_without_mutation(self):
        self.repo.close()
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute('PRAGMA user_version=99')
        before = self.path.read_bytes()
        with self.assertRaises(ValueError):
            TaskRepository(self.path)
        self.assertEqual(self.path.read_bytes(), before)

    def test_future_and_past_date_roots_do_not_enter_today_order(self):
        root = self.create()
        self.create(due={'mode':'value','value':{'kind':'date','date':'2026-11-12'}})
        self.repo.call('setDayOrder', '2026-11-11', [root['id']])
        self.assertEqual(self.repo.call('getDayOrder', '2026-11-11'), [root['id']])

    def test_sql_constraints_reject_invalid_state(self):
        root = self.create()
        with closing(sqlite3.connect(self.path)) as db, db:
            with self.assertRaises(sqlite3.IntegrityError):
                db.execute('UPDATE todos SET completed=2 WHERE id=?', (root['id'],))

    def test_optional_list_flag_accepts_json_null_from_preload(self):
        self.create()
        self.assertEqual(len(self.repo.call('listTodos', None)), 1)

    def test_unknown_method_does_not_execute_arbitrary_attribute(self):
        with self.assertRaises(ValueError):
            self.repo.call('close')
        self.assertEqual(self.repo.call('listTodos'), [])


if __name__ == '__main__':
    unittest.main()
