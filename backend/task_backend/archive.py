"""Draft confirmation and versioned backup compatibility."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import re
from urllib.parse import urlsplit
from .tree import TodoTree, strict, identifier, input_value, calendar, config_value, instant, parse_instant

MAX_BACKUP=64*1024*1024


def text(value, limit, nonempty=False):
    if not isinstance(value,str) or len(value.encode('utf-16-le'))//2>limit or (nonempty and not value):
        raise ValueError('文本字段无效或超过长度限制')
    return value


def drafts_value(items, allow_empty=False):
    if not isinstance(items,list) or len(items)>100 or (not items and not allow_empty):
        raise ValueError('请选择1至100项草稿')
    parsed=[]
    for item in items:
        strict(item,('title','notes','due','tempId','parentTempId','source','sourceMailIds','todoId','parentTodoId'),('title','tempId','parentTempId'))
        # Preserve the original Zod field order for existing SHA256 receipts.
        value=input_value({k:item[k] for k in ('title','notes','due') if k in item})
        value['tempId']=text(item['tempId'],100,True)
        value['parentTempId']=None if item['parentTempId'] is None else text(item['parentTempId'],100,True)
        value['source']=text(item.get('source',''),2000)
        if 'sourceMailIds' in item:
            if not isinstance(item['sourceMailIds'],list) or len(item['sourceMailIds'])>20: raise ValueError('无效邮件来源')
            value['sourceMailIds']=[text(v,2048,True) for v in item['sourceMailIds']]
        for key in ('todoId','parentTodoId'):
            if key in item: value[key]=identifier(item[key])
        parsed.append(value)
    by_id={v['tempId']:v for v in parsed}
    if len(by_id)!=len(parsed): raise ValueError('草稿存在重复标识')
    ordered=[]; children={}
    for value in parsed:
        parent=value['parentTempId']
        if parent is None or (parent not in by_id and value.get('parentTodoId')):
            ordered.append(value)
        elif parent not in by_id: raise ValueError('草稿引用了未选择或不存在的父项')
        else: children.setdefault(parent,[]).append(value)
    index=0
    while index<len(ordered):
        ordered.extend(children.get(ordered[index]['tempId'],[]));index+=1
    if len(ordered)!=len(parsed): raise ValueError('草稿层级存在循环')
    return ordered


def save_drafts(repo, items, token):
    parsed=drafts_value(items)
    text(token,200,True)
    if not token.strip(): raise ValueError('无效确认令牌')
    fingerprint=hashlib.sha256(json.dumps(parsed,ensure_ascii=False,separators=(',',':')).encode('utf-8')).hexdigest()
    def action(tree):
        seen=repo.db.execute('SELECT fingerprint,count FROM draft_confirmations WHERE token=?',(token,)).fetchone()
        if seen:
            if seen['fingerprint']!=fingerprint: raise ValueError('该确认令牌已用于不同草稿，请重新确认')
            return seen['count']
        real={}
        for value in parsed:
            parent=None if value['parentTempId'] is None else real.get(value['parentTempId'],value.get('parentTodoId'))
            if value['parentTempId'] is not None and parent is None: raise ValueError('草稿父项尚未保存')
            data={k:value[k] for k in ('title','notes','due') if k in value}
            data['parentId']=parent
            real[value['tempId']]=tree.create(data,value.get('todoId'))['id']
        repo.db.execute('INSERT INTO draft_confirmations VALUES (?,?,?)',(token,fingerprint,len(parsed)))
        return len(parsed)
    return repo._write(action)


def source_value(value, with_content=True):
    fields=('sourceId','key','todoId','description','url') if with_content else ('sourceId','key','todoId')
    strict(value,fields,fields)
    if not isinstance(value['sourceId'],str) or not re.fullmatch('[a-f0-9]{64}',value['sourceId']): raise ValueError('无效Canvas来源标识')
    text(value['key'],1000,True);identifier(value['todoId'])
    result=deepcopy(value)
    if not with_content: result.update(description='',url='')
    text(result['description'],5*1024*1024);url=text(result['url'],8000)
    if url:
        parsed=urlsplit(url)
        if parsed.scheme not in ('http','https') or not parsed.hostname or parsed.username or parsed.password: raise ValueError('无效Canvas原文地址')
    return result


def validate_relations(orders,mappings,tree):
    nodes={v['id']:v for v in tree.list(True)}
    roots={id for id,v in nodes.items() if v['parentId'] is None}
    days=set()
    for order in orders:
        strict(order,('day','ids'),('day','ids'));calendar(order['day'])
        ids=order['ids']
        if not isinstance(ids,list) or any(not isinstance(v,str) for v in ids) or len(ids)!=len(set(ids)) or not set(ids)<=roots or order['day'] in days: raise ValueError('备份排序无效')
        days.add(order['day'])
    keys=set();targets=set()
    for value in mappings:
        pair=(value['sourceId'],value['key'])
        if pair in keys or value['todoId'] in targets or value['todoId'] not in nodes: raise ValueError('Canvas来源映射无效')
        keys.add(pair);targets.add(value['todoId'])


def export_backup(repo):
    repo.db.execute('BEGIN')
    try:
        tree=repo._tree()
        orders=[dict(day=r['day'],ids=json.loads(r['value'])) for r in repo.db.execute('SELECT day,value FROM day_orders ORDER BY day')]
        mappings=[source_value(dict(sourceId=r['source_id'],key=r['item_key'],todoId=r['todo_id'],description=r['description'],url=r['url'])) for r in repo.db.execute('SELECT * FROM canvas_items ORDER BY source_id,item_key')]
        validate_relations(orders,mappings,tree)
        result=dict(version=6,dayOrders=orders,canvasItems=mappings,exportedAt=instant(),todos=tree.list(True),completionConfig=tree.config)
        repo.db.execute('COMMIT')
        return result
    except BaseException:
        repo.db.execute('ROLLBACK');raise


def import_backup(repo,value):
    if not isinstance(value,dict) or type(value.get('version')) is not int or value['version'] not in (2,3,4,5,6): raise ValueError('不支持的备份版本')
    version=value['version']
    fields=['version','exportedAt','todos','completionConfig']+(['dayOrders'] if version>=3 else [])+(['canvasItems'] if version>=4 else [])
    strict(value,fields,fields);parse_instant(value['exportedAt'])
    if not isinstance(value['todos'],list): raise ValueError('无效任务列表')
    tree=TodoTree(value['todos'],value['completionConfig'])
    orders=value.get('dayOrders',[]);raw=value.get('canvasItems',[])
    if not isinstance(orders,list) or not isinstance(raw,list): raise ValueError('无效备份列表')
    mappings=[source_value(v,version>=5) for v in raw]
    validate_relations(orders,mappings,tree)
    repo.db.execute('BEGIN IMMEDIATE')
    try:
        if any(repo.db.execute('SELECT 1 FROM '+table+' LIMIT 1').fetchone() for table in ('todos','draft_confirmations','day_orders','canvas_items')): raise ValueError('备份只能导入全新空数据库，原数据库未修改')
        repo._persist(tree,{})
        for order in orders: repo.db.execute('INSERT INTO day_orders VALUES (?,?)',(order['day'],json.dumps(order['ids'])))
        for v in mappings: repo.db.execute('INSERT INTO canvas_items VALUES (?,?,?,?,?)',(v['sourceId'],v['key'],v['todoId'],v['description'],v['url']))
        result=dict(value=len(value['todos']),warnings=tree.warnings())
        repo.db.execute('COMMIT');return result
    except BaseException:
        repo.db.execute('ROLLBACK');raise


def backup_file(repo, path, write=False):
    path=Path(path)
    if write:
        value=json.dumps(export_backup(repo),ensure_ascii=False,indent=2).encode('utf-8')
        if len(value)>MAX_BACKUP: raise ValueError('备份超过64MB，请完整备份data文件夹')
        temp=path.with_name(path.name+'.tmp')
        try:
            temp.write_bytes(value);temp.replace(path)
        finally:
            if temp.exists(): temp.unlink()
    else:
        with path.open('rb') as stream: content=stream.read(MAX_BACKUP+1)
        if len(content)>MAX_BACKUP: raise ValueError('备份超过64MB')
        try: value=json.loads(content.decode('utf-8-sig'))
        except (ValueError,UnicodeError): raise ValueError('备份不是有效JSON') from None
        import_backup(repo,value)
    return True
