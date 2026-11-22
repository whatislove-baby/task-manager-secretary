import React,{useEffect,useRef,useState} from 'react';
import type {AgentSource,LearningMethod} from '../shared/agent';
import type {Result} from '../shared/contracts';
const unwrap=<T,>(value:Result<T>):T=>{if(!value.ok)throw Error(value.error);return value.data;};
const statusLabels:Record<LearningMethod['status'],string>={draft:'待确认',approved:'已批准',disabled:'已停用',superseded:'已被新版本替代'};
const scopeLabels={customer:'客户',course:'课程',personal:'个人'};
const teachingTime=(value:string)=>{const date=new Date(value);return Number.isNaN(date.getTime())?value:date.toLocaleString('zh-CN',{timeZone:'Asia/Hong_Kong',hour12:false})+'（香港时间）';};
const show=(value:unknown):string=>Array.isArray(value)?value.map(show).join('、'):value!==null&&typeof value==='object'?JSON.stringify(value):String(value??'');
function describeOperation(step:Record<string,unknown>){
 const operand=(value:unknown)=>{const item=value as {column?:string;value?:unknown}|undefined;return item?.column??show(item?.value);};
 const operators:Record<string,string>={add:'+',subtract:'−',multiply:'×',divide:'÷',eq:'等于',ne:'不等于',gt:'大于',gte:'大于等于',lt:'小于',lte:'小于等于',contains:'包含',is_empty:'为空',not_empty:'不为空'};
 if(step.op==='derive')return `${show(step.column)} = ${operand(step.left)} ${operators[String(step.operator)]??show(step.operator)} ${operand(step.right)}`;
 if(step.op==='fill_missing')return `${show(step.column)} 为空时填入 ${show(step.value)}`;
 if(step.op==='filter')return `只保留 ${show(step.column)} ${operators[String(step.operator)]??show(step.operator)} ${show(step.value)} 的行${step.numeric?'（按数值比较）':''}`;
 if(step.op==='sort')return `按 ${show(step.columns)} ${step.ascending===false?'降序':'升序'}排列${step.numeric?'（数值排序）':''}`;
 if(step.op==='select')return `仅保留 ${show(step.columns)}`;
 if(step.op==='drop_duplicates')return `按 ${show(step.subset)||'所有列'} 去除重复行，保留第一行`;
 if(step.op==='rename')return `重命名字段：${Object.entries((step.mapping??{}) as Record<string,unknown>).map(([a,b])=>a+' → '+show(b)).join('；')}`;
 if(step.op==='update')return `数据第 ${Number(step.row)+1} 行的 ${show(step.column)}：${show(step.expected)} → ${show(step.value)}（先核对原值）`;
 if(step.op==='groupby')return `按 ${show(step.by)||'全部行'} 汇总；计算细节见全部规则`;
 return '请查看下面的完整规则：'+show(step.op);
}

export function LearningReview({id,onChanged,onSource,onBusy}:{id:string;onChanged?():void|Promise<void>;onSource?(source:AgentSource):void;onBusy?(busy:boolean):void}){
 const [method,setMethod]=useState<LearningMethod|null>(null),[error,setError]=useState(''),[busy,setBusy]=useState(false),[notice,setNotice]=useState('');
 const generation=useRef(0),locked=useRef(false);
 useEffect(()=>{const current=++generation.current;setMethod(null);setError('');setNotice('');setBusy(false);locked.current=false;
  if(!window.learning){setError('学习方法接口尚未加载，请重新打开应用。');return;}
  void window.learning.get(id).then(result=>{if(current===generation.current){const value=unwrap(result);if(value.id!==id)throw Error('方法版本与所选内容不一致，请重新打开。');setMethod(value);}}).catch(e=>{if(current===generation.current)setError(e instanceof Error?e.message:'读取方法失败');});
  return()=>{generation.current++;};
 },[id]);
 useEffect(()=>{onBusy?.(busy);return()=>onBusy?.(false);},[busy,onBusy]);
 async function review(action:'approve'|'disable'){
  if(!method||!window.learning||locked.current)return;locked.current=true;setBusy(true);setError('');setNotice('');const current=generation.current;
  try{const result=unwrap(await window.learning[action]({id:method.id,expectedHash:method.versionHash}));if(current!==generation.current)return;setMethod(result);setNotice(action==='approve'?'已批准此版本。助手可在该范围内复用。':'已停用此版本，后续任务不会再执行它。');await onChanged?.();}
  catch(e){if(current===generation.current)setError(e instanceof Error?e.message:'审核未完成');}
  finally{if(current===generation.current){locked.current=false;setBusy(false);}}
 }
 const recipe=method?.proposal.recipe,preview=method?.preview,teaching=method?.proposal.teaching;
 return <section className="ag-review" aria-label="学习方法审核" aria-busy={busy}>
  {error&&<p className="ag-error" role="alert">{error}</p>}{notice&&<p role="status">{notice}</p>}
  {!method&&!error&&<p role="status">正在读取方法…</p>}
  {method&&<><header><div><h2>{method.title}</h2><p>{statusLabels[method.status]} · 版本 {method.version} · {method.proposal.kind==='table_workflow'?'表格处理方法':'文字经验'}</p></div></header>
   <section aria-label="方法适用范围"><h3>适用范围</h3><dl><dt>对象</dt><dd>{scopeLabels[method.scope.kind]} · {method.scope.label}</dd><dt>发件邮箱</dt><dd>{method.scope.emails.length?method.scope.emails.join('、'):'未关联邮箱'}</dd>{method.scope.contextKey&&<><dt>课程 / 上下文</dt><dd>{method.scope.contextKey}</dd></>}</dl><p className="ag-muted">只在这一范围复用；显示名相同不代表同一客户。From 地址本身不代表身份认证通过。</p><details><summary>版本与范围校验</summary><p>范围：<code>{method.scopeId}</code></p><p>版本：<code>{method.versionHash}</code></p></details></section>
   <section aria-label="原始用户教学"><h3>原始用户教学</h3>{teaching?.source==='conversation'?<><p className="ag-muted">以下内容来自你在对话中发送的原文；请与下面归纳的方法一起核对。</p><details open><summary>查看 {teaching.turns.length} 条用户原文</summary>{teaching.turns.map(turn=><article className="ag-teaching-turn" key={turn.id}><time dateTime={turn.createdAt}>{teachingTime(turn.createdAt)}</time><blockquote>{turn.content}</blockquote></article>)}</details>{teaching.omittedUserTurns>0&&<p className="ag-review-warning">另有 {teaching.omittedUserTurns} 条较早用户消息未包含在此版本证据中。</p>}<details><summary>教学来源校验</summary><p>对话：<code>{teaching.conversationId}</code></p><p>原文快照：<code>{teaching.sourceHash}</code></p></details></>:<p className="ag-muted">{teaching?'直接输入；没有可验证的原始对话。':'旧版无原始对话。'}</p>}</section>
   <section><h3>要保存的处理方法</h3><p className="ag-review-prose">{method.proposal.instruction}</p>{method.parentId&&<button disabled={busy||!onSource} onClick={()=>onSource?.({kind:'learning',id:method.parentId!,title:'上一版本'})}>查看上一版本</button>}</section>
   {recipe&&<section aria-label="确定的处理规则"><h3>字段与计算规则</h3><div className="ag-review-table"><table><thead><tr><th>输出字段</th><th>输入来源</th></tr></thead><tbody>{Object.entries(recipe.mapping).map(([target,source])=><tr key={target}><td>{target}</td><td>{source}</td></tr>)}</tbody></table></div>
    <dl><dt>输入列</dt><dd>{recipe.inputColumns.join('、')}</dd><dt>允许空值的输入列</dt><dd>{recipe.optionalInputColumns?.length?recipe.optionalInputColumns.join('、'):'无'}</dd><dt>输出列</dt><dd>{recipe.outputColumns.join('、')}</dd><dt>输出格式</dt><dd>{recipe.outputFormat.toUpperCase()}</dd><dt>行数规则</dt><dd>{recipe.preserveRowCount?'逐行保留':'允许按以下规则改变行数'}</dd><dt>额外列</dt><dd>{recipe.allowExtraColumns?'允许':'出现未知列时停止并澄清'}</dd>{recipe.sheetName&&<><dt>输入工作表</dt><dd>{recipe.sheetName}</dd></>}{recipe.commercial&&<><dt>金额口径</dt><dd>{recipe.commercial.currency} · {{inclusive:'含税',exclusive:'未税',not_applicable:'税额不适用'}[recipe.commercial.taxBasis]} · 单位 {recipe.commercial.unit}</dd></>}</dl><p className="ag-muted">其他声明的输入列均须有值；缺失时停止处理并请求补充。</p>
    {(recipe.concat.length>0||recipe.operations.length>0||Object.keys(recipe.constants).length>0)&&<div><h4>拼接、常量与计算</h4><ol>{Object.entries(recipe.constants).map(([key,value])=><li key={'constant-'+key}>{key} 固定填写 {value}</li>)}{recipe.concat.map((step,i)=><li key={'concat-'+i}>{step.target}：以“{step.separator||'无分隔符'}”连接 {step.columns.join('、')}{step.padLast?`；最后一段补零至 ${step.padLast} 位`:''}</li>)}{recipe.operations.map((step,i)=><li key={'operation-'+i}>{describeOperation(step)}</li>)}</ol></div>}
    <details><summary>查看全部规则和校验条件</summary><pre>{JSON.stringify(recipe,null,2)}</pre></details>
   </section>}
   {preview&&<section aria-label="样例试运行结果"><h3>样例试运行</h3><p>输入 {preview.inputRowCount} 行 → 输出 {preview.rowCount} 行{preview.expectedOutputMatched?' · 与提供的正确样例一致':''}</p><div className="ag-review-table"><table><thead><tr>{preview.columns.map((column,i)=><th key={i}>{column}</th>)}</tr></thead><tbody>{preview.rows.map((row,i)=><tr key={i}>{row.map((value,j)=><td key={j}>{value}</td>)}</tr>)}</tbody></table></div>{preview.previewTruncated&&<p>这里显示部分样例；完整结果按上述规则生成。</p>}{Object.keys(preview.totals).length>0&&<dl>{Object.entries(preview.totals).map(([column,total])=><React.Fragment key={column}><dt>{column} 合计</dt><dd>{total}</dd></React.Fragment>)}</dl>}{preview.warnings.map((warning,i)=><p className="ag-review-warning" key={i}>{warning}</p>)}<details><summary>样例与产物校验</summary><p>输入：<code>{preview.inputSha256}</code></p><p>输出：<code>{preview.outputSha256}</code></p></details></section>}
   <section><h3>依据资料</h3><div className="ag-review-sources">{method.proposal.sources.map(source=><button key={source.kind+source.id} disabled={busy||!onSource} onClick={()=>onSource?.({kind:source.kind,id:source.id,title:source.name||source.fromAddress||'来源邮件'})}>{source.name||source.fromAddress||'来源邮件'}</button>)}{!method.proposal.sources.length&&<p>未附加来源邮件或文件。</p>}</div></section>
   <footer className="ag-review-actions">{method.status==='draft'&&<><p>核对范围、规则和样例后批准。批准新版本会替代同范围的旧版本。</p><button className="ag-primary" disabled={busy} onClick={()=>void review('approve')}>批准并保存此方法</button><button disabled={busy} onClick={()=>void review('disable')}>不采用此方法</button></>}{method.status==='approved'&&<button disabled={busy} onClick={()=>void review('disable')}>停用此方法</button>}{method.status==='disabled'&&<p>此版本已停用；需要修改时可在对话中提出新的学习版本。</p>}{method.status==='superseded'&&<p>此版本保留用于追溯，后续执行采用当前已批准版本。</p>}</footer>
  </>}
 </section>;
}

export function LearningLibrary({onOpen}:{onOpen(id:string):void}){
 const [methods,setMethods]=useState<LearningMethod[]>([]),[status,setStatus]=useState<'all'|LearningMethod['status']>('approved'),[error,setError]=useState(''),[busy,setBusy]=useState(false);
 const generation=useRef(0);
 async function refresh(){const current=++generation.current;setBusy(true);setError('');try{if(!window.learning)throw Error('学习方法接口尚未加载。');const values=unwrap(await window.learning.list({limit:200}));if(current===generation.current)setMethods(values);}catch(e){if(current===generation.current)setError(e instanceof Error?e.message:'读取方法列表失败');}finally{if(current===generation.current)setBusy(false);}}
 useEffect(()=>{void refresh();return()=>{generation.current++;};},[]);
 const visible=methods.filter(method=>status==='all'||method.status===status);
 return <section className="ag-review" aria-label="已保存的学习方法"><p>查看已批准的经验、表格处理方法，以及各次修订。</p><div className="ag-review-actions"><label>查看范围<select aria-label="方法状态" value={status} onChange={e=>setStatus(e.target.value as typeof status)}><option value="approved">已批准</option><option value="draft">待确认</option><option value="all">所有版本</option><option value="disabled">已停用</option></select></label><button disabled={busy} onClick={()=>void refresh()}>刷新方法</button></div>{error&&<p role="alert" className="ag-error">{error}</p>}{busy&&<p role="status">正在读取方法…</p>}<div className="ag-method-list">{visible.map(method=><button key={method.id} onClick={()=>onOpen(method.id)}><strong>{method.title}</strong><span>{method.scope.label} · 版本 {method.version} · {statusLabels[method.status]}</span><small>{method.scope.emails.join('、')||'个人范围'}</small></button>)}</div>{!busy&&!error&&!visible.length&&<p>此分类下还没有学习方法。可以在对话中说明一种处理方法，请助手先用样例验证后提交审核。</p>}</section>;
}
