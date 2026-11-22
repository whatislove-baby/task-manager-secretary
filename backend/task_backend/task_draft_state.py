"""Read-only task-draft projection: immutable proposal != current task state.

Lifecycle owns version eligibility; mail analysis owns reviewed-item mappings;
Todo owns present task fields. No read updates one store to resemble another.
"""
from copy import deepcopy
import json

from .tree import instant


def save_progress(analysis):
    ids = [item['tempId'] for item in analysis.get('drafts', [])]
    added = set(analysis.get('addedTempIds', [])) & set(ids)
    # Older fully-saved analyses can predate per-item mappings. Keep them saved
    # and report unknown linkage rather than invent IDs or recreate tasks.
    saved = bool(analysis.get('saved')) or bool(ids) and added == set(ids)
    pending = [] if saved else [id for id in ids if id not in added]
    return dict(state='saved' if saved else 'partially_saved' if added else 'not_saved',
                totalCount=len(ids), savedCount=len(ids) if saved else len(added),
                savedTempIds=ids if saved else [id for id in ids if id in added],
                pendingTempIds=pending)


def is_pending_draft(value):
    if value.get('kind') == 'task_draft':
        return value.get('isPending', False)
    return value.get('lifecycleStatus') == 'active'


def _current_tasks(service, analysis, progress):
    mappings = analysis.get('addedTodos', {})
    tree = service.repo._tree()
    result = []
    for temp in progress['savedTempIds']:
        id = mappings.get(temp)
        item = dict(tempId=temp, taskId=id, state='unlinked' if not id else 'missing', task=None)
        if id:
            try:
                task = tree.get(id)
            except ValueError:
                result.append(item)
                continue
            item['task'] = {k: task[k] for k in ('id', 'title', 'due', 'revision', 'updatedAt',
                'completed', 'completedAt', 'deletedAt', 'trashBatchId', 'parentId')}
            item['state'] = 'trashed' if task['deletedAt'] else 'completed' if task['completed'] else 'active'
            if task['deletedAt']:
                rows = service.repo.db.execute("SELECT payload FROM task_changes.proposals "
                    "WHERE json_extract(payload,'$.taskId')=? AND json_extract(payload,'$.action')='cancel' "
                    "AND json_extract(payload,'$.status')='approved'", (id,))
                for row in rows:
                    change = json.loads(row['payload'])
                    # A restored and later manually trashed task is not still
                    # cancelled by the older, unrelated deletion batch.
                    if change.get('trashBatchId') == task['trashBatchId']:
                        item.update(state='cancelled', cancellationChangeId=change['id'])
                        break
        result.append(item)
    return result


def project_task_draft(service, record, analysis=None):
    """Return live review/task facts without mutating record or its version row.

    Caller holds the draft-family lock when a tracked lifecycle exists. Current
    Todo rows and cancellation receipts are read within one repository snapshot.
    A later write still must check the actual revision at the approval boundary.
    """
    analysis = deepcopy(analysis if analysis is not None else service.mail.store.get_analysis(record['id']))
    progress = save_progress(analysis)
    stored = record.get('lifecycleStatus', 'active')
    eligible = stored in ('active', 'saving', 'saved')
    if not eligible:
        status = stored
        lifecycle = stored
    elif progress['state'] == 'saved':
        status = lifecycle = 'saved'
    elif progress['state'] == 'partially_saved':
        status, lifecycle = 'partially_saved', 'saving'
    elif stored == 'saved':
        status, lifecycle = 'state_conflict', 'saved'
    else:
        status = 'saving' if stored == 'saving' else 'pending_confirmation'
        lifecycle = stored
    pending = eligible and bool(progress['pendingTempIds']) and status != 'state_conflict'
    labels = {
        'saved': '本草稿已保存为正式任务；当前日期、完成及取消情况见 currentTasks，不再是待确认事项。',
        'partially_saved': '本草稿仅部分保存；已保存项见 currentTasks，剩余待确认项见 saveProgress.pendingTempIds。',
        'pending_confirmation': '尚有未保存项，须由用户审核后加入本地任务；读取不会自动保存。',
        'saving': '保存尚未全部确认完成；可重试原审核请求以幂等恢复，不应另建重复任务。',
        'state_conflict': '历史生命周期与保存记录不一致；无法确认当前关联，禁止猜测或重复创建。',
        'superseded': '这是已被替代的历史草稿，不可保存；请读取 supersededBy 指向的版本。',
        'dismissed': '这是已撤回的历史草稿，不再等待确认，也不可重新保存。',
    }
    detail = labels.get(status, '此历史版本不可作为当前待确认事项；请核对生命周期。')
    # Do not echo creation-time boilerplate as current instructions/status.
    analysis.update(saved=progress['state'] == 'saved', notes=detail,
        instructions='原始提议内容及说明见 originalProposal；保存进度见 saveProgress，实际任务见 currentTasks。')
    connection = service.repo.db
    own_transaction = not connection.in_transaction
    if own_transaction:
        connection.execute('BEGIN')
    try:
        tasks = _current_tasks(service, analysis, progress)
    finally:
        if own_transaction:
            connection.execute('ROLLBACK')  # Close the read snapshot; never write.
    original = deepcopy(record)
    for key in ('familyId', 'revision', 'lifecycleStatus', 'supersededBy'):
        original.pop(key, None)
    return dict(deepcopy(record), originalProposal=original, analysis=analysis, status=status,
        lifecycleStatus=lifecycle, storedLifecycleStatus=stored, isPending=pending,
        saveProgress=progress, currentTasks=tasks, detail=detail, observedAt=instant(),
        stateScope='应用内记录；未完成仅表示软件未标记，不能证明现实中未做或已做。')
