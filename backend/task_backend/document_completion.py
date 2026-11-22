"""Bounded document subtask completion using official LangChain middleware.

The framework owns routing and tool accounting. This adapter only adds a
fact-based handover at that boundary; it does not implement another agent loop,
keyword task classifier, or a separate persisted budget. Use on the document
subagent, never as a global limit on a user's conversation or number of files.
"""
from __future__ import annotations

import json
import time
from dataclasses import replace
from operator import add
from typing import Annotated, NotRequired

from langchain.agents.middleware import AgentMiddleware, AgentState, ModelCallLimitMiddleware, ToolCallLimitMiddleware, hook_config
from langchain_core.messages import AIMessage, SystemMessage, ToolMessage
from langgraph.types import Command


_WRITE_TOOLS = frozenset({'create_document','create_business_document','revise_business_document',
    'edit_word','format_word','convert_to_pdf','extract_pdf_pages','create_csv','transform_csv','run_skill','run_learning',
    'register_workspace_file','register_workspace_files','publish_file'})


def _merge_ids(left,right):
    return list(dict.fromkeys((left or [])+(right or [])))


def _merge_views(left,right):
    result=dict(left or {})
    for key,pages in (right or {}).items():result[key]=sorted(set(result.get(key,[])+pages))
    return result


def _merge_reviews(left,right):
    result={key:dict(pages) for key,pages in (left or {}).items()}
    for key,pages in (right or {}).items():
        result.setdefault(key,{}).update(pages)
    return result


def _layout_review(payload):
    """Accept only the structured layout contract, never infer pass from prose."""
    if not isinstance(payload,dict) or not payload.get('vision'):return {}
    fid,sha=payload.get('fileId'),payload.get('sha256')
    review=payload.get('layoutReview')
    if not isinstance(fid,str) or not isinstance(sha,str) or not isinstance(review,dict):return {}
    pages=review.get('pages')
    if not isinstance(pages,list):return {}
    checked={}
    for page in pages:
        if not isinstance(page,dict) or type(page.get('page')) is not int or page['page']<1:return {}
        if page.get('status') not in ('pass','needs_fix','uncertain'):return {}
        issues=page.get('blockingIssues')
        if not isinstance(issues,list) or any(not isinstance(v,str) for v in issues):return {}
        checked[str(page['page'])]={'status':page['status'],'blockingIssues':issues}
    if sorted(map(int,checked))!=sorted(payload.get('pages',[])):return {}
    return {fid+':'+sha:checked}


def imported_source_inventory(ctx):
    """Current execution's successful imports, shared through the existing journal."""
    rows=ctx.db.execute("SELECT result FROM agent_tool_results WHERE execution_id=? AND operation='import_attachment' AND result IS NOT NULL ORDER BY created_at,id",(ctx.execution_id,)).fetchall()
    files={}
    for row in rows:
        try:
            value=json.loads(row['result']);file=value.get('file')
            if not isinstance(file,dict) or not isinstance(file.get('id'),str):continue
            current=ctx.service.files.get(file['id'])
            if current.get('source')!='mail' or current['sha256']!=file.get('sha256'):continue
            files[current['id']]={key:current.get(key) for key in ('id','name','sourceMailId','sha256')}
        except (ValueError,TypeError,KeyError,FileNotFoundError):continue
    return list(files.values())


class DocumentCompletionState(AgentState):
    # These are ordinary framework checkpoint fields, not a new artifact store.
    # Keep delivery provenance if an official summarizer evicts tool messages.
    document_output_ids: NotRequired[Annotated[list[str],_merge_ids]]
    document_page_views: NotRequired[Annotated[dict[str,list[int]],_merge_views]]
    document_layout_reviews: NotRequired[Annotated[dict[str,dict],_merge_reviews]]
    document_repair_count: NotRequired[Annotated[int,add]]
    document_model_limited: NotRequired[bool]


def _payload(message):
    value=message.content
    if isinstance(value,list):
        value=''.join(p.get('text','') for p in value if isinstance(p,dict) and p.get('type')=='text')
    if isinstance(value,str):
        try:return json.loads(value)
        except (ValueError,TypeError):return None
    return value if isinstance(value,dict) else None


def _output_ids(value):
    if isinstance(value,list):
        return [fid for item in value for fid in _output_ids(item)]
    if not isinstance(value,dict):return []
    ids=[]
    # Only known output fields, never source IDs, parentIds or arbitrary prose.
    if value.get('kind')=='file' and isinstance(value.get('id'),str):ids.append(value['id'])
    if isinstance(value.get('file'),dict) and isinstance(value['file'].get('id'),str):ids.append(value['file']['id'])
    if isinstance(value.get('id'),str) and 'sha256' in value and 'name' in value:ids.append(value['id'])
    for key in ('files','artifacts','outputs','outputFiles','result'):
        if key in value:ids.extend(_output_ids(value[key]))
    return ids


def document_outputs(ctx,state):
    """Read actual current artifacts produced by THIS subtask's tool results."""
    candidates=list(state.get('document_output_ids',[]));views={}
    reviews={}
    for key,pages in state.get('document_page_views',{}).items():
        fid,sha=key.split(':',1);views[(fid,sha)]=set(pages)
    for message in state.get('messages',[]):
        if not isinstance(message,ToolMessage):continue
        payload=_payload(message)
        if message.name in _WRITE_TOOLS:candidates.extend(_output_ids(payload))
        if message.name=='view_file' and isinstance(payload,dict) and payload.get('vision'):
            reviews=_merge_reviews(reviews,_layout_review(payload))
            key=(payload.get('fileId'),payload.get('sha256'))
            views.setdefault(key,set()).update(payload.get('pages',[]))
    reviews=_merge_reviews(reviews,state.get('document_layout_reviews',{}))
    files=[];unavailable=[]
    for fid in dict.fromkeys(candidates):
        try:
            record=ctx.service.files.get(fid)
            if record.get('source')!='generated' or record.get('lifecycleStatus','active')!='active':continue
            ctx.service.files.bytes(fid)  # hash/read check, not model claims
        except (ValueError,FileNotFoundError):
            unavailable.append(fid);continue
        validation=record.get('validation') or {}
        layout=reviews.get(fid+':'+record['sha256'],{})
        page_count=validation.get('pageCount',0)
        expected_pages=set(range(1,page_count+1)) if type(page_count) is int and 0<page_count<=10000 else set()
        checked_pages={int(p) for p in layout if p.isdigit() and int(p) in expected_pages}
        passed_pages={p for p in checked_pages if layout[str(p)]['status']=='pass' and not layout[str(p)]['blockingIssues']}
        issues=[dict(page=p,status=layout[str(p)]['status'],issues=layout[str(p)]['blockingIssues'])
                for p in sorted(checked_pages) if p not in passed_pages]
        files.append(dict(id=fid,name=record['name'],sha256=record['sha256'],pageCount=page_count,
            layoutCheckedPages=sorted(checked_pages),layoutPassedPages=sorted(passed_pages),layoutIssues=issues,
            layoutReady=bool(expected_pages and passed_pages==expected_pages and validation.get('structuralPassed') and validation.get('rendered')),
            structuralPassed=validation.get('structuralPassed',False),
            rendered=validation.get('rendered',False),
            pagesViewed=sorted(views.get((fid,record['sha256']),set())),
            warnings=validation.get('warnings',[])))
    return dict(files=files,unavailableIds=unavailable)


def _handover(snapshot,limited=False):
    lines=[]
    if limited:
        lines.append('文档子任务到达本次处理边界，当前为部分完成。已停止继续试排版；尚未完成的内容、图表或核查不能宣称已完成。')
    if snapshot['files']:
        lines.append('实际保存文件：')
        for file in snapshot['files']:
            name=file['name'].replace('[','(').replace(']',')').replace('\n',' ')
            checks=['结构已验证' if file['structuralPassed'] else '结构尚未验证']
            if file['rendered']:checks.append('已渲染')
            if file['pagesViewed']:checks.append('模型查看了第 '+','.join(map(str,file['pagesViewed']))+' 页')
            else:checks.append('尚无页面视觉检查证据')
            if file.get('layoutReady'):checks.append('当前版本全部页面未发现阻碍阅读的排版问题')
            elif file.get('layoutIssues'):checks.append('仍有排版问题或未确定项：'+json.dumps(file['layoutIssues'],ensure_ascii=False))
            lines.append('- [%s](file://%s)：%s。'%(name,file['id'],'；'.join(checks)))
        lines.append('保存、结构检查和页面观察不等于内容正确性已全部核实；保留来源中的不确定性。')
    else:
        lines.append('本次文档子任务尚未保存可用的新文件。')
    if snapshot['unavailableIds']:
        lines.append('部分记录对应文件不可用或字节已变化，未作为交付文件列出：'+', '.join(snapshot['unavailableIds'])+'。')
    return '\n'.join(lines)


class DocumentCompletionMiddleware(AgentMiddleware):
    """A handover hook around the official per-subtask tool-call counter."""
    state_schema=DocumentCompletionState
    def __init__(self,ctx,max_tool_calls=None,review_after=3,model_limit=40):
        self.ctx=ctx;self.max_tool_calls=max_tool_calls;self.review_after=review_after;self.model_limit=model_limit

    async def awrap_tool_call(self,request,handler):
        result=await handler(request)
        update=result.update if isinstance(result,Command) else {'messages':[result]}
        if not isinstance(update,dict):return result
        ids=[];views={};reviews={}
        for message in update.get('messages',[]):
            if not isinstance(message,ToolMessage):continue
            payload=_payload(message)
            if request.tool_call['name'] in _WRITE_TOOLS:ids.extend(_output_ids(payload))
            if request.tool_call['name']=='view_file' and isinstance(payload,dict) and payload.get('vision'):
                reviews=_merge_reviews(reviews,_layout_review(payload))
                if isinstance(payload.get('fileId'),str) and isinstance(payload.get('sha256'),str):
                    views[payload['fileId']+':'+payload['sha256']]=payload.get('pages',[])
        if not ids and not views and not reviews:return result
        changes=dict(update)
        if ids:changes['document_output_ids']=_merge_ids(changes.get('document_output_ids'),ids)
        if reviews:changes['document_layout_reviews']=_merge_reviews(changes.get('document_layout_reviews'),reviews)
        if views:changes['document_page_views']=_merge_views(changes.get('document_page_views'),views)
        if ids and request.tool_call['name'] in ('edit_word','format_word','revise_business_document'):
            changes['document_repair_count']=changes.get('document_repair_count',0)+1
        return replace(result,update=changes) if isinstance(result,Command) else Command(update=changes)

    @hook_config(can_jump_to=['end'])
    async def abefore_model(self,state,runtime):
        # Mark only an attempted next model call. A natural final answer on the
        # 40th call is complete; the upstream limiter stops the attempted 41st.
        if state.get('run_model_call_count',0)>=self.model_limit:
            return dict(document_model_limited=True)
        count=state.get('run_tool_call_count',{}).get('__all__',0)
        if self.max_tool_calls is None or count<self.max_tool_calls:return None
        snapshot=document_outputs(self.ctx,state)
        self.ctx.on_event(dict(type='document_completion',limited=True,toolCalls=count,
            fileIds=[f['id'] for f in snapshot['files']],detail='已停止反复返工，保留实际文件和未完成状态'))
        return dict(messages=[AIMessage(content=_handover(snapshot,limited=True))],jump_to='end')

    async def awrap_model_call(self,request,handler):
        guidance=('文档子任务原则：先保证来源事实、计算和必需内容，再修缺字、截断和重叠。'
            '保留“不一定、尚未核实”等原始限定，不能改写成确定事实。'
            '约两页等近似要求不是必须反复挤压的硬门槛。正常留白和非关键装饰不值得重建整份文档。'
            '先按篇幅筛选读者需要的内容：两页左右的摘要通常正文控制在约900至1400个中文字，少量关键数据和来源即可；不是把技术源文压缩抄成多个宽表。'
            '草稿前确定重点和顺序，一次性合并必要修改；不要每个措辞变化都生成一个版本。'
            '已有 Word 改正文用 edit_word，改列宽/段落样式/分页用 format_word；原模板及其他内容保留。'
            '同一份 Word 转 PDF 用 convert_to_pdf 保留实际版式，不能提取为 Markdown 再重建。'
            '输出排版检查使用 view_file(purpose="layout")，按实际页数每批最多3页，聚焦缺字、遮挡、溢出和不可读图形，不询问留白比例或美学建议。'
            '只在看完相邻页后判断表格是否缺行；正常跨页续表不是内容缺失。已经检查的同一版本同一页不要重复检查。'
            '有 chart 语法时直接用图表生成能力，不以字符条形充当稳定排版方案。'
            '当前展示的工具就是可用能力；ls只能查看工作笔记或资料目录，不能发现未提供的程序。'
            '来源已齐就先生成有用的报告，避免为寻找不存在的图表程序反复检索或建立连通性测试文件。'
            '近似要求或可选图表不应阻断其他已明确内容的交付。图形应可见且比例准确；'
            '用户未指定底层格式时，不要自行要求原生图表对象或图片。准确的比例数据条可以是图形表达，'
            '但占位文字不是图；如仍有能力缺口，交付已有内容并如实说明。'
            '最终报告实际文件ID、已做检查和明确未完成项；不会在此子任务发送邮件。')
        if request.state.get('document_repair_count',0)>=self.review_after and request.state.get('document_output_ids'):
            guidance+='\n本次已有实际产物并多次修订。只补必要事实或缺损修复和最后验证，停止非必要试排版；如仍有问题，交回现有文件和准确限制。'
        imports=imported_source_inventory(self.ctx)
        if imports:
            guidance+='\n本次执行已成功导入的邮件附件清单（真实工具记录，只作定位，不代表属于同一业务对象）：'+json.dumps(imports,ensure_ascii=False)
            guidance+=' 按用户目标与sourceMailId选取对应资料，用实际文件ID读取即可，不要因委派遗漏ID重复搜索或导入。文件名只是资料标签。'
        snapshot=document_outputs(self.ctx,request.state)
        if snapshot['files']:
            # Same-version checks are explicit. A stale review, source file or
            # plain extraction can never turn a new report into a passed one.
            current=[{key:file[key] for key in ('id','name','pageCount','structuralPassed','rendered',
                'layoutCheckedPages','layoutPassedPages','layoutIssues','layoutReady')} for file in snapshot['files'][-12:]]
            guidance+='\n当前产物与已取得的排版证据（程序读取，不能把旧版本检查套给新版本）：'+json.dumps(current,ensure_ascii=False)
            guidance+='\nlayoutReady=true 表示该版本全部页面的排版检查无阻碍，不代表业务事实自动正确。若所需内容与格式已齐，直接返回文件和检查结果；不要继续为留白、非关键措辞或近似页数返工。'
            guidance+='若还缺用户明确要求的另一种格式，直接转换并检查该格式；若确有内容错误则合并修正，再检查新版本。uncertain 是需核实的观察，不是已确认缺页或必须重写全文。'
        system=request.system_message
        base=system.text if system is not None else ''
        updated=request.override(system_message=SystemMessage(content=base+'\n\n'+guidance))
        started=time.monotonic()
        self.ctx.on_event(dict(type='model_call',kind='document',status='started'))
        try:
            result=await handler(updated)
        except Exception:
            self.ctx.on_event(dict(type='model_call',kind='document',status='failed',elapsedSeconds=round(time.monotonic()-started,3)))
            raise
        tokens={}
        for message in result.result:
            for key,value in (getattr(message,'usage_metadata',None) or {}).items():
                if key in ('input_tokens','output_tokens','total_tokens') and isinstance(value,int):tokens[key]=tokens.get(key,0)+value
        self.ctx.on_event(dict(type='model_call',kind='document',status='completed',elapsedSeconds=round(time.monotonic()-started,3),usage=tokens))
        return result

    async def aafter_agent(self,state,runtime):
        messages=state.get('messages',[])
        if not messages or not isinstance(messages[-1],AIMessage):return None
        message=messages[-1]
        if message.tool_calls:return None
        snapshot=document_outputs(self.ctx,state)
        # The bounded fallback already carries this same truthful inventory.
        if self.max_tool_calls is not None and state.get('run_tool_call_count',{}).get('__all__',0)>=self.max_tool_calls:return None
        limited=state.get('document_model_limited',False)
        self.ctx.on_event(dict(type='document_completion',limited=limited,
            fileIds=[f['id'] for f in snapshot['files']],detail='已核对文档子任务实际文件清单'))
        content=_handover(snapshot,limited=True) if limited else message.text+'\n\n'+_handover(snapshot)
        return dict(messages=[message.model_copy(update={'content':content})])


def document_completion_middleware(ctx,*,max_tool_calls=None,review_after=3,model_limit=40):
    """Attach only to an official document subagent; the parent keeps its budget."""
    if max_tool_calls is not None and (type(max_tool_calls) is not int or max_tool_calls<2):raise ValueError('文档子任务工具额度至少为2')
    if type(review_after) is not int or review_after<1:raise ValueError('文档返工提示位置无效')
    if type(model_limit) is not int or model_limit<1:raise ValueError('模型调用额度无效')
    optional=[] if max_tool_calls is None else [ToolCallLimitMiddleware(run_limit=max_tool_calls,exit_behavior='continue')]
    return [*optional,DocumentCompletionMiddleware(ctx,max_tool_calls,review_after,model_limit),
            ModelCallLimitMiddleware(run_limit=model_limit,exit_behavior='end')]
