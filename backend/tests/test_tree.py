"""Behavioral migration tests, derived from the original product rules."""
import unittest
from task_backend.tree import TodoTree


class TreeTests(unittest.TestCase):
    def setUp(self):
        self.tree = TodoTree()

    def root(self, **kwargs):
        return self.tree.create({'title': 'root', **kwargs})

    def child(self, parent, **kwargs):
        return self.tree.create({'title': 'child', 'parentId': parent['id'], **kwargs})

    def current(self, node):
        return self.tree.get(node['id'])

    def test_create_trims_title_defaults_and_returns_isolated_copy(self):
        root = self.root(title='  中文任务  ')
        self.assertEqual(root['title'], '中文任务')
        self.assertEqual(root['due'], {'mode': 'none'})
        self.assertEqual(root['revision'], 1)
        root['title'] = 'tampered'
        self.assertEqual(self.current(root)['title'], '中文任务')

    def test_inheritance_tracks_parent_and_none_blocks_it(self):
        root = self.root(due={'mode': 'value', 'value': {'kind': 'date', 'date': '2026-11-12'}})
        child = self.child(root)
        grandchild = self.child(child)
        self.assertEqual(self.tree.effective_due(grandchild['id'])['sourceId'], root['id'])
        self.assertIsNone(self.tree.effective_due(child['id'])['displayedDue'])
        self.tree.update(child['id'], {'due': {'mode': 'none'}}, child['revision'])
        self.assertIsNone(self.tree.effective_due(grandchild['id'])['deadline'])

    def test_completing_parent_cascades_reopening_preserves_children(self):
        root = self.root()
        child = self.child(root)
        root = self.tree.set_completed(root['id'], True, self.current(root)['revision'])
        self.assertTrue(self.current(child)['completed'])
        self.tree.set_completed(root['id'], False, root['revision'])
        self.assertFalse(self.current(root)['completed'])
        self.assertTrue(self.current(child)['completed'])

    def test_last_child_completion_completes_parent_new_child_reopens(self):
        root = self.root()
        child = self.child(root)
        self.tree.set_completed(child['id'], True, child['revision'])
        self.assertTrue(self.current(root)['completed'])
        self.child(root)
        self.assertFalse(self.current(root)['completed'])

    def test_disabled_cascade_and_auto_completion(self):
        self.tree.set_config({'cascadeComplete': False, 'autoCompleteParent': False})
        root = self.root()
        child = self.child(root)
        self.tree.set_completed(root['id'], True, root['revision'])
        self.assertFalse(self.current(child)['completed'])

    def test_stale_edit_rejected_without_overwriting(self):
        root = self.root()
        self.tree.update(root['id'], {'notes': 'saved'}, root['revision'])
        with self.assertRaises(ValueError):
            self.tree.update(root['id'], {'notes': 'lost'}, root['revision'])
        self.assertEqual(self.current(root)['notes'], 'saved')

    def test_cycle_move_rejected_and_move_to_root_clears_inheritance(self):
        root = self.root()
        child = self.child(root)
        with self.assertRaises(ValueError):
            self.tree.move(root['id'], child['id'], self.current(root)['revision'])
        moved = self.tree.move(child['id'], None, child['revision'])
        self.assertIsNone(moved['parentId'])
        self.assertEqual(moved['due'], {'mode': 'none'})

    def test_trash_batches_exclude_previously_deleted_branch(self):
        root = self.root()
        a = self.child(root)
        b = self.child(root)
        old_batch = self.tree.trash(a['id'], a['revision'])
        batch = self.tree.trash(root['id'], self.current(root)['revision'])
        self.assertEqual(self.tree.list(), [])
        self.assertEqual(len(self.tree.restore(batch)), 2)
        self.assertEqual(self.current(a)['trashBatchId'], old_batch)
        self.assertEqual(self.tree.restore(batch), [])
        self.assertFalse(self.current(b)['deletedAt'])

    def test_restore_without_active_parent_becomes_root(self):
        root = self.root()
        child = self.child(root)
        child_batch = self.tree.trash(child['id'], child['revision'])
        self.tree.trash(root['id'], self.current(root)['revision'])
        restored = self.tree.restore(child_batch)[0]
        self.assertIsNone(restored['parentId'])
        self.assertEqual(restored['due'], {'mode': 'none'})

    def test_invalid_inputs_and_unknown_fields_rejected(self):
        for payload in ({'title': ' '}, {'title': 'x', 'priority': 1},
                        {'title': 'x', 'due': {'mode': 'inherit'}},
                        {'title': 'x', 'due': {'mode': 'value', 'value': {'kind': 'date', 'date': '2026-02-30'}}}):
            with self.subTest(payload=payload), self.assertRaises(ValueError):
                self.tree.create(payload)
        self.assertEqual(self.tree.list(), [])

    def test_later_child_deadline_is_warning_not_rejection(self):
        root = self.root(due={'mode': 'value', 'value': {'kind': 'date', 'date': '2026-11-12'}})
        child = self.child(root, due={'mode': 'value', 'value': {'kind': 'date', 'date': '2026-11-13'}})
        self.assertEqual(self.tree.warnings()[0]['todoId'], child['id'])
        self.assertEqual(self.tree.warnings()[0]['code'], 'AFTER_PARENT')

    def test_rejects_disconnected_trash_batch_and_cycle_on_load(self):
        a, b = self.root(), self.root()
        a['parentId'], b['parentId'] = b['id'], a['id']
        with self.assertRaises(ValueError):
            TodoTree([a, b])
        a['parentId'] = b['parentId'] = None
        for n in (a, b):
            n['deletedAt'] = n['updatedAt']
            n['trashBatchId'] = a['id']
        with self.assertRaises(ValueError):
            TodoTree([a, b])


if __name__ == '__main__':
    unittest.main()

class BoundaryTests(unittest.TestCase):
    def test_null_boolean_and_fractional_revisions_rejected(self):
        tree = TodoTree()
        root = tree.create({'title': 'root'})
        for revision in (None, True, 1.5, 0, '1'):
            with self.subTest(revision=revision), self.assertRaises(ValueError):
                tree.update(root['id'], {'title': 'changed'}, revision)
        self.assertEqual(tree.get(root['id'])['title'], 'root')

    def test_minute_deadline_normalizes_utc_and_rejects_seconds(self):
        tree = TodoTree()
        root = tree.create({'title':'minute','due':{'mode':'value','value':{'kind':'minute','at':'2026-11-11T10:30:00+08:00','timeZone':'Asia/Shanghai'}}})
        self.assertEqual(root['due']['value']['at'], '2026-11-11T02:30:00.000Z')
        with self.assertRaises(ValueError):
            tree.create({'title':'bad','due':{'mode':'value','value':{'kind':'minute','at':'2026-11-11T10:30:01+08:00','timeZone':'Asia/Shanghai'}}})

    def test_deep_tree_does_not_use_python_recursion(self):
        tree = TodoTree(config={'cascadeComplete':False,'autoCompleteParent':False})
        root = tree.create({'title':'root','due':{'mode':'value','value':{'kind':'date','date':'2026-11-12'}}})
        parent = root
        for _ in range(1200):
            parent = tree.create({'title':'child','parentId':parent['id']})
        loaded = TodoTree(tree.list(True), tree.config)
        self.assertEqual(loaded.effective_due(parent['id'])['sourceId'], root['id'])
        self.assertEqual(loaded.warnings(), [])
