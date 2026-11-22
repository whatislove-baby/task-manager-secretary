"""Business tools for the fixed home and user-visible task conversations.

The official Deep Agents graph still owns model/tool execution. These adapters
connect existing mail import, persistent triage and ordinary checkpointed chats.
"""
from copy import deepcopy
from .mail_store import digest

SECRETARY_PROMPT = """你在固定的秘书主对话中。用户说“刷新一下”“有什么要处理的”或询问最新收件时，先调用 refresh_inbox 真正检查已连接邮箱，再据实际结果简短汇总。search_mail 只能查本地，不能代替收取。
重要、需行动、待核实先说；普通通知和疑似广告由收件整理卡折叠保留，不删除。必须说明收取失败、未连接账号和尚未分类的余量。没有新邮件不等于旧事项已完成。需要回看待处理事项时调用 get_secretary_briefing(include_briefed=true)、list_tasks/list_drafts/get_child_tasks，引用实际状态；已汇报、已生成草稿、模型完成、已发送或已提交是不同状态。不能仅根据邮件分类就判定工作已完成。
同一主对话继续处理用户指出的事项，简单回复和待办在这里完成。用户要求实际制作报告、处理复杂附件或明确单独处理时，先 get_child_tasks 查看是否已在处理，再用 create_child_conversation 交给一个可打开的子对话。传准确来源ID、用户要求、已确认约束及不确定事项。不要为仅询问能力或建议创建任务，不在主对话重复执行已分派的工作。工具返回queued只表示排队，不是已完成。
子任务在分派它的主轮结束后由后台继续执行，秘书有独立执行通道，用户可继续询问进度或处理别的事项。主对话不要循环等待它；给出子对话卡即可，用户可随时点开。后续询问结果用 get_child_tasks 查询实际最新进度和产物；running只能说明进行中，不能把已有中间文件当成最终交付。官方内部document子助手与用户可打开的子对话不同。用户纠正垃圾分类时调用 correct_mail_category，只改变本地分类、不删原邮件。
"""


def public_briefing(value):
    items = [dict(mailId=i['localId'], subject=i['subject'], sender=i.get('sender', ''),
                  receivedAt=i.get('receivedAt', ''), category=i['category'],
                  summary=i.get('summary', ''), reason=i.get('reason', ''))
             for i in value.get('items', [])]
    warnings = [e.get('message', '本轮有部分操作未完成') for e in value.get('errors', [])]
    if value.get('pendingClassification'):
        warnings.append(f"还有 {value['pendingClassification']} 封待分类；继续刷新可处理下一批，未把它们当成广告。")
    if (value.get('importReport') or {}).get('limited'):
        warnings.append('本轮收取达到分页上限，仍有邮件待继续收取。')
    if value.get('hasMore'):
        warnings.append(f"本卡展示 {len(items)} 封，共 {value.get('total', len(items))} 封尚待汇报；可继续询问或刷新查看余下邮件。")
    return dict(id=value['executionId'], createdAt=value.get('createdAt'),
                counts={c:sum(i['category'] == c for i in items)
                        for c in ('action', 'important', 'normal', 'spam', 'review')},
                items=items, warning='；'.join(dict.fromkeys(warnings)))


class SecretaryAgentTools:
    def refresh_inbox(self):
        value = self.service.secretary.refresh(self.execution_id,
                    check=lambda: self.service.agent._check(self.execution_id))
        for item in value.get('items', []):
            self._source('mail', item['localId'], item['subject'], item.get('summary', ''))
        if value.get('status') != 'busy':
            self._artifact('briefing', self.execution_id, '本次收件整理')
        return value

    def get_secretary_briefing(self, include_briefed=True, limit=30, offset=0):
        value = self.service.secretary.get_briefing(include_briefed=include_briefed, limit=limit, offset=offset)
        for item in value.get('items', []):
            self._source('mail', item['localId'], item['subject'], item.get('summary', ''))
        return {**value, 'scope':'本地分类和汇报记录；briefedAt只表示已在汇总中展示，不代表已处理。具体进度还须查任务、草稿与子对话。'}

    def correct_mail_category(self, message_id, category, reason=''):
        value = self.service.secretary.correct_category(message_id, category, reason)
        mail = self.service.mail.store.get_local(message_id)
        self._source('mail', message_id, mail['subject'], mail.get('preview', ''))
        return value

    def create_child_conversation(self, title, goal, mail_ids=None, file_ids=None, *, call_id):
        if not self.conversation_id:
            raise ValueError('此操作需要秘书主对话')
        arguments = dict(parentConversationId=self.conversation_id, title=title, goal=goal,
                         mailIds=mail_ids or [], fileIds=file_ids or [])
        def create(stable, created_at):
            # Also reuse a pre-upgrade call-ID journal entry in this execution.
            # A crash after the child commit but before the journal result is
            # filled must recover that same child, not enqueue another copy.
            earlier = self.db.execute('SELECT id FROM agent_tool_results WHERE execution_id=? '
                'AND operation=? AND request_hash=? ORDER BY rowid LIMIT 1',
                (self.execution_id, 'create_child_conversation', digest(arguments))).fetchone()
            if earlier:
                stable = earlier['id']
            child = self.service.agent.create_child(arguments, stable_id=stable, source_run_id=self.execution_id)
            return dict(id=child['id'])
        # Models may repeat identical delegation with a NEW tool_call_id. Only
        # this parent execution deduplicates; a later user turn can request a redo.
        saved = self._once('create_child_conversation', 'child:'+digest(arguments), arguments, create)
        child = self.service.agent.call('getConversation', saved['id'])
        self._artifact('conversation', child['id'], child['title'])
        return dict(id=child['id'], title=child['title'], goal=child['goal'],
                    parentConversationId=child['parentConversationId'],
                    status=(child.get('activeRun') or child.get('lastRun') or {}).get('status'),
                    notice='子任务已持久保存；当前主轮完成后后台继续。排队不代表文件已完成，更不代表已发送。请用卡片打开，不重复执行。')

    def get_child_tasks(self, conversation_id='', offset=0, limit=20):
        from .agent_tools import _page
        offset, limit = _page(offset, limit, 50)
        parent = self.service.agent._secretary()
        children = self.service.agent._children(parent['id'])
        if conversation_id:
            children = [c for c in children if c['id'] == conversation_id]
            if not children:
                raise ValueError('未找到这个秘书子任务，请先查看已有任务列表')
        items = []
        for summary in children[offset:offset+limit]:
            child = self.service.agent.call('getConversation', summary['id'])
            run = child.get('activeRun') or child.get('lastRun') or {}
            last_answer = next((t for t in reversed(child['turns']) if t['role'] == 'assistant'), {})
            current_answer = last_answer.get('runId') == run.get('id') and run.get('status') == 'completed'
            item = {**summary, 'goal':child['goal'], 'mailIds':child.get('mailIds', []),
                    'fileIds':child.get('fileIds', []), 'currentRunId':run.get('id'),
                    'answer':last_answer.get('content', '')[:6000] if current_answer else '',
                    'answerRunId':last_answer.get('runId') if current_answer else None,
                    'previousAnswer':last_answer.get('content', '')[:6000] if not current_answer else '',
                    'previousAnswerRunId':last_answer.get('runId') if not current_answer else None,
                    'clarification':run.get('clarification'), 'error':run.get('error'),
                    'artifacts':deepcopy(run.get('artifacts', []))}
            items.append(item)
            self._artifact('conversation', child['id'], child['title'])
            for source in run.get('sources', []):
                self._source(source['kind'], source['id'], source['title'], source.get('excerpt', ''))
        return dict(items=items, total=len(children), offset=offset,
                    nextOffset=offset+len(items) if offset+len(items)<len(children) else None,
                    notice='status是当前轮Agent执行状态，不是业务完成状态。previousAnswer仅是上一版历史回复，不可用于宣称本轮已完成。completed只表示这一轮结束；待办、附件、发送与审核状态请读取当前实际记录。')
