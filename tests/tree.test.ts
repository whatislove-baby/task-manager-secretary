import test from 'node:test';
import assert from 'node:assert/strict';
import { randomUUID } from 'node:crypto';
import { TodoTree, isOverdue } from '../src/domain/tree';
import type { Todo, Deadline } from '../src/shared/todo';

const date = (day: string) => ({ mode: 'value' as const, value: { kind: 'date' as const, date: day } });
const minute = (at: string) => ({ mode: 'value' as const, value: { kind: 'minute' as const, at, timeZone: 'UTC' } });
const complete = (tree: TodoTree, id: string, value = true) => tree.setCompleted(id, value, tree.get(id).revision);

for (const cascadeComplete of [false, true]) for (const autoCompleteParent of [false, true]) {
  test(`completion switches cascade=${cascadeComplete}, auto=${autoCompleteParent}`, () => {
    const tree = new TodoTree([], { cascadeComplete, autoCompleteParent });
    const parent = tree.create({ title: 'parent' });
    const child = tree.create({ title: 'child', parentId: parent.id });
    const grandchild = tree.create({ title: 'grandchild', parentId: child.id });
    complete(tree, parent.id);
    assert.equal(tree.get(child.id).completed, cascadeComplete);
    assert.equal(tree.get(grandchild.id).completed, cascadeComplete);
    const second = new TodoTree([], { cascadeComplete, autoCompleteParent });
    const p = second.create({ title: 'p' });
    const a = second.create({ title: 'a', parentId: p.id });
    const b = second.create({ title: 'b', parentId: p.id });
    complete(second, a.id);
    assert.equal(second.get(p.id).completed, false);
    complete(second, b.id);
    assert.equal(second.get(p.id).completed, autoCompleteParent);
  });
}

test('explicitly reopening parent preserves all descendants and remains open until a later related event', () => {
  const tree = new TodoTree();
  const p = tree.create({ title: 'p' });
  const a = tree.create({ title: 'a', parentId: p.id });
  const b = tree.create({ title: 'b', parentId: p.id });
  complete(tree, p.id);
  complete(tree, p.id, false);
  assert.equal(tree.get(p.id).completed, false);
  assert.equal(tree.get(a.id).completed, true);
  assert.equal(tree.get(b.id).completed, true);
  tree.update(p.id, { notes: 'does not recompute completion' }, tree.get(p.id).revision);
  assert.equal(tree.get(p.id).completed, false);
  complete(tree, a.id);
  assert.equal(tree.get(p.id).completed, true);
  complete(tree, a.id, false);
  assert.equal(tree.get(p.id).completed, false);
  assert.equal(tree.get(b.id).completed, true);
});

test('reopening child reopens each ancestor without changing siblings; config changes are prospective', () => {
  const tree = new TodoTree();
  const p = tree.create({ title: 'p' });
  const a = tree.create({ title: 'a', parentId: p.id });
  const leaf = tree.create({ title: 'leaf', parentId: a.id });
  const sibling = tree.create({ title: 'sibling', parentId: p.id });
  complete(tree, p.id);
  complete(tree, leaf.id, false);
  assert.equal(tree.get(a.id).completed, false);
  assert.equal(tree.get(p.id).completed, false);
  assert.equal(tree.get(sibling.id).completed, true);
  tree.setConfig({ cascadeComplete: false, autoCompleteParent: false });
  complete(tree, p.id);
  tree.setConfig({ cascadeComplete: true, autoCompleteParent: true });
  assert.equal(tree.get(leaf.id).completed, false);
  assert.equal(tree.get(p.id).completed, true);
});

test('dynamic inheritance, hidden inherited display, explicit overrides and empty barriers', () => {
  const tree = new TodoTree();
  const p = tree.create({ title: 'p', due: date('2026-10-29') });
  const c = tree.create({ title: 'c', parentId: p.id });
  const g = tree.create({ title: 'g', parentId: c.id });
  assert.deepEqual(tree.effectiveDue(g.id), { deadline: date('2026-10-29').value, displayedDue: null, inherited: true, sourceId: p.id });
  tree.update(p.id, { due: date('2026-10-30') }, tree.get(p.id).revision);
  assert.deepEqual(tree.effectiveDue(g.id).deadline, date('2026-10-30').value);
  tree.update(c.id, { due: date('2026-11-01') }, tree.get(c.id).revision);
  assert.deepEqual(tree.effectiveDue(c.id).displayedDue, date('2026-11-01').value);
  assert.equal(tree.effectiveDue(g.id).sourceId, c.id);
  assert.equal(tree.warnings().length, 1);
  tree.update(c.id, { due: { mode: 'none' } }, tree.get(c.id).revision);
  assert.equal(tree.effectiveDue(g.id).deadline, null);
  tree.update(c.id, { due: { mode: 'inherit' } }, tree.get(c.id).revision);
  assert.equal(tree.effectiveDue(g.id).sourceId, p.id);
  tree.move(c.id, null, tree.get(c.id).revision);
  assert.deepEqual(tree.get(c.id).due, { mode: 'none' });
  assert.equal(tree.effectiveDue(g.id).deadline, null);
});

test('mixed precision compares local days, minute precision compares actual instants', () => {
  const tree = new TodoTree();
  const local = new Date(2026, 9, 29, 13, 0, 0);
  const p = tree.create({ title: 'p', due: date('2026-10-29') });
  const c = tree.create({ title: 'c', parentId: p.id, due: minute(local.toISOString()) });
  assert.equal(tree.warnings().length, 0);
  const tomorrow = new Date(2026, 9, 30, 1, 0, 0);
  tree.update(c.id, { due: minute(tomorrow.toISOString()) }, tree.get(c.id).revision);
  assert.equal(tree.warnings()[0].code, 'AFTER_PARENT');
  tree.update(p.id, { due: minute('2026-10-30T10:00:00.000Z') }, tree.get(p.id).revision);
  tree.update(c.id, { due: minute('2026-10-30T11:00:00.000Z') }, tree.get(c.id).revision);
  assert.equal(tree.warnings().length, 1);
  tree.update(c.id, { due: minute('2026-10-30T12:00:00+02:00') }, tree.get(c.id).revision);
  assert.equal(tree.warnings().length, 0);
});

test('date and minute overdue boundaries use local calendar and strict instant boundaries', () => {
  const d: Deadline = { kind: 'date', date: '2026-10-29' };
  assert.equal(isOverdue(d, new Date(2026, 9, 29, 23, 59, 59)), false);
  assert.equal(isOverdue(d, new Date(2026, 9, 30, 0, 0, 0)), true);
  const m = minute('2026-10-29T10:00:00.000Z').value;
  assert.equal(isOverdue(m, new Date(m.at)), false);
  assert.equal(isOverdue(m, new Date(Date.parse(m.at) + 1)), true);
  assert.equal(isOverdue(null), false);
});

test('invalid commands are atomic; own/descendant parents, stale revisions, invalid dates and root inheritance fail', () => {
  const tree = new TodoTree();
  const p = tree.create({ title: ' p ' });
  const c = tree.create({ title: 'c', parentId: p.id });
  const before = tree.list(true);
  assert.throws(() => tree.move(p.id, p.id, p.revision));
  assert.throws(() => tree.move(p.id, c.id, p.revision));
  assert.throws(() => tree.update(p.id, { title: 'wrong' }, p.revision + 10));
  assert.throws(() => tree.update(p.id, { title: ' ' }, p.revision));
  assert.throws(() => tree.update(p.id, { due: date('2026-02-30') }, p.revision));
  assert.throws(() => tree.create({ title: 'root', due: { mode: 'inherit' } }));
  assert.throws(() => tree.update(p.id, { due: { mode: 'inherit' } }, p.revision));
  assert.deepEqual(tree.list(true), before);
  assert.equal(p.title, 'p');
});

test('trash isolates previous batches, excludes deleted nodes, restores under active parent and is idempotent', () => {
  const tree = new TodoTree();
  const p = tree.create({ title: 'p' });
  const c = tree.create({ title: 'c', parentId: p.id });
  const old = tree.create({ title: 'old', parentId: c.id });
  const oldBatch = tree.trash(old.id, tree.get(old.id).revision);
  const batch = tree.trash(c.id, tree.get(c.id).revision);
  assert.equal(tree.list().length, 1);
  assert.equal(tree.get(old.id).trashBatchId, oldBatch);
  assert.throws(() => tree.create({ title: 'x', parentId: c.id }));
  assert.throws(() => tree.move(p.id, c.id, tree.get(p.id).revision));
  assert.throws(() => tree.update(c.id, { title: 'x' }, tree.get(c.id).revision));
  assert.equal(tree.restore(batch).length, 1);
  assert.equal(tree.get(c.id).parentId, p.id);
  assert.equal(tree.get(old.id).deletedAt !== null, true);
  assert.deepEqual(tree.restore(batch), []);
  assert.equal(tree.restore(oldBatch).length, 1);
});

test('restore fallback makes root, clears inherited mode, preserves completion; restored unfinished child reopens parent', () => {
  const tree = new TodoTree();
  const p = tree.create({ title: 'p', due: date('2026-10-29') });
  const c = tree.create({ title: 'c', parentId: p.id });
  const g = tree.create({ title: 'g', parentId: c.id });
  complete(tree, g.id);
  complete(tree, c.id, false);
  const batch = tree.trash(c.id, tree.get(c.id).revision);
  tree.trash(p.id, tree.get(p.id).revision);
  const restored = tree.restore(batch);
  assert.equal(restored.length, 2);
  assert.equal(tree.get(c.id).parentId, null);
  assert.deepEqual(tree.get(c.id).due, { mode: 'none' });
  assert.equal(tree.get(c.id).completed, false);
  assert.equal(tree.get(g.id).completed, true);
  const t2 = new TodoTree();
  const root = t2.create({ title: 'r' });
  const child = t2.create({ title: 'child', parentId: root.id });
  const trash = t2.trash(child.id, child.revision);
  complete(t2, root.id);
  t2.restore(trash);
  assert.equal(t2.get(root.id).completed, false);
});

test('structural events reconcile old and new parent chains, never auto-complete empty leaves', () => {
  const tree = new TodoTree();
  const p = tree.create({ title: 'p' });
  const a = tree.create({ title: 'a', parentId: p.id });
  const b = tree.create({ title: 'b', parentId: p.id });
  complete(tree, a.id);
  tree.trash(b.id, tree.get(b.id).revision);
  assert.equal(tree.get(p.id).completed, true);
  const q = tree.create({ title: 'q' });
  const incomplete = tree.create({ title: 'incomplete', parentId: q.id });
  tree.move(a.id, q.id, tree.get(a.id).revision);
  assert.equal(tree.get(q.id).completed, false);
  tree.trash(incomplete.id, tree.get(incomplete.id).revision);
  assert.equal(tree.get(q.id).completed, true);
  assert.equal(tree.create({ title: 'leaf' }).completed, false);
  tree.create({ title: 'new', parentId: q.id });
  assert.equal(tree.get(q.id).completed, false);
});

test('snapshot validation, deep cloning and metadata prevent external or stale writes', () => {
  const tree = new TodoTree();
  const p = tree.create({ title: 'p', due: date('2026-10-29') });
  p.title = 'external';
  assert.equal(tree.get(p.id).title, 'p');
  const due = tree.effectiveDue(p.id);
  if (due.deadline?.kind === 'date') due.deadline.date = '2099-01-01';
  assert.deepEqual(tree.effectiveDue(p.id).deadline, date('2026-10-29').value);
  const before = tree.get(p.id);
  const after = tree.update(p.id, { notes: 'n' }, before.revision);
  assert.equal(after.revision, before.revision + 1);
  assert.ok(after.updatedAt > before.updatedAt);
  assert.throws(() => tree.update(p.id, { notes: 'stale' }, before.revision));
  const nodes = tree.list(true);
  assert.throws(() => new TodoTree([nodes[0], nodes[0]]));
  assert.throws(() => new TodoTree([{ ...nodes[0], parentId: nodes[0].id }]));
  assert.throws(() => new TodoTree([{ ...nodes[0], parentId: randomUUID() }]));
  assert.throws(() => new TodoTree([{ ...nodes[0], deletedAt: new Date().toISOString() }]));
  const restored = new TodoTree(nodes);
  nodes[0].title = 'external again';
  assert.equal(restored.get(p.id).title, 'p');
});

test('deep trees are iterative for validation, due resolution, warnings, cascade, trash and restore', () => {
  const nodes: Todo[] = [];
  for (let i = 0; i < 3000; i++) nodes.push({ id: randomUUID(), parentId: i ? nodes[i - 1].id : null,
    title: String(i), notes: '', completed: false, due: i ? { mode: 'inherit' } : date('2026-10-29'),
    createdAt: '2026-10-29T00:00:00.000Z', updatedAt: '2026-10-29T00:00:00.000Z', revision: 1,
    deletedAt: null, trashBatchId: null });
  const tree = new TodoTree(nodes);
  assert.equal(tree.effectiveDue(nodes.at(-1)!.id).sourceId, nodes[0].id);
  assert.deepEqual(tree.warnings(), []);
  complete(tree, nodes[0].id);
  assert.equal(tree.list().every(n => n.completed), true);
  complete(tree, nodes.at(-1)!.id, false);
  assert.equal(tree.get(nodes[0].id).completed, false);
  const batch = tree.trash(nodes[0].id, tree.get(nodes[0].id).revision);
  assert.equal(tree.list().length, 0);
  assert.equal(tree.restore(batch).length, 3000);
  assert.deepEqual(tree.warnings(), []);
  assert.equal(tree.list().length, 3000);
});

test('undefined patches cannot erase required fields; disconnected trash batches are rejected', () => {
  const tree = new TodoTree();
  const a = tree.create({ title: 'a' });
  const b = tree.create({ title: 'b' });
  assert.throws(() => tree.update(a.id, { title: undefined }, a.revision));
  tree.update(a.id, { notes: 'safe', due: undefined }, a.revision);
  assert.deepEqual(tree.get(a.id).due, { mode: 'none' });
  const batch = randomUUID();
  assert.throws(() => new TodoTree([a, b].map(node => ({ ...node, deletedAt: new Date().toISOString(), trashBatchId: batch }))));
});

test('完成时刻记录在显式完成、级联完成与自动完成父项上，重开时清除', () => {
  const tree = new TodoTree([], { cascadeComplete: true, autoCompleteParent: true });
  const parent = tree.create({ title: 'parent' });
  const child = tree.create({ title: 'child', parentId: parent.id });
  const done = complete(tree, parent.id);
  assert.ok(done.completedAt);
  assert.ok(tree.get(child.id).completedAt);
  complete(tree, child.id, false);
  assert.equal(tree.get(child.id).completedAt, null);
  assert.equal(tree.get(parent.id).completed, false);
  assert.equal(tree.get(parent.id).completedAt, null);

  const auto = new TodoTree([], { cascadeComplete: false, autoCompleteParent: true });
  const root = auto.create({ title: 'root' });
  const only = auto.create({ title: 'only', parentId: root.id });
  complete(auto, only.id);
  assert.equal(auto.get(root.id).completed, true);
  assert.ok(auto.get(root.id).completedAt);
});
