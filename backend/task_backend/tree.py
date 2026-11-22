"""Validated, iterative task-tree rules. No desktop or persistence dependencies."""
from copy import deepcopy
from datetime import date, datetime, timedelta, timezone
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
import re

DEFAULT_CONFIG = {'cascadeComplete': True, 'autoCompleteParent': True}


def instant():
    return datetime.now(timezone.utc).isoformat(timespec='milliseconds').replace('+00:00', 'Z')


def parse_instant(value):
    if not isinstance(value, str):
        raise ValueError('无效时间')
    try:
        result = datetime.fromisoformat(value.replace('Z', '+00:00'))
        if result.tzinfo is None:
            raise ValueError()
        return result
    except (ValueError, TypeError):
        raise ValueError('无效时间') from None


def identifier(value):
    try:
        if not isinstance(value, str) or str(UUID(value)) != value.lower():
            raise ValueError()
    except (ValueError, TypeError, AttributeError):
        raise ValueError('无效任务标识') from None
    return value


def calendar(value):
    if not isinstance(value, str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}', value):
        raise ValueError('无效日期')
    date.fromisoformat(value)
    return value


def strict(value, allowed, required=()):
    if not isinstance(value, dict) or set(value) - set(allowed) or set(required) - set(value):
        raise ValueError('输入字段不正确')


def config_value(value):
    strict(value, DEFAULT_CONFIG, DEFAULT_CONFIG)
    if any(type(v) is not bool for v in value.values()):
        raise ValueError('联动配置必须是布尔值')
    return deepcopy(value)


def due_value(value):
    strict(value, ('mode', 'value'), ('mode',))
    mode = value['mode']
    if mode in ('none', 'inherit') and set(value) == {'mode'}:
        return deepcopy(value)
    if mode != 'value' or 'value' not in value:
        raise ValueError('无效截止方式')
    deadline = value['value']
    if not isinstance(deadline, dict):
        raise ValueError('无效截止值')
    if deadline.get('kind') == 'date':
        strict(deadline, ('kind', 'date'), ('kind', 'date'))
        calendar(deadline['date'])
    elif deadline.get('kind') == 'minute':
        strict(deadline, ('kind', 'at', 'timeZone'), ('kind', 'at', 'timeZone'))
        at = parse_instant(deadline['at'])
        if at.second or at.microsecond:
            raise ValueError('时间只精确到分钟')
        try:
            ZoneInfo(deadline['timeZone'])
        except (ZoneInfoNotFoundError, TypeError, ValueError):
            raise ValueError('无效时区（Windows 运行时需包含 tzdata）') from None
        deadline = {**deadline, 'at': at.astimezone(timezone.utc).isoformat(timespec='milliseconds').replace('+00:00', 'Z')}
    else:
        raise ValueError('无效截止精度')
    return {'mode': 'value', 'value': deepcopy(deadline)}


def input_value(value, patch=False):
    strict(value, ('title', 'notes', 'due') if patch else ('title', 'notes', 'due', 'parentId'), () if patch else ('title',))
    if patch and not value:
        raise ValueError('没有修改内容')
    result = deepcopy(value)
    if 'title' in result:
        if not isinstance(result['title'], str) or not 1 <= len(result['title'].strip()) <= 200:
            raise ValueError('标题不能为空，最多200字')
        result['title'] = result['title'].strip()
    if 'notes' in result and (not isinstance(result['notes'], str) or len(result['notes']) > 10000):
        raise ValueError('备注最多10000字')
    if 'due' in result:
        result['due'] = due_value(result['due'])
    if result.get('parentId') is not None:
        identifier(result['parentId'])
    return result


def deadline_day(value):
    return value['date'] if value['kind'] == 'date' else parse_instant(value['at']).astimezone().date().isoformat()


class TodoTree:
    def __init__(self, todos=(), config=None):
        self.config = config_value(DEFAULT_CONFIG if config is None else config)
        self.nodes = {}
        self.due_cache = {}
        self.children = {}
        fields = ('id', 'parentId', 'title', 'notes', 'completed', 'due', 'completedAt', 'createdAt', 'updatedAt', 'revision', 'deletedAt', 'trashBatchId')
        for value in todos:
            strict(value, fields, set(fields) - {'completedAt'})
            node = deepcopy(value)
            identifier(node['id'])
            input_value({k: node[k] for k in ('title', 'notes', 'due', 'parentId')})
            node['due'] = due_value(node['due'])
            if type(node['completed']) is not bool or type(node['revision']) is not int or node['revision'] < 1:
                raise ValueError('任务状态或版本无效')
            for key in ('createdAt', 'updatedAt'):
                parse_instant(node[key])
            for key in ('deletedAt', 'completedAt'):
                if node.get(key) is not None:
                    parse_instant(node[key])
            if node.get('completedAt') and not node['completed']:
                raise ValueError('完成时间必须对应已完成状态')
            if node['trashBatchId'] is not None:
                identifier(node['trashBatchId'])
            if (node['deletedAt'] is None) != (node['trashBatchId'] is None):
                raise ValueError('回收状态与批次不一致')
            if node['parentId'] is None and node['due']['mode'] == 'inherit':
                raise ValueError('根待办不能继承截止时间')
            if node['id'] in self.nodes:
                raise ValueError('重复的待办 ID')
            self.nodes[node['id']] = node
            self._link(node['parentId'], node['id'])
        done, batches = set(), {}
        for node in self.nodes.values():
            parent = self.nodes.get(node['parentId'])
            if not node['deletedAt'] and node['parentId'] and (not parent or parent['deletedAt']):
                raise ValueError('有效待办的父项不存在或已回收')
            path, current = set(), node
            while current and current['id'] not in done:
                if current['id'] in path:
                    raise ValueError('待办树存在循环')
                path.add(current['id'])
                current = self.nodes.get(current['parentId'])
            done.update(path)
            batch = node['trashBatchId']
            if batch and (not parent or parent['trashBatchId'] != batch):
                batches[batch] = batches.get(batch, 0) + 1
        if any(n != 1 for n in batches.values()):
            raise ValueError('回收批次必须是一棵连续子树')

    def _link(self, parent, child):
        if parent is not None:
            self.children.setdefault(parent, {})[child] = None

    def _require(self, id, active=False, revision=None):
        identifier(id)
        if id not in self.nodes:
            raise ValueError('待办不存在')
        node = self.nodes[id]
        if active and node['deletedAt']:
            raise ValueError('待办已在回收站')
        if revision is not None and (type(revision) is not int or revision != node['revision']):
            raise ValueError('版本冲突：待办已被修改，请刷新后重试')
        return node

    def _editable(self, id, revision):
        if type(revision) is not int or revision < 1:
            raise ValueError('版本号必须为正整数')
        return self._require(id, True, revision)

    def get(self, id):
        return deepcopy(self._require(id))

    def list(self, include_deleted=False):
        return deepcopy(sorted((n for n in self.nodes.values() if include_deleted or not n['deletedAt']), key=lambda n: parse_instant(n['createdAt'])))

    def set_config(self, value):
        self.config = config_value(value)

    def _finish(self, touched):
        self.due_cache.clear()
        now = datetime.now(timezone.utc)
        for id in touched:
            node = self.nodes[id]
            node['revision'] += 1
            node['updatedAt'] = max(now, parse_instant(node['updatedAt']) + timedelta(milliseconds=1)).isoformat(timespec='milliseconds').replace('+00:00', 'Z')

    def _reconcile(self, parent, touched):
        if not self.config['autoCompleteParent']:
            return
        while parent is not None:
            node = self.nodes.get(parent)
            if not node or node['deletedAt']:
                break
            children = [self.nodes[id] for id in self.children.get(parent, {}) if not self.nodes[id]['deletedAt']]
            complete = all(c['completed'] for c in children)
            if children and node['completed'] != complete:
                node['completed'] = complete
                node['completedAt'] = instant() if complete else None
                touched.add(parent)
            parent = node['parentId']

    def create(self, value, id=None):
        value = input_value(value)
        id = identifier(str(uuid4()) if id is None else id)
        if id in self.nodes:
            raise ValueError('待办标识已存在')
        parent = value.get('parentId')
        if parent is not None:
            self._require(parent, True)
        due = value.get('due', {'mode': 'inherit' if parent is not None else 'none'})
        if parent is None and due['mode'] == 'inherit':
            raise ValueError('根待办不能继承截止时间')
        now = instant()
        node = dict(id=id, parentId=parent, title=value['title'], notes=value.get('notes', ''), completed=False, completedAt=None, due=due, createdAt=now, updatedAt=now, revision=1, deletedAt=None, trashBatchId=None)
        self.nodes[id] = node
        self._link(parent, id)
        touched = set()
        self._reconcile(parent, touched)
        self._finish(touched)
        return deepcopy(node)

    def update(self, id, patch, revision):
        node = self._editable(id, revision)
        patch = input_value(patch, True)
        if node['parentId'] is None and patch.get('due', {}).get('mode') == 'inherit':
            raise ValueError('根待办不能继承截止时间')
        node.update(patch)
        self._finish({id})
        return deepcopy(node)

    def move(self, id, parent, revision):
        node = self._editable(id, revision)
        current = self._require(parent, True) if parent is not None else None
        while current:
            if current['id'] == id:
                raise ValueError('不能移动到自身或自己的后代')
            current = self.nodes.get(current['parentId'])
        old_parent = node['parentId']
        self.children.get(old_parent, {}).pop(id, None)
        node['parentId'] = parent
        self._link(parent, id)
        if parent is None and node['due']['mode'] == 'inherit':
            node['due'] = {'mode': 'none'}
        touched = {id}
        self._reconcile(old_parent, touched)
        self._reconcile(parent, touched)
        self._finish(touched)
        return deepcopy(node)

    def _subtree(self, id):
        result, stack = [], [id]
        while stack:
            node = self.nodes[stack.pop()]
            if node['deletedAt']:
                continue
            result.append(node)
            stack.extend(self.children.get(node['id'], {}))
        return result

    def set_completed(self, id, completed, revision):
        node = self._editable(id, revision)
        if type(completed) is not bool:
            raise ValueError('完成状态必须是布尔值')
        touched, at = {id}, instant()
        node['completed'], node['completedAt'] = completed, at if completed else None
        if completed and self.config['cascadeComplete']:
            for item in self._subtree(id):
                if not item['completed']:
                    item['completed'], item['completedAt'] = True, at
                    touched.add(item['id'])
        self._reconcile(node['parentId'], touched)
        self._finish(touched)
        return deepcopy(node)

    def trash(self, id, revision):
        node = self._editable(id, revision)
        batch, at, touched = str(uuid4()), instant(), set()
        for item in self._subtree(id):
            item['deletedAt'], item['trashBatchId'] = at, batch
            touched.add(item['id'])
        self._reconcile(node['parentId'], touched)
        self._finish(touched)
        return batch

    def restore(self, batch):
        identifier(batch)
        nodes = [n for n in self.nodes.values() if n['deletedAt'] and n['trashBatchId'] == batch]
        members, external, touched = {n['id'] for n in nodes}, set(), set()
        for node in nodes:
            parent = node['parentId']
            if parent is not None and parent not in members:
                p = self.nodes.get(parent)
                if p and not p['deletedAt']:
                    external.add(parent)
                else:
                    self.children.get(parent, {}).pop(node['id'], None)
                    node['parentId'] = None
                    if node['due']['mode'] == 'inherit':
                        node['due'] = {'mode': 'none'}
            node['deletedAt'] = node['trashBatchId'] = None
            touched.add(node['id'])
        for parent in external:
            self._reconcile(parent, touched)
        self._finish(touched)
        return deepcopy(nodes)

    def effective_due(self, id):
        current = self._require(id)
        pending = []
        while current['id'] not in self.due_cache:
            pending.append(current)
            if current['due']['mode'] != 'inherit' or current['parentId'] not in self.nodes:
                break
            current = self.nodes[current['parentId']]
        for node in reversed(pending):
            mode = node['due']['mode']
            own = node['due'].get('value')
            parent = self.due_cache.get(node['parentId'], {})
            deadline = parent.get('deadline') if mode == 'inherit' else own
            source = parent.get('sourceId') if mode == 'inherit' else node['id'] if own else None
            self.due_cache[node['id']] = dict(deadline=deadline, displayedDue=own, inherited=mode == 'inherit', sourceId=source)
        return deepcopy(self.due_cache[id])

    def warnings(self):
        result = []
        for node in self.nodes.values():
            if node['deletedAt'] or node['parentId'] is None:
                continue
            own = self.effective_due(node['id'])['deadline']
            parent = self.effective_due(node['parentId'])['deadline']
            if not own or not parent:
                continue
            later = parse_instant(own['at']) > parse_instant(parent['at']) if own['kind'] == parent['kind'] == 'minute' else deadline_day(own) > deadline_day(parent)
            if later:
                result.append(dict(todoId=node['id'], parentId=node['parentId'], code='AFTER_PARENT', message='子待办截止时间晚于父待办，仍可保存。'))
        return result
