"""Deterministic delivery inspection hosted by the official middleware hooks.

This checks persisted objects, not the truth of a model's prose. Unattached
files are candidates for review, never an instruction to attach them to a
guessed customer. The framework owns the bounded correction and checkpoints.
"""
from __future__ import annotations

import json
from typing import NotRequired

from deepagents.graph import DeepAgentState
from langchain.agents.middleware import AgentMiddleware, hook_config
from langchain_core.messages import AIMessage, HumanMessage, RemoveMessage

from .mail_store import compact, digest


class DeliveryReviewState(DeepAgentState):
    delivery_review_run: NotRequired[str]
    delivery_review_count: NotRequired[int]
    delivery_review_fingerprint: NotRequired[str]


def delivery_snapshot(ctx):
    """Only inspect this conversation's durable writes; never fetch remote mail."""
    rows = ctx.db.execute(
        'SELECT id,execution_id,operation,result FROM agent_tool_results '
        'WHERE conversation_id=? AND result IS NOT NULL ORDER BY created_at,id',
        (ctx.conversation_id,)).fetchall()
    writes = [r for r in rows if r['execution_id'] == ctx.execution_id
              and r['operation'] not in ('import_attachment', 'extract_archive')]
    # Skill execution deliberately bypasses the idempotence journal so revoked
    # approval is rechecked on replay. Its durable artifact events still bind
    # output IDs to this conversation; include those without weakening approval.
    runs = ctx.service.agent._runs(ctx.conversation_id) if ctx.conversation_id else []
    artifact_ids = {a['id'] for run in runs for a in run.get('artifacts', []) if a['kind'] == 'file'}
    current_outputs = any(run['id'] == ctx.execution_id and any(a['kind'] == 'file'
                          for a in run.get('artifacts', [])) for run in runs)
    if not writes and not current_outputs:
        return None
    drafts, files = {}, {}
    for row in rows:
        result = json.loads(row['result'])
        if result.get('kind') == 'mail_draft':
            value = ctx.read_draft(result['id'])
            if value['lifecycleStatus'] == 'active':
                drafts[value['id']] = value
        if result.get('kind') == 'file':
            artifact_ids.add(result['id'])
    for fid in artifact_ids:
        value = ctx.service.files.get(fid)
        if value.get('source') == 'generated' and value.get('lifecycleStatus', 'active') == 'active':
            files[value['id']] = value
    if not drafts:
        return None  # File-only tasks do not need a mail draft.
    attached = {fid for draft in drafts.values() for fid in draft.get('attachmentIds', [])}
    invalid = []
    for draft in drafts.values():
        proof = ctx.validate_mail_draft(draft['id'])
        if not proof['passed']:
            invalid.append(proof)
    # All candidates are explicit IDs, not inferred associations. No filename,
    # sender, order-number heuristic is permitted to choose a recipient.
    unlinked = [dict(id=f['id'], name=f['name'], parentIds=f.get('parentIds', []))
                for f in files.values() if f['id'] not in attached]
    if not invalid and not unlinked:
        return None
    return dict(invalidDrafts=invalid, unattachedGeneratedFiles=unlinked,
                currentDrafts=[dict(id=d['id'], revision=d['revision'], subject=d['subject'],
                                    to=d.get('to', ''), replyToLocalId=d.get('replyToLocalId', ''),
                                    attachmentIds=d.get('attachmentIds', [])) for d in drafts.values()],
                limitation='未附文件可能是参考稿、挂起工作或独立交付；此检查不推断用户意图或客户对应关系。')


class DeliveryReviewMiddleware(AgentMiddleware):
    """One official graph correction per turn, then factual visible limitations."""
    state_schema = DeliveryReviewState

    def __init__(self, ctx):
        self.ctx = ctx

    @hook_config(can_jump_to=['model'])
    async def aafter_model(self, state, runtime):
        message = state['messages'][-1]
        if not isinstance(message, AIMessage) or message.tool_calls:
            return None
        snapshot = delivery_snapshot(self.ctx)
        if not snapshot:
            return None
        fingerprint = digest(snapshot)
        count = state.get('delivery_review_count', 0) if state.get('delivery_review_run') == self.ctx.execution_id else 0
        update = dict(delivery_review_run=self.ctx.execution_id, delivery_review_count=count + 1,
                      delivery_review_fingerprint=fingerprint)
        if count == 0:
            self.ctx.on_event(dict(type='status', label='正在核对当前文件与邮件草稿附件'))
            # Remove the rejected provisional answer so later summaries cannot
            # mistake it for a successful delivery. It has no tool calls.
            update.update(jump_to='model', messages=[RemoveMessage(id=message.id), HumanMessage(
                id=self.ctx.execution_id + ':delivery-review',
                content='应用对实际已保存产物的检查（系统自动检查，不是用户新指令或发送授权）：\n'
                + compact(snapshot) + '\n请对照用户本轮目标核对交付：应随邮件交付的文件，用read_draft、replace_mail_draft和'
                'validate_mail_draft(expected_attachment_ids=实际目标文件ID)补齐；不要猜客户或把所有文件塞进一封邮件。'
                '已被新件取代的生成文件，先更新/撤回引用它的草稿，再archive_generated_files；保留原始附件。'
                '独立交付、参考文件或用户挂起的工作可不附，但最终说明该事实。已经明确的字段不要再次询问用户。'
                '不能仅口头声称已附、归档或交付完成，必须以工具读回结果为准。')])
            return update
        # We do not keep self-reviewing indefinitely or pretend to understand a
        # legitimate file-only exception. Preserve the answer and append facts.
        note = '\n\n应用交付核对：'
        if snapshot['invalidDrafts']:
            note += '仍有草稿附件缺失或失效，需要修复后再发送。'
        if snapshot['unattachedGeneratedFiles']:
            note += '以下有效生成文件尚未附到本对话的任何待发草稿：' + '、'.join(
                '[%s](file://%s)' % (f['name'], f['id']) for f in snapshot['unattachedGeneratedFiles']) + '。'
        corrected = message.model_copy(update={'content': message.text + note})
        update['messages'] = [corrected]
        return update
