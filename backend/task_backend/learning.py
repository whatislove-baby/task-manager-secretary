"""Versioned, user-approved local knowledge and bounded table workflows.

The model-facing surface deliberately has no approve/disable operation. A new
mail or a new draft cannot modify an existing version or its approved pointer.
"""
from __future__ import annotations

from decimal import Decimal, localcontext
import hashlib
import json
from pathlib import Path
import re
import sqlite3
from uuid import UUID, uuid4, uuid5, NAMESPACE_URL

from .csv_documents import encode_csv, transform_csv, _numeric, _format_number, _headers, _cell, _formula
from .mail_identity import address
from .spreadsheet_files import read_table, encode_xlsx
from .tree import instant


def compact(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


def digest(value):
    return hashlib.sha256(compact(value).encode('utf-8')).hexdigest()


def string(value, maximum=1000, required=True):
    if not isinstance(value, str) or len(value)>maximum or (required and not value.strip()) or re.search(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]',value):
        raise ValueError('学习内容缺失、过长或含不支持的控制字符')
    return value.strip()


def shape(value, allowed, required=(), *, at='学习规则'):
    if not isinstance(value, dict):
        raise ValueError(at+' 必须是对象；只接受字段：'+', '.join(allowed))
    missing=set(required)-set(value);unknown=set(value)-set(allowed)
    if missing or unknown:
        # Report the exact key path, never echo private values or whole input.
        details=[]
        if missing:details.append('缺少字段：'+', '.join(sorted(str(k) for k in missing)))
        if unknown:details.append('未知字段：'+', '.join(sorted(str(k) for k in unknown)))
        raise ValueError(at+'；'+'；'.join(details)+'。只接受字段：'+', '.join(allowed))


def identifier(value):
    try:return str(UUID(value))
    except (ValueError, TypeError, AttributeError):raise ValueError('学习版本标识无效') from None


def columns(value, allow_empty=False):
    if allow_empty and value==[]:return []
    return list(_headers(value))


def teaching_from_conversation(service, conversation_id=None, run_id=None, *, through_turn_id=None):
    """Read saved user turns, never reconstruct them from a model summary."""
    evidence=dict(source='direct-input',turns=[],omittedUserTurns=0)
    if conversation_id is not None:
        agent=getattr(service,'agent',None)
        if agent is None:raise ValueError('无法核对学习提议的真实对话来源')
        conversation=agent._conversation(conversation_id)
        user_turns=[t for t in conversation.get('turns',[]) if t.get('role')=='user']
        positions=[i for i,t in enumerate(user_turns) if t.get('runId')==run_id]
        if not positions:raise ValueError('学习提议的执行不属于此对话，未保存无法验证的教学原文')
        cutoff=positions[-1]
        if through_turn_id is not None:
            matching=[i for i in positions if user_turns[i]['id']==through_turn_id]
            if not matching:raise ValueError('教学快照的截止消息不属于本次执行')
            cutoff=matching[0]
        candidates=user_turns[:cutoff+1];selected=[];size=0
        for turn in reversed(candidates):
            content=turn.get('content')
            if not isinstance(content,str) or not content or len(content)>64000:raise ValueError('原始教学消息缺失或超过保存限制，未用摘要替代原文')
            if len(selected)>=4 or size+len(content)>128000:break
            size+=len(content)
            selected.append(dict(id=turn['id'],runId=turn.get('runId',''),createdAt=turn.get('createdAt',''),content=content,textSha256=hashlib.sha256(content.encode('utf-8')).hexdigest()))
        evidence=dict(source='conversation',conversationId=conversation_id,runId=run_id,turns=list(reversed(selected)),omittedUserTurns=len(candidates)-len(selected))
    evidence['sourceHash']=digest(evidence)
    return evidence


def normalize_scope(value):
    shape(value, ('kind','label','emails','contextKey'), ('kind','label','emails'), at='scope')
    kind=value['kind']
    if kind not in ('customer','course','personal'):raise ValueError('学习范围类型无效')
    label=string(value['label'],200)
    emails=value['emails']
    if not isinstance(emails,list) or len(emails)>20 or any(not isinstance(v,str) for v in emails):raise ValueError('学习范围须列出精确邮箱地址')
    emails=sorted(set(address(v) for v in emails))
    context=string(value.get('contextKey',''),200,False)
    if kind in ('customer','course') and not emails:raise ValueError('客户或课程方法必须绑定精确邮箱，不能只用显示名')
    if kind=='course' and not context:raise ValueError('课程方法须明确课程代号 contextKey')
    identity=dict(kind=kind,emails=emails,contextKey=context)
    scope_id=kind+'-'+uuid5(NAMESPACE_URL,'task-manager-scope:'+compact(identity)).hex
    return scope_id,dict(identity,label=label)


def normalize_recipe(value):
    allowed=('inputColumns','optionalInputColumns','allowExtraColumns','sheetName','mapping','constants','concat','operations','outputColumns','requiredValues','numericColumns','moneyColumns','totals','preserveRowCount','commercial','outputFormat')
    required=('inputColumns','mapping','outputColumns','requiredValues','numericColumns','outputFormat')
    shape(value,allowed,required,at='recipe')
    result=json.loads(compact(value))
    for key in ('inputColumns','outputColumns'):result[key]=columns(value[key])
    result['optionalInputColumns']=columns(value.get('optionalInputColumns',[]),True)
    if not set(result['optionalInputColumns'])<=set(result['inputColumns']):raise ValueError('允许空值的列必须是已声明的输入列')
    for key in ('requiredValues','numericColumns','moneyColumns','totals'):result[key]=columns(value.get(key,[]),True)
    for key in ('allowExtraColumns','preserveRowCount'):
        result[key]=value.get(key,key=='preserveRowCount')
        if type(result[key])is not bool:raise ValueError('学习规则的布尔参数无效')
    if value['outputFormat'] not in ('csv','xlsx'):raise ValueError('表格规则输出仅支持 CSV 或 XLSX')
    if 'sheetName' in value:result['sheetName']=string(value['sheetName'],31)
    mapping=value['mapping'];constants=value.get('constants',{})
    if not isinstance(mapping,dict) or not mapping or not isinstance(constants,dict):raise ValueError('请提供输出列到输入列的 mapping')
    targets=columns(list(mapping)+list(constants))
    if any(v not in result['inputColumns'] for v in mapping.values()):raise ValueError('映射引用了未声明的输入列')
    for v in constants.values():_cell(v)
    result['constants']=constants
    result['concat']=value.get('concat',[])
    if not isinstance(result['concat'],list) or len(result['concat'])>20:raise ValueError('拼接规则过多或无效')
    for index,spec in enumerate(result['concat']):
        shape(spec,('target','columns','separator','padLast'),('target','columns','separator'),at=f'recipe.concat[{index}]')
        columns([spec['target']]);columns(spec['columns'])
        if spec['target'] in targets or any(c not in result['inputColumns'] for c in spec['columns']):raise ValueError('拼接列重复或输入列未声明')
        string(spec['separator'],20,False)
        if 'padLast' in spec and (type(spec['padLast'])is not int or not 1<=spec['padLast']<=20):raise ValueError('版本补零宽度须为 1 至 20')
        targets.append(spec['target'])
    operations=value.get('operations',[])
    if not isinstance(operations,list) or len(operations)>30:raise ValueError('表格规则最多 30 步')
    # The CSV implementation validates every operation shape during the required
    # example execution. Refuse executable-looking languages before that boundary.
    if any(not isinstance(step,dict) or step.get('op') not in ('drop_duplicates','fill_missing','filter','sort','select','rename','groupby','derive','update') for step in operations):raise ValueError('只支持已定义的表格操作，不能执行代码、网络或 shell')
    result['operations']=operations
    if not set(result['moneyColumns'])<=set(result['numericColumns']):raise ValueError('金额列必须同时声明为 numericColumns')
    if not set(result['totals'])<=set(result['numericColumns']):raise ValueError('合计列必须同时声明为 numericColumns')
    if result['moneyColumns']:
        commercial=value.get('commercial')
        shape(commercial,('currency','taxBasis','unit'),('currency','taxBasis','unit'),at='recipe.commercial')
        if not isinstance(commercial['currency'],str) or not re.fullmatch('[A-Z]{3}',commercial['currency']):raise ValueError('金额处理须明确三位币种代码')
        if commercial['taxBasis'] not in ('inclusive','exclusive','not_applicable'):raise ValueError('含税或未税口径不明确，请向用户澄清')
        string(commercial['unit'],50)
        # Renaming a stated tax basis into the opposite basis is not a conversion.
        for target,source in mapping.items():
            if ('含税' in source and '未税' in target) or ('未税' in source and '含税' in target):raise ValueError('输入输出税口径不一致，不能只改列名；请明确转换规则')
    elif 'commercial' in value:
        raise ValueError('commercial 须同时声明 moneyColumns，避免金额检查被跳过')
    return result


def apply_recipe(data, extension, recipe):
    table=read_table(data,extension,recipe.get('sheetName'))
    actual=table['columns'];expected=recipe['inputColumns']
    if set(expected)-set(actual) or (not recipe['allowExtraColumns'] and set(actual)!=set(expected)):
        raise ValueError('输入布局与已确认方法不符，请澄清新列名并提议新版本；要求：'+', '.join(expected))
    if not table['rows']:raise ValueError('输入表格没有数据行，请澄清后再运行')
    names=list(recipe['mapping'])+list(recipe['constants'])+[s['target'] for s in recipe['concat']]
    rows=[]
    for row_index,row in enumerate(table['rows'],2):
        source=dict(zip(actual,row))
        for column in recipe['inputColumns']:
            if column not in recipe.get('optionalInputColumns',[]) and not source[column].strip():
                raise ValueError(f'输入第 {row_index} 行的 {column} 为空；默认输入列均为必填，请先澄清，不能静默填补')
        output={key:source[val] for key,val in recipe['mapping'].items()}
        output.update({key:_cell(val) for key,val in recipe['constants'].items()})
        for spec in recipe['concat']:
            parts=[source[c] for c in spec['columns']]
            if any(not part.strip() for part in parts):raise ValueError('客户料号或版本存在空值，请先澄清，未自动补版本')
            if 'padLast' in spec:
                if not parts[-1].isdigit() or len(parts[-1])>spec['padLast']:raise ValueError('版本格式不符合已确认的补零规则，请澄清')
                parts[-1]=parts[-1].zfill(spec['padLast'])
            output[spec['target']]=spec['separator'].join(parts)
        for key in recipe['requiredValues']:
            if key in output and not output[key].strip():raise ValueError('必填列 '+key+' 有空值，请先澄清；不会猜测价格或标识')
        for key in recipe['numericColumns']:
            if key in output:_numeric(output[key],key)
        rows.append([output[n] for n in names])
    # Existing bounded CSV transformations supply exact decimal arithmetic.
    warnings=list(table['warnings']);changes=[]
    protected=sum(_formula(value) for row in [names]+rows for value in row)
    if protected and (recipe['operations'] or recipe['outputFormat']=='csv'):
        warnings.append(f'表格安全导出已将 {protected} 个公式样式值加前导单引号；这些值仅作为文字，未执行公式。')
    if recipe['operations']:
        transformed,report=transform_csv(encode_csv(names,rows),recipe['operations'])
        result=read_table(transformed,'csv');names=result['columns'];rows=result['rows']
        warnings+=report['warnings'];changes=report['operations']
    if recipe['preserveRowCount'] and len(rows)!=len(table['rows']):raise ValueError('处理改变了行数，但方法要求保留每一行；请先确认筛选或合并规则')
    required=set(recipe['outputColumns']+recipe['requiredValues']+recipe['numericColumns']+recipe['totals'])
    if required-set(names):raise ValueError('规则结果缺少声明的输出列，请修正方法')
    totals={key:Decimal(0) for key in recipe['totals']};final=[]
    with localcontext() as context:
        context.prec=100
        for row in rows:
            output=dict(zip(names,row))
            for key in recipe['requiredValues']:
                if not output[key].strip():raise ValueError('结果必填列 '+key+' 为空，请澄清')
            for key in recipe['numericColumns']:
                number=_numeric(output[key],key)
                if key in totals:totals[key]+=number
            final.append([output[key] for key in recipe['outputColumns']])
    output=encode_xlsx(recipe['outputColumns'],final) if recipe['outputFormat']=='xlsx' else encode_csv(recipe['outputColumns'],final)
    checked=read_table(output,recipe['outputFormat'])
    if checked['columns']!=recipe['outputColumns'] or len(checked['rows'])!=len(rows):raise ValueError('生成文件回读校验失败')
    sample=[[cell[:240] for cell in row] for row in checked['rows'][:8]]
    return output,dict(columns=checked['columns'],rows=sample,previewTruncated=len(rows)>8 or any(len(cell)>240 for row in checked['rows'][:8] for cell in row),rowCount=len(rows),inputRowCount=len(table['rows']),totals={key:_format_number(v) for key,v in totals.items()},warnings=list(dict.fromkeys(warnings)),changes=changes,outputSha256=hashlib.sha256(output).hexdigest())


class LearningService:
    def __init__(self, service):
        self.service=service
        self.db=sqlite3.connect(Path(service.directory)/'learning.sqlite',isolation_level=None,timeout=10)
        self.db.row_factory=sqlite3.Row
        self.db.executescript('''PRAGMA secure_delete=ON;
            CREATE TABLE IF NOT EXISTS learning_versions(id TEXT PRIMARY KEY, scope_id TEXT NOT NULL, title TEXT NOT NULL, number INTEGER NOT NULL, parent_id TEXT, version_hash TEXT NOT NULL, proposal TEXT NOT NULL, preview TEXT, created_at TEXT NOT NULL, request_hash TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS learning_reviews(version_id TEXT PRIMARY KEY, status TEXT NOT NULL, reviewed_at TEXT);
            CREATE TABLE IF NOT EXISTS learning_active(scope_id TEXT NOT NULL,title TEXT NOT NULL,version_id TEXT NOT NULL, PRIMARY KEY(scope_id,title));
            CREATE TABLE IF NOT EXISTS learning_runs(id TEXT PRIMARY KEY, request_hash TEXT NOT NULL,result TEXT NOT NULL,created_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS learning_teaching_calls(id TEXT PRIMARY KEY,value_hash TEXT NOT NULL,evidence TEXT NOT NULL);
            CREATE UNIQUE INDEX IF NOT EXISTS learning_version_number ON learning_versions(scope_id,title,number);
            CREATE TRIGGER IF NOT EXISTS learning_immutable_update BEFORE UPDATE ON learning_versions BEGIN SELECT RAISE(ABORT,'learning versions are immutable'); END;
            CREATE TRIGGER IF NOT EXISTS learning_immutable_delete BEFORE DELETE ON learning_versions BEGIN SELECT RAISE(ABORT,'learning versions are immutable'); END;
        ''')

    def close(self):self.db.close()

    def teaching_for_call(self,call_id,value,conversation_id=None,run_id=None):
        """Checkpoint the original user evidence before generic tool journaling.

        A later clarification cannot change a completed tool call's source or
        request hash when the framework replays that old call.
        """
        call_id=identifier(call_id);signature=digest(value)
        self.db.execute('BEGIN IMMEDIATE')
        try:
            row=self.db.execute('SELECT value_hash,evidence FROM learning_teaching_calls WHERE id=?',(call_id,)).fetchone()
            if row:
                if row['value_hash']!=signature:raise ValueError('此学习调用标识已用于不同的提议内容')
                evidence=json.loads(row['evidence'])
                if evidence.get('conversationId')!=conversation_id or evidence.get('runId')!=run_id and conversation_id is not None:
                    raise ValueError('学习调用的对话来源不一致')
            else:
                evidence=teaching_from_conversation(self.service,conversation_id,run_id)
                self.db.execute('INSERT INTO learning_teaching_calls VALUES(?,?,?)',(call_id,signature,compact(evidence)))
            self.db.execute('COMMIT')
        except BaseException:self.db.execute('ROLLBACK');raise
        return json.loads(compact(evidence))

    def _file_mails(self,file_id,seen=None):
        seen=set() if seen is None else seen
        if file_id in seen:return set()
        if len(seen)>=60:raise ValueError('文件来源链过长，请明确原始来源')
        seen.add(file_id);record=self.service.files.get(file_id);self.service.files.bytes(file_id)
        mails={record['sourceMailId']} if record.get('sourceMailId') else set()
        parents=record.get('parentIds',[])
        # A generated result has both customer input and reviewed reference
        # examples as parents. The reference may have been sent by an internal
        # colleague; it must not become the customer's identity on later reuse.
        audit=record.get('learning')
        if audit:
            if audit.get('inputId') not in parents:raise ValueError('学习输出的输入来源链不完整')
            parents=[audit['inputId']]
        for parent in parents:mails.update(self._file_mails(parent,seen))
        return mails

    def _mail(self,mail_id,scope):
        mail=self.service.mail.store.get_local(mail_id)
        sender=mail.get('fromAddress','')
        if not sender:raise ValueError('邮件缺少明确的 From 地址，请刷新来源；不能靠显示名识别客户')
        sender=address(sender)
        if scope is not None and sender not in scope['emails']:raise ValueError('邮件发件邮箱不属于此学习范围，禁止跨客户继承方法')
        return dict(kind='mail',id=mail_id,fromAddress=sender,contentHash=digest({key:mail.get(key,'') for key in ('fromAddress','subject','body','messageId')}),identityNotice='From 是邮件声明地址，不代表身份认证通过')

    def _file_scope(self,file_id,scope,source_mail_id=''):
        mail_ids=self._file_mails(file_id)
        if source_mail_id:mail_ids.add(source_mail_id)
        for mid in sorted(mail_ids):self._mail(mid,scope)
        return sorted(mail_ids)

    def _review_base(self,scope_id,title):
        """Snapshot approvals/revocations, including an approve-disable ABA."""
        active=self.db.execute('SELECT version_id FROM learning_active WHERE scope_id=? AND title=?',(scope_id,title)).fetchone()
        reviews=[dict(row) for row in self.db.execute('''SELECT v.id,r.status,r.reviewed_at FROM learning_versions v
            JOIN learning_reviews r ON r.version_id=v.id WHERE v.scope_id=? AND v.title=? AND r.status!='draft' ORDER BY v.id''',(scope_id,title))]
        return dict(activeId=active[0] if active else None,stateHash=digest(reviews))

    def _revision_base(self,scope_id,title,parent_id):
        """Permit correction chains, but never reset an old branch's baseline."""
        base=self._review_base(scope_id,title)
        if not parent_id:
            if base['activeId']:raise ValueError('此范围已有已批准方法，请基于当前版本重新提议修订')
            return base
        visited=set();parent=self.get(parent_id)
        while True:
            if parent['id'] in visited or len(visited)>=100:raise ValueError('学习修订来源链无效或过长')
            visited.add(parent['id'])
            if parent['scopeId']!=scope_id or parent['title']!=title:raise ValueError('修订不能跨客户或跨方法')
            if parent['status']=='draft':
                saved=parent['proposal'].get('reviewBase')
                if saved is not None and saved!=base:
                    raise ValueError('当前方法已被其他操作更新；此草稿基于旧版本，请以当前版本重新提议')
                if saved is None:
                    # Legacy drafts have no baseline. Never silently bless one
                    # after a review happened since it was originally created.
                    changed=self.db.execute('''SELECT 1 FROM learning_versions v JOIN learning_reviews r ON r.version_id=v.id
                        WHERE v.scope_id=? AND v.title=? AND r.reviewed_at>=? LIMIT 1''',(scope_id,title,parent['createdAt'])).fetchone()
                    if changed:raise ValueError('旧版本草稿缺少审核基线，请基于当前版本重新提议')
                if not parent['parentId']:
                    if base['activeId']:raise ValueError('当前方法已更新，此草稿基于旧版本')
                    return base
                parent=self.get(parent['parentId']);continue
            if parent['status']=='approved' and parent['id']==base['activeId']:return base
            if parent['status']=='disabled' and not base['activeId']:
                latest=self.db.execute('''SELECT v.id FROM learning_versions v JOIN learning_reviews r ON r.version_id=v.id
                    WHERE v.scope_id=? AND v.title=? AND r.reviewed_at IS NOT NULL ORDER BY r.reviewed_at DESC,v.number DESC LIMIT 1''',(scope_id,title)).fetchone()
                if latest and latest[0]==parent['id']:return base
            raise ValueError('当前方法已被其他操作更新；此草稿基于旧版本，请以当前已批准或最近停用版本重新提议')

    def propose(self,value,proposal_id=None,*,teaching=None):
        allowed=('title','kind','scope','instruction','sources','parentId','recipe','exampleFileId','expectedOutputFileId')
        shape(value,allowed,('title','kind','scope','instruction','sources'),at='proposal')
        # Clone bounded JSON before any persistence; caller mutations cannot change it.
        if len(compact(value))>100000:raise ValueError('学习草稿过大，请拆分方法')
        if teaching is None:teaching=teaching_from_conversation(self.service)
        elif not isinstance(teaching,dict):raise ValueError('教学来源无效')
        elif teaching.get('source')=='conversation':
            turns=teaching.get('turns',[])
            if not isinstance(turns,list) or not turns or not isinstance(turns[-1],dict):raise ValueError('教学原文记录无效')
            expected=teaching_from_conversation(self.service,teaching.get('conversationId'),teaching.get('runId'),through_turn_id=turns[-1].get('id'))
            if teaching!=expected:raise ValueError('教学原文与服务器保存的用户消息不一致，未保存模型改写的原文')
        elif teaching!=teaching_from_conversation(self.service):raise ValueError('教学来源不能伪造用户原文')
        raw=json.loads(compact(value));request_hash=digest(dict(value=raw,teaching=teaching))
        version_id=identifier(proposal_id) if proposal_id else str(uuid4())
        existing=self.db.execute('SELECT request_hash FROM learning_versions WHERE id=?',(version_id,)).fetchone()
        if existing:
            if existing['request_hash']!=request_hash:raise ValueError('学习提议标识已用于其他内容')
            return self.get(version_id)
        scope_id,scope=normalize_scope(value['scope']);title=string(value['title'],200);instruction=string(value['instruction'],20000)
        kind=value['kind']
        if kind not in ('note','table_workflow'):raise ValueError('学习类型仅支持 note 或 table_workflow')
        parent_id=value.get('parentId');number=1
        if parent_id:
            parent=self.get(parent_id)
            if parent['scopeId']!=scope_id or parent['title']!=title or parent['proposal']['kind']!=kind:raise ValueError('修订必须保留原方法范围、标题和类型；其他客户须另建方法')
            number=parent['version']+1
        elif self.db.execute('SELECT 1 FROM learning_versions WHERE scope_id=? AND title=?',(scope_id,title)).fetchone():
            raise ValueError('此范围已有同名方法；请先 get_learning 读取明确的父版本，再用 {"value":{"parentId":"该版本 UUID",...}} 修订。未自动选择父版本或覆盖旧稿。')
        sources=value['sources']
        if not isinstance(sources,list) or len(sources)>12:raise ValueError('学习来源须为最多 12 项的数组')
        sources=json.loads(compact(sources));preview=None
        if kind=='table_workflow':
            if not value.get('exampleFileId') or not value.get('recipe'):raise ValueError('表格方法须提供可试运行的 exampleFileId 和 recipe')
            recipe=normalize_recipe(value['recipe'])
            for key in ('exampleFileId','expectedOutputFileId'):
                if value.get(key) and not any(s.get('kind')=='file' and s.get('id')==value[key] for s in sources if isinstance(s,dict)):
                    sources.append(dict(kind='file',id=value[key]))
            record=self.service.files.get(value['exampleFileId']);data=self.service.files.bytes(record['id'])
            self._file_scope(record['id'],scope)
            output,preview=apply_recipe(data,record['extension'],recipe)
            preview.update(inputId=record['id'],inputSha256=hashlib.sha256(data).hexdigest())
            if value.get('expectedOutputFileId'):
                expected=self.service.files.get(value['expectedOutputFileId'])
                expected_table=read_table(self.service.files.bytes(expected['id']),expected['extension'])
                actual=read_table(output,recipe['outputFormat'])
                if (actual['columns'],actual['rows'])!=(expected_table['columns'],expected_table['rows']):raise ValueError('试运行结果与用户提供的正确样例不一致，请修正规则')
                preview['expectedOutputMatched']=True
        elif any(k in value for k in ('recipe','exampleFileId','expectedOutputFileId')):raise ValueError('文字经验不能包含可执行表格参数')
        # An explicitly selected completed reference may arrive from a colleague.
        # Its own provenance mail is the same reviewed evidence as the file, not
        # an additional customer's execution identity. No unrelated mail qualifies.
        reference_mail_ids=set()
        if kind=='table_workflow' and value.get('expectedOutputFileId') and value['expectedOutputFileId']!=value['exampleFileId']:
            reference_mail_ids=self._file_mails(value['expectedOutputFileId'])
        snapshots=[];seen=set()
        if len(sources)>14:raise ValueError('学习来源过多')
        for index,source in enumerate(sources):
            shape(source,('kind','id'),('kind','id'),at=f'sources[{index}]')
            key=(source['kind'],source['id'])
            if key in seen:continue
            seen.add(key)
            if source['kind']=='file':
                record=self.service.files.get(source['id'])
                is_reference=kind=='table_workflow' and record['id']==value.get('expectedOutputFileId') and record['id']!=value['exampleFileId']
                if is_reference:
                    mail_sources=[self._mail(mid,None) for mid in sorted(self._file_mails(record['id']))]
                else:self._file_scope(record['id'],scope)
                snapshot=dict(kind='file',id=record['id'],sha256=record['sha256'],name=record['name'])
                if is_reference:snapshot.update(role='reference-output',mailSources=mail_sources)
                snapshots.append(snapshot)
            elif source['kind']=='mail':
                is_reference=source['id'] in reference_mail_ids
                snapshot=self._mail(source['id'],None if is_reference else scope)
                if is_reference:snapshot['role']='reference-output'
                snapshots.append(snapshot)
            else:raise ValueError('来源只能是已管理的 file 或 mail 标识')
        proposal=dict(title=title,kind=kind,scope=scope,instruction=instruction,sources=snapshots,teaching=teaching)
        if parent_id:proposal['parentId']=identifier(parent_id)
        if kind=='table_workflow':
            proposal.update(recipe=recipe,exampleFileId=value['exampleFileId'])
            if value.get('expectedOutputFileId'):proposal['expectedOutputFileId']=value['expectedOutputFileId']
        self.db.execute('BEGIN IMMEDIATE')
        try:
            # Baseline is server-authored and captured under the review lock.
            # Corrections to drafts inherit the same unchanged authority state.
            proposal['reviewBase']=self._revision_base(scope_id,title,parent_id)
            version_hash=digest(dict(proposal=proposal,preview=preview))
            # Branches can be proposed from an old example, but every version has
            # a distinct increasing number within this scope and method title.
            number=self.db.execute('SELECT COALESCE(MAX(number),0)+1 FROM learning_versions WHERE scope_id=? AND title=?',(scope_id,title)).fetchone()[0]
            self.db.execute('INSERT INTO learning_versions VALUES(?,?,?,?,?,?,?,?,?,?)',(version_id,scope_id,title,number,parent_id,version_hash,compact(proposal),compact(preview) if preview else None,instant(),request_hash))
            self.db.execute('INSERT INTO learning_reviews VALUES(?,?,NULL)',(version_id,'draft'))
            self.db.execute('COMMIT')
        except BaseException:self.db.execute('ROLLBACK');raise
        return self.get(version_id)

    def get(self,id):
        row=self.db.execute('SELECT v.*,r.status,r.reviewed_at FROM learning_versions v JOIN learning_reviews r ON r.version_id=v.id WHERE v.id=?',(identifier(id),)).fetchone()
        if not row:raise ValueError('找不到此学习版本')
        active=self.db.execute('SELECT version_id FROM learning_active WHERE scope_id=? AND title=?',(row['scope_id'],row['title'])).fetchone()
        status=row['status']
        if status=='approved' and (not active or active[0]!=row['id']):status='superseded'
        proposal=json.loads(row['proposal']);preview=json.loads(row['preview']) if row['preview'] else None
        if digest(dict(proposal=proposal,preview=preview))!=row['version_hash']:raise ValueError('学习版本内容校验失败')
        if status=='draft' and proposal.get('reviewBase') is not None and proposal['reviewBase']!=self._review_base(row['scope_id'],row['title']):
            # Historical proposals remain immutable/readable, but once another
            # review changes their authority baseline they are no longer pending.
            status='superseded'
        return dict(kind='learning' if status=='approved' else 'learning_draft',id=row['id'],title=row['title'],scopeId=row['scope_id'],scope=proposal['scope'],version=row['number'],versionHash=row['version_hash'],parentId=row['parent_id'],status=status,createdAt=row['created_at'],reviewedAt=row['reviewed_at'],proposal=proposal,preview=preview)

    def list(self,value=None):
        value=value or {};shape(value,('scopeId','status','limit'))
        limit=value.get('limit',100)
        if type(limit)is not int or not 1<=limit<=200:raise ValueError('学习列表上限须为 1 至 200')
        rows=self.db.execute('SELECT id FROM learning_versions ORDER BY rowid DESC').fetchall()
        result=[]
        for row in rows:
            item=self.get(row[0])
            if value.get('scopeId') and item['scopeId']!=value['scopeId']:continue
            if value.get('status') and item['status']!=value['status']:continue
            result.append(item)
            if len(result)>=limit:break
        return result

    def _review(self,value,approve):
        shape(value,('id','expectedHash'),('id','expectedHash'))
        self.db.execute('BEGIN IMMEDIATE')
        try:
            current=self.get(value['id'])
            if current['versionHash']!=value['expectedHash']:raise ValueError('方法预览已变化，请重新查看后确认')
            if approve:
                if current['status']=='approved':
                    self.db.execute('COMMIT')
                    return self.get(current['id'])
                if current['status']!='draft':raise ValueError('只有待确认草稿可以批准；旧版本或停用版本不能直接重新启用')
                if 'reviewBase' not in current['proposal']:
                    changed=self.db.execute('''SELECT 1 FROM learning_versions v JOIN learning_reviews r ON r.version_id=v.id
                        WHERE v.scope_id=? AND v.title=? AND r.reviewed_at>=? LIMIT 1''',(current['scopeId'],current['title'],current['createdAt'])).fetchone()
                    if changed:raise ValueError('旧版本草稿缺少审核基线且审核状态已变化，请基于当前版本重新提议')
                base=self._revision_base(current['scopeId'],current['title'],current['parentId'])
                if current['proposal'].get('reviewBase',base)!=base:
                    raise ValueError('审核状态已变化；此草稿基于旧版本，请以当前版本重新提议')
                # Source files must still be intact at approval time. Mail may have
                # since been deleted; its immutable source snapshot stays reviewable.
                for source in current['proposal']['sources']:
                    if source['kind']=='file':
                        data=self.service.files.bytes(source['id'])
                        if hashlib.sha256(data).hexdigest()!=source['sha256']:raise ValueError('样例文件已变化，请重新提议方法')
                self.db.execute('UPDATE learning_reviews SET status=?,reviewed_at=? WHERE version_id=?',('approved',instant(),current['id']))
                self.db.execute('INSERT INTO learning_active VALUES(?,?,?) ON CONFLICT(scope_id,title) DO UPDATE SET version_id=excluded.version_id',(current['scopeId'],current['title'],current['id']))
            else:
                if current['status']=='disabled':
                    self.db.execute('COMMIT')
                    return current
                self.db.execute('UPDATE learning_reviews SET status=?,reviewed_at=? WHERE version_id=?',('disabled',instant(),current['id']))
                self.db.execute('DELETE FROM learning_active WHERE version_id=?',(current['id'],))
            self.db.execute('COMMIT')
        except BaseException:self.db.execute('ROLLBACK');raise
        return self.get(current['id'])

    def approve(self,value):return self._review(value,True)
    def disable(self,value):return self._review(value,False)

    def search(self,query='',sender='',scope_id='',status='approved'):
        query=string(query,300,False).casefold();sender=address(sender) if sender else '';scope_id=string(scope_id,100,False)
        if status not in ('approved','draft','all'):raise ValueError('学习检索状态只支持 approved、draft 或 all')
        results=[]
        rows=self.db.execute('SELECT version_id FROM learning_active ORDER BY rowid DESC') if status=='approved' else self.db.execute('SELECT id FROM learning_versions ORDER BY rowid DESC')
        for row in rows:
            item=self.get(row[0])
            if status!='all' and item['status']!=status:continue
            if scope_id and item['scopeId']!=scope_id:continue
            if sender and sender not in item['scope']['emails']:continue
            if query and query not in (item['title']+' '+item['scope']['label']+' '+item['proposal']['instruction']+' '+item['scope']['contextKey']).casefold():continue
            results.append(item)
            if len(results)>=30:break
        return results

    def run(self,version_id,file_id,name,scope_id='',source_mail_id='',run_id=None):
        method=self.get(version_id)
        if method['status']!='approved':raise ValueError('方法尚未由用户批准、已停用或已被新版本替代')
        if not scope_id or method['scopeId']!=scope_id:raise ValueError('运行方法必须明确匹配的 scopeId，不能凭显示名跨客户复用')
        if method['proposal']['kind']!='table_workflow':raise ValueError('文字经验只能检索参考，不能当作表格程序运行')
        name=string(name,200);recipe=method['proposal']['recipe']
        if Path(name).name!=name or '/' in name or '\\' in name:raise ValueError('输出只接受文件名，不接受任意路径')
        name=Path(name).stem+'.'+recipe['outputFormat']
        record=self.service.files.get(file_id);data=self.service.files.bytes(file_id)
        mails=self._file_scope(file_id,method['scope'],source_mail_id)
        request=dict(versionId=method['id'],versionHash=method['versionHash'],scopeId=scope_id,inputId=file_id,inputSha256=hashlib.sha256(data).hexdigest(),name=name,sourceMailIds=mails)
        signature=digest(request);key=identifier(run_id) if run_id else str(uuid5(NAMESPACE_URL,'task-manager-learning-run:'+signature))
        old=self.db.execute('SELECT request_hash,result FROM learning_runs WHERE id=?',(key,)).fetchone()
        if old:
            if old['request_hash']!=signature:raise ValueError('此执行标识已用于不同的学习任务')
            result=json.loads(old['result']);output=self.service.files.bytes(result['file']['id'])
            if hashlib.sha256(output).hexdigest()!=result['audit']['outputSha256']:raise ValueError('历史输出校验失败')
            return dict(result,replayed=True)
        output,report=apply_recipe(data,record['extension'],recipe)
        parents=list(dict.fromkeys([file_id]+[s['id'] for s in method['proposal']['sources'] if s['kind']=='file']))
        artifact_id=str(uuid5(NAMESPACE_URL,'task-manager-learning-artifact:'+key))
        self.db.execute('BEGIN IMMEDIATE')
        try:
            # Hold the same lock used by review until the run is checkpointed.
            if self.get(method['id'])['status']!='approved':raise ValueError('方法在处理期间被停用或替换，请重新确认')
            prior=self.service.files.db.execute('SELECT request_hash FROM files WHERE id=?',(artifact_id,)).fetchone()
            if prior and prior[0]!=signature:raise ValueError('已存在输出的处理参数不同，未复用或覆盖')
            saved=self.service.files._save(name,output,'generated',parents=parents,artifact_id=artifact_id,request_hash=signature)
            audit=dict(request,outputId=saved['id'],outputSha256=saved['sha256'],approvedVersion=method['version'],totals=report['totals'])
            saved=dict(saved,learning=audit)
            # Extend metadata only; never rewrite artifact bytes or core identity.
            self.service.files.db.execute('UPDATE files SET record=? WHERE id=?',(compact(saved),saved['id']))
            result=dict(kind='file',file=saved,audit=audit,report=report,replayed=False)
            self.db.execute('INSERT INTO learning_runs VALUES(?,?,?,?)',(key,signature,compact(result),instant()))
            self.db.execute('COMMIT')
        except BaseException:self.db.execute('ROLLBACK');raise
        return result

    def call(self,method,*args):
        # IPC integration invokes this route; it is not an agent tool registry.
        routes={'list':self.list,'get':self.get,'approve':self.approve,'disable':self.disable}
        if method not in routes:raise ValueError('未知学习管理操作')
        return routes[method](*args)
