"""Small, explicit visual-review contract, separate from open-ended reading.

A page's absence from an image batch is never evidence of missing content.
Invalid or partial model output cannot clear a document for delivery.
"""
import json

LAYOUT_REVIEW_VERSION = 1


def layout_prompt(pages):
    return (
        '这是最终版面检查，不是重写或设计评比。只指出截图上实际可见的缺字乱码、'
        '文本截断/重叠、表格或图形超出边界、图形比例不可读等影响使用的缺陷。'
        '正常留白、自然换页、重复表头、可读的正常换行不是阻断问题。'
        '没有提供的上一页/下一页不能据此判定内容丢失，不能从单页推断整份文件缺项。'
        '无法辨认时标 uncertain，不要猜测。不要重新分析全文、提出新内容要求或复述思考过程。'
        '对每张图分别返回一项，只输出简短 JSON：'
        '{"pages":[{"page":1,"status":"pass|needs_fix|uncertain",'
        '"blockingIssues":[],"suggestions":[]}],"summary":"一两句结论"}。'
        'needs_fix 必须给出可定位的可见问题；建议只放 suggestions，不是返工条件。'
        '每项最多3个问题，每条不超过160字。必须且只能包含这些页码：'+','.join(map(str,pages))+'。'
    )


def parse_layout_review(answer, pages):
    value=answer.strip()
    if value.startswith('```') and value.endswith('```'):
        value=value.split('\n',1)[1].rsplit('```',1)[0]
    try:
        data=json.loads(value)
    except (ValueError,TypeError):
        raise ValueError('版面检查未返回完整结构化结果，不能视为检查通过') from None
    if not isinstance(data,dict) or set(data)!={'pages','summary'}:
        raise ValueError('版面检查结果格式无效')
    if not isinstance(data['summary'],str) or len(data['summary'])>1000:
        raise ValueError('版面检查摘要无效')
    rows=data['pages']
    if not isinstance(rows,list) or len(rows)!=len(pages):
        raise ValueError('版面检查没有覆盖本次请求的全部页面')
    seen=set()
    for row in rows:
        if not isinstance(row,dict) or set(row)!={'page','status','blockingIssues','suggestions'}:
            raise ValueError('版面检查页面结果格式无效')
        page=row['page'];status=row['status']
        if type(page) is not int or page not in pages or page in seen:
            raise ValueError('版面检查页码不匹配')
        seen.add(page)
        if status not in ('pass','needs_fix','uncertain'):
            raise ValueError('版面检查状态无效')
        for key in ('blockingIssues','suggestions'):
            if not isinstance(row[key],list) or len(row[key])>8 or any(
                    not isinstance(item,str) or not item.strip() or len(item)>1000 for item in row[key]):
                raise ValueError('版面检查问题说明无效')
        if status=='pass' and row['blockingIssues'] or status=='needs_fix' and not row['blockingIssues']:
            raise ValueError('版面检查状态与问题不一致')
    return dict(pages=rows,summary=data['summary'],passed=all(row['status']=='pass' for row in rows))


def layout_review_coverage(db, file_id, sha256, page_count):
    """Project existing visual observations onto this exact immutable version.

    This is a SELECT-only adapter over the tool journal shared by parent and
    subagents. Rendering success cannot manufacture a visual review, and a
    review of a replaced file cannot clear its successor.
    """
    expected=set(range(1,page_count+1)) if type(page_count) is int and 0<page_count<=80 else set()
    reviewed={}
    rows=db.execute("SELECT id,created_at,result FROM agent_tool_results WHERE operation='view_file' AND result IS NOT NULL ORDER BY created_at,id").fetchall()
    for row in rows:
        try:
            value=json.loads(row['result'])
            if not isinstance(value,dict) or value.get('fileId')!=file_id or value.get('sha256')!=sha256:
                continue
            if value.get('vision') is not True or value.get('purpose')!='layout':continue
            pages=value.get('pages');proof=value.get('layoutReview')
            if not isinstance(pages,list) or not pages or any(type(p) is not int or p not in expected for p in pages):continue
            if len(set(pages))!=len(pages) or not isinstance(proof,dict):continue
            # Revalidate persisted data rather than treating a stored `passed`
            # flag or the model's prose as proof. Latest valid page observation
            # wins, including a later uncertain/needs_fix result.
            parsed=parse_layout_review(json.dumps({k:proof[k] for k in ('pages','summary')}),pages)
            for page in parsed['pages']:
                reviewed[page['page']]={**page,'model':value.get('model',''),
                    'reviewedAt':row['created_at'],'evidenceId':row['id']}
        except (ValueError,TypeError,KeyError):continue
    seen=set(reviewed);passed={p for p,item in reviewed.items() if item['status']=='pass'}
    complete=bool(expected and seen==expected)
    return dict(fileId=file_id,sha256=sha256,pageCount=page_count,
        reviewedPages=sorted(seen),passedPages=sorted(passed),missingPages=sorted(expected-seen),
        complete=complete,allPassed=bool(complete and passed==expected),
        pages=[reviewed[p] for p in sorted(reviewed)],
        scope='已有模型对当前文件哈希的页面观察；完整观察不等于检查通过，也不保证业务事实正确。')
