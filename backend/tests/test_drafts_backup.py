import copy
from pathlib import Path
import sqlite3
import tempfile
import unittest
from task_backend.repository import TaskRepository


def draft(key, title='task', parent=None, **extra):
    return dict(tempId=key,parentTempId=parent,title=title,**extra)


class DraftBackupTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.repo=TaskRepository(Path(self.temp.name)/'tasks.sqlite')

    def tearDown(self):
        self.repo.close()
        self.temp.cleanup()

    def test_child_before_parent_sorted_and_confirmation_idempotent(self):
        items=[draft('child',parent='root'),draft('root')]
        self.assertEqual(self.repo.call('saveDrafts',items,'token')['value'],2)
        self.assertEqual(self.repo.call('saveDrafts',items,'token')['value'],2)
        todos=self.repo.call('listTodos')
        self.assertEqual(len(todos),2)
        self.assertEqual(todos[1]['parentId'],todos[0]['id'])
        with self.assertRaises(ValueError):
            self.repo.call('saveDrafts',[draft('root',title='different')],'token')

    def test_bad_batch_does_not_partially_save(self):
        for items in ([draft('a'),draft('b',parent='missing')], [draft('a',parent='b'),draft('b',parent='a')], [draft('a'),draft('a')], [draft('a'),draft('b',title=' ')]):
            with self.subTest(items=items),self.assertRaises(ValueError):
                self.repo.call('saveDrafts',items,'token')
            self.assertEqual(self.repo.call('listTodos'),[])

    def test_late_database_failure_rolls_back_tasks_and_token(self):
        existing=self.repo.call('createTodo',{'title':'existing'})['value']
        with self.assertRaises(ValueError):
            self.repo.call('saveDrafts',[draft('new'),draft('duplicate',todoId=existing['id'])],'token')
        self.assertEqual(len(self.repo.call('listTodos')),1)
        self.assertEqual(self.repo.call('saveDrafts',[draft('new')],'token')['value'],1)

    def test_backup_roundtrip_preserves_tree_trash_and_orders(self):
        root=self.repo.call('createTodo',{'title':'root'})['value']
        child=self.repo.call('createTodo',{'title':'child','parentId':root['id']})['value']
        self.repo.call('setDayOrder','2026-11-12',[root['id']])
        self.repo.call('trashTodo',child['id'],child['revision'])
        backup=self.repo.export_backup()
        other=TaskRepository(Path(self.temp.name)/'other.sqlite')
        try:
            self.assertEqual(other.import_backup(backup)['value'],2)
            self.assertEqual(other.call('listTodos',True),self.repo.call('listTodos',True))
            self.assertEqual(other.call('getDayOrder','2026-11-12'),[root['id']])
            with self.assertRaises(ValueError): other.import_backup(backup)
        finally: other.close()

    def test_invalid_source_mapping_backup_rejected_without_writes(self):
        backup=self.repo.export_backup()
        backup['canvasItems']=[dict(sourceId='a'*64,key='x',todoId='00000000-0000-4000-8000-000000000001',description='',url='')]
        with self.assertRaises(ValueError): self.repo.import_backup(backup)
        self.assertEqual(self.repo.call('listTodos'),[])

    def test_legacy_v2_backup_and_source_read(self):
        backup=dict(version=2,exportedAt='2026-11-12T00:00:00.000Z',todos=[],completionConfig=dict(cascadeComplete=True,autoCompleteParent=True))
        self.assertEqual(self.repo.import_backup(backup)['value'],0)
        node=self.repo.call('createTodo',{'title':'x'})['value']
        self.assertIsNone(self.repo.call('getCanvasSource',node['id']))


if __name__=='__main__': unittest.main()
