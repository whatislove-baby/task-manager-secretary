import React,{useEffect,useRef,useState} from 'react';
import type {Result} from '../shared/contracts';
import type {AgentSource,LearningTeaching} from '../shared/agent';
import type {SkillKind,SkillRecord,SkillStatus} from '../shared/skills';
import {AgentMessageBody} from './AgentMessageBody';

const unwrap=<T,>(result:Result<T>):T=>{if(!result.ok)throw Error(result.error);return result.data;};
const statuses:Record<SkillStatus,string>={draft:'待确认',approved:'已批准',disabled:'已停用',superseded:'已被新版本替代'};
const kinds:Record<SkillKind,string>={memory:'文字经验',script:'脚本技能',hybrid:'经验与脚本'};
const comparisons={table:'逐格比对表格内容',workbook:'核对工作表、数据类型与模板结构',text:'比对文本（统一换行）',bytes:'逐字节比对文件'};
const scopes={customer:'客户',course:'课程',personal:'个人'};
const time=(value:string)=>{const date=new Date(value);return Number.isNaN(date.getTime())?value:date.toLocaleString('zh-CN',{timeZone:'Asia/Hong_Kong',hour12:false})+'（香港时间）';};

function OriginalTeaching({teaching}:{teaching?:LearningTeaching}){
 return <section aria-label="技能原始教学"><h3>原始用户指导</h3>{teaching?.source==='conversation'?<>
  <p className="ag-muted">这是保存的对话原文；下面的分类和说明由助手整理，请一并核对。</p>
  <details open><summary>查看 {teaching.turns.length} 条用户原文</summary>{teaching.turns.map(turn=><article className="ag-teaching-turn" key={turn.id}>
   <time dateTime={turn.createdAt}>{time(turn.createdAt)}</time><blockquote>{turn.content}</blockquote>
  </article>)}</details>{teaching.omittedUserTurns>0&&<p className="ag-review-warning">另有 {teaching.omittedUserTurns} 条较早用户消息未包含在此版本证据中。</p>}
 </>:<p className="ag-muted">{teaching?'直接输入；没有可验证的原始对话。':'此版本没有原始对话证据。'}</p>}</section>;
}

export function SkillReview({id,onChanged,onSource,onBusy}:{id:string;onChanged?():void|Promise<void>;onSource?(source:AgentSource):void;onBusy?(busy:boolean):void}){
 const [skill,setSkill]=useState<SkillRecord|null>(null),[error,setError]=useState(''),[notice,setNotice]=useState(''),[busy,setBusy]=useState(false);
 const generation=useRef(0),locked=useRef(false);
 useEffect(()=>{
  const current=++generation.current;setSkill(null);setError('');setNotice('');setBusy(false);locked.current=false;
  if(!window.skills){setError('技能库接口尚未加载，请重新打开更新后的应用。');return;}
  void window.skills.get(id).then(result=>{if(current!==generation.current)return;const next=unwrap(result);if(next.id!==id)throw Error('技能版本与所选内容不一致，请重新打开。');setSkill(next);}).catch(e=>{if(current===generation.current)setError(e instanceof Error?e.message:'读取技能失败');});
  return()=>{generation.current++;};
 },[id]);
 useEffect(()=>{onBusy?.(busy);return()=>onBusy?.(false);},[busy,onBusy]);
 async function review(action:'approve'|'disable'){
  if(!skill||!window.skills||locked.current||(action==='approve'&&!skill.canApprove))return;
  const current=generation.current;locked.current=true;setBusy(true);setError('');setNotice('');
  try{
   const next=unwrap(await window.skills[action]({id:skill.id,expectedHash:skill.versionHash}));
   if(current!==generation.current)return;
   if(next.id!==skill.id)throw Error('返回的技能版本不一致，请重新读取。');
   setSkill(next);setNotice(action==='approve'?'此版本已保存到技能库，可在适用范围内复用。':'此版本已停用，后续任务不会再调用它。');await onChanged?.();
  }catch(e){if(current===generation.current)setError(e instanceof Error?e.message:'技能审核未完成');}
  finally{if(current===generation.current){locked.current=false;setBusy(false);}}
 }
 const proposal=skill?.proposal,validation=skill?.validation;
 const sources:AgentSource[]=proposal?.sources.map(source=>({kind:source.kind,id:source.id,title:source.name||source.fromAddress||'来源邮件'}))??[];
 return <section className="ag-review" aria-label="技能审核" aria-busy={busy}>
  {error&&<p className="ag-error" role="alert">{error}</p>}{notice&&<p role="status">{notice}</p>}
  {!skill&&!error&&<p role="status">正在读取技能…</p>}
  {skill&&proposal&&validation&&<>
   <header><div><h2>{skill.title}</h2><p>{statuses[skill.status]} · 版本 {skill.version} · {kinds[proposal.kind]}</p></div></header>
   <section aria-label="技能适用范围"><h3>适用范围</h3><dl><dt>对象</dt><dd>{scopes[skill.scope.kind]} · {skill.scope.label}</dd><dt>发件邮箱</dt><dd>{skill.scope.emails.join('、')||'未关联邮箱'}</dd>{skill.scope.contextKey&&<><dt>课程 / 上下文</dt><dd>{skill.scope.contextKey}</dd></>}</dl><p className="ag-muted">按此范围检索和调用。同名联系人不会因此共享技能。</p></section>
   <section aria-label="技能分类理由"><h3>为什么这样保存</h3><p><strong>{kinds[proposal.kind]}</strong></p><p className="ag-review-prose">{proposal.rationale}</p></section>
   <OriginalTeaching teaching={proposal.teaching}/>
   {proposal.importedPackage&&<section aria-label="导入技能来源"><h3>已开发的技能包</h3><p>导入用户提供的代码和资源，业务程序未经助手重写。用途与适用范围以上方用户指导为准。</p><button disabled={busy||!onSource} onClick={()=>onSource?.({kind:'file',id:proposal.importedPackage!.sourceFileId,title:'原始技能 ZIP'})}>查看原始技能包</button><p>运行资源：{proposal.importedPackage.runtimeFiles.join('、')||'仅 main.py'}</p>{proposal.importedPackage.supplement&&<p className="ag-review-prose">补充说明：{proposal.importedPackage.supplement}</p>}<details><summary>查看原包校验记录</summary><p>ZIP SHA-256：<code>{proposal.importedPackage.sourceSha256}</code></p><pre>{JSON.stringify(proposal.importedPackage.originalFiles,null,2)}</pre></details></section>}
   <section aria-label="技能说明"><h3>处理说明</h3><AgentMessageBody text={proposal.instruction} sources={sources} onSource={onSource}/>
    <details><summary>查看保存的 SKILL.md</summary><pre>{proposal.markdown}</pre></details>
    {skill.parentId&&<button disabled={busy||!onSource} onClick={()=>onSource?.({kind:'skill',id:skill.parentId!,title:'上一版本技能'})}>查看上一版本</button>}
   </section>
   {proposal.kind!=='memory'&&<>
    <section aria-label="技能输入输出"><h3>输入与输出</h3><div className="ag-review-table"><table><thead><tr><th>类型</th><th>文件名</th><th>约定</th></tr></thead><tbody>
     {proposal.inputs.map(input=><tr key={'in-'+input.name}><td>输入</td><td>{input.name}</td><td>{input.description}</td></tr>)}
     {proposal.outputs.map(output=><tr key={'out-'+output.name}><td>输出</td><td>{output.name}</td><td>{output.description}<br/>{comparisons[output.comparison]}</td></tr>)}
    </tbody></table></div></section>
    <section aria-label="技能脚本"><h3>处理脚本</h3><p className="ag-muted">这是本版本保存的 Python 代码。查看或批准不会在此页面执行脚本。</p><details><summary>查看 main.py</summary><pre><code>{proposal.code}</code></pre></details></section>
   </>}
   <section aria-label="技能验证结果"><h3>{proposal.kind==='memory'?'文字经验核对':'样例验证'}</h3>
    {proposal.kind==='memory'?<p>文字经验不执行脚本；请核对原始指导、适用对象和长期规则。没有自动验证语义正确。</p>:<>
     <p><strong>{validation.passed?'样例验证通过':'样例验证未通过'}</strong> · 正常样例 {validation.normalExamples??validation.cases.filter(c=>c.kind==='success').length} · 失败样例 {validation.failureExamples??validation.cases.filter(c=>c.kind==='failure').length}</p>
     {(validation.summary||validation.detail)&&<p>{validation.summary||validation.detail}</p>}
     <div className="ag-review-table"><table><thead><tr><th>样例</th><th>预期</th><th>结果</th><th>说明</th></tr></thead><tbody>{validation.cases.map((item,i)=><tr key={i}><td>{item.name}</td><td>{item.kind==='success'?'产生匹配结果':'拒绝不合规输入'}</td><td>{item.passed?'通过':'未通过'}</td><td>{item.detail||'—'}{item.exitCode!==undefined&&item.exitCode!==null&&<> · 退出码 {item.exitCode}</>}
      {(item.stdout||item.stderr||item.runnerError)&&<details><summary>查看运行记录</summary>{item.stdout&&<><p>标准输出</p><pre>{item.stdout}</pre></>}{item.stderr&&<><p>错误输出</p><pre>{item.stderr}</pre></>}{item.runnerError&&<p>{item.runnerError}</p>}</details>}
     </td></tr>)}</tbody></table></div>
     <p className="ag-muted">样例通过不代表所有新输入都正确；还需核对业务范围与比较方式。表格内容比对不等于版式检查。</p>
    </>}
    {skill.status==='draft'&&!skill.canApprove&&<p className="ag-review-warning">此版本暂不能批准。请在对话中补充样例或修正问题，再生成可审核的新版本。</p>}
   </section>
   <section><h3>依据资料</h3><div className="ag-review-sources">{sources.map(source=><button key={source.kind+source.id} disabled={busy||!onSource} onClick={()=>onSource?.(source)}>{source.title}</button>)}{!sources.length&&<p>未附加来源文件或邮件。</p>}</div></section>
   <details aria-label="技能版本校验"><summary>技能包与版本记录</summary><p>位置：<code>{skill.packagePath||'由本地技能库管理'}</code></p><p>版本：<code>{skill.versionHash}</code></p>{proposal.packageManifest&&<pre>{JSON.stringify(proposal.packageManifest,null,2)}</pre>}</details>
   <footer className="ag-review-actions">
    {skill.status==='draft'&&<><p>确认后，此版本可在新对话中检索复用。批准新版本会替代此技能的旧版本。</p><button className="ag-primary" disabled={busy||!skill.canApprove} onClick={()=>void review('approve')}>批准并保存到技能库</button><button disabled={busy} onClick={()=>void review('disable')}>不采用此技能</button></>}
    {skill.status==='approved'&&<button disabled={busy} onClick={()=>void review('disable')}>停用此技能</button>}
    {skill.status==='disabled'&&<p>此版本已停用。可在对话中提出修订，再审核新版本。</p>}
    {skill.status==='superseded'&&<p>此版本保留用于追溯，后续任务使用当前已批准版本。</p>}
   </footer>
  </>}
 </section>;
}

export function SkillLibrary({onOpen}:{onOpen(id:string):void}){
 const [items,setItems]=useState<SkillRecord[]>([]),[status,setStatus]=useState<SkillStatus|'all'>('approved'),[error,setError]=useState(''),[busy,setBusy]=useState(false);
 const generation=useRef(0);
 async function refresh(){const current=++generation.current;setBusy(true);setError('');try{if(!window.skills)throw Error('技能库接口尚未加载。');const data=unwrap(await window.skills.list({limit:200}));if(current===generation.current)setItems(data);}catch(e){if(current===generation.current)setError(e instanceof Error?e.message:'读取技能库失败');}finally{if(current===generation.current)setBusy(false);}}
 useEffect(()=>{void refresh();return()=>{generation.current++;};},[]);
 const visible=items.filter(item=>status==='all'||item.status===status);
 return <section className="ag-review" aria-label="已保存的技能库"><p>已导入的业务技能和用户指导形成的文字经验，按对象与版本保存。在对话中上传现成技能 ZIP，并说明用途和适用的邮件或会话；首次核对后，以后直接复用。</p><div className="ag-review-actions"><label>查看范围<select aria-label="技能状态" value={status} onChange={e=>setStatus(e.target.value as typeof status)}><option value="approved">已批准</option><option value="draft">待确认</option><option value="all">所有版本</option><option value="disabled">已停用</option></select></label><button disabled={busy} onClick={()=>void refresh()}>刷新技能库</button></div>
  {error&&<p className="ag-error" role="alert">{error}</p>}{busy&&<p role="status">正在读取技能库…</p>}
  <div className="ag-method-list">{visible.map(item=><button key={item.id} onClick={()=>onOpen(item.id)}><strong>{item.title}</strong><span>{kinds[item.proposal.kind]} · {item.scope.label} · 版本 {item.version} · {statuses[item.status]}</span><small>{item.scope.emails.join('、')||'个人范围'}</small></button>)}</div>
  {!busy&&!error&&!visible.length&&<p>此分类下还没有技能。可上传已经开发好的技能包，或告诉助手需要长期记住的沟通习惯、报告格式。</p>}
 </section>;
}
