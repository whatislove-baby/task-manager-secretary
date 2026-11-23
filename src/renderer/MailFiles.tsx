import React,{useEffect,useRef,useState} from 'react';
import type {FileRecord} from '../shared/mail-files';
import type {Result} from '../shared/contracts';
const unwrap=<T,>(r:Result<T>):T=>{if(!r.ok)throw Error(r.error);return r.data;};
export const fileSize=(n:number)=>n<1024?`${n} B`:`${(n/1024).toFixed(1)} KB`;

type TablePreview={columns:string[];rows:string[][];rowCount:number;offset:number;nextOffset:number|null;warnings:string[]};
const pngData=/^data:image\/png;base64,[A-Za-z0-9+/]+={0,2}$/;

export function FileCard({id,onAttach,onSelect,onLearning,onSkill,disabled=false}:{id:string;onAttach?(id:string,sourceMailId?:string):void;onSelect?(id:string):void;onLearning?(id:string):void;onSkill?(id:string):void;disabled?:boolean}){
 const [file,setFile]=useState<FileRecord|null>(null),[text,setText]=useState<string|null>(null),[busy,setBusy]=useState(false),[error,setError]=useState(''),[notice,setNotice]=useState('');
 const [preview,setPreview]=useState<import('../shared/mail-files').FilePreview|null>(null),[table,setTable]=useState<TablePreview|null>(null),[validation,setValidation]=useState<import('../shared/mail-files').FileValidation|null>(null);
 const generation=useRef(0),request=useRef(0),currentId=useRef(id),lock=useRef(false);currentId.current=id;
 useEffect(()=>{
  const version=++generation.current;request.current++;lock.current=false;
  setFile(null);setText(null);setPreview(null);setTable(null);setValidation(null);setBusy(false);setError('');setNotice('');
  let live=true;
  void window.mailFiles!.get(id).then(r=>{
   if(!live||generation.current!==version||currentId.current!==id)return;
   const next=unwrap(r);if(next.id!==id)throw Error('文件信息不匹配，请重新打开。');
   setFile(next);if(next.validation?.sha256===next.sha256)setValidation(next.validation);
  }).catch(e=>{if(live&&generation.current===version&&currentId.current===id)setError(e instanceof Error?e.message:'文件信息读取失败，请重新打开。');});
  return()=>{live=false;generation.current++;request.current++;lock.current=false;};
 },[id]);
 async function run<T,>(work:()=>Promise<T>,apply:(value:T)=>void){
  if(lock.current)return;
  lock.current=true;const version=generation.current,sequence=++request.current;
  const current=()=>generation.current===version&&request.current===sequence&&currentId.current===id;
  setBusy(true);setError('');setNotice('');
  try{const value=await work();if(current())apply(value);}
  catch(e){if(current())setError(e instanceof Error?e.message:'文件操作未完成');}
  finally{if(current()){lock.current=false;setBusy(false);}}
 }
 function loadPreview(page:number){
  void run(async()=>{
   const result=unwrap(await window.mailFiles!.preview!(id,page));
   if(result.fileId!==id||result.sha256!==file?.sha256||result.page!==page||!Number.isInteger(result.pageCount)||result.pageCount<page||!result.rendered)throw Error('页面预览与当前文件版本不匹配。');
   if(typeof result.dataUrl!=='string'||result.dataUrl.length>20*1024*1024||!pngData.test(result.dataUrl))throw Error('页面预览格式无效；只显示本地 PNG 页面。');
   return result;
  },setPreview);
 }
 function loadTable(offset:number){
  void run(async()=>{
   const result=unwrap(await window.mailFiles!.inspect!(id,offset,30));
   const columns=result.columns,rows=result.rows,total=result.rowCount;
   const validCell=(value:unknown)=>typeof value==='string'||(file?.extension==='xlsx'&&(value===null||typeof value==='boolean'||(typeof value==='number'&&Number.isFinite(value))));
   if(result.fileId&&result.fileId!==id)throw Error('表格与当前文件不匹配。');
   if(!Array.isArray(columns)||!columns.length||!columns.every(v=>typeof v==='string')||!Array.isArray(rows)||!rows.every(row=>Array.isArray(row)&&row.length===columns.length&&row.every(validCell))||typeof total!=='number'||!Number.isInteger(total)||total<offset+rows.length)throw Error('表格数据不完整，请重新读取。');
   const next=offset+rows.length<total?offset+rows.length:null;
   if(next!==null&&next<=offset)throw Error('表格没有返回后续行，请重新读取。');
   if(result.nextOffset!==undefined&&result.nextOffset!==next)throw Error('表格分页信息不一致，请重新读取。');
   const displayRows=rows.map(row=>row.map(value=>value===null?'':typeof value==='boolean'?(value?'TRUE':'FALSE'):String(value)));
   return {columns,rows:displayRows,rowCount:total,offset,nextOffset:next,warnings:result.warnings??[]};
  },setTable);
 }
 function checkFile(){
  setValidation(null);
  void run(async()=>{
   const result=unwrap(await window.mailFiles!.validate!(id));
   if(result.sha256!==file?.sha256)throw Error('检查结果与当前文件版本不匹配。');
   return result;
  },setValidation);
 }
 const shown=file?.id===id?file:null,api=window.mailFiles;
 return <div className="mf-card" aria-label={shown?`文件 ${shown.name}`:'文件'} aria-busy={busy}>
  {shown&&<><strong>{shown.name}</strong><small>{fileSize(shown.byteSize)} · {shown.source==='generated'?'生成副本':shown.source==='mail'?'邮件附件副本':'本地资料副本'} · 版本 {shown.sha256.slice(0,10)}</small>{shown.warnings.map((warning,i)=><p key={i} className="mf-warning">{warning}</p>)}
   {shown.archiveOrigin&&<section className="mf-lineage" aria-label="压缩包来源"><strong>压缩包内文件</strong><p>{shown.archiveOrigin.memberPath}</p><small>来自压缩包版本 {shown.archiveOrigin.archiveSha256.slice(0,16)} · 第 {shown.archiveOrigin.depth} 层</small></section>}
   {shown.skill&&<section className="mf-lineage" aria-label="应用的技能"><strong>按已批准技能生成 · 版本 {shown.skill.approvedVersion}</strong><small>技能校验 {shown.skill.versionHash.slice(0,16)} · {shown.skill.inputIds.length} 份输入资料</small>{onSkill&&<button type="button" disabled={busy||disabled} onClick={()=>onSkill(shown.skill!.versionId)}>查看生成技能</button>}</section>}
   {shown.learning&&<section className="mf-lineage" aria-label="应用的学习方法"><strong>按已批准方法生成 · 版本 {shown.learning.approvedVersion}</strong><small>方法校验 {shown.learning.versionHash.slice(0,16)} · 输入版本 {shown.learning.inputSha256.slice(0,16)}</small>{Object.entries(shown.learning.totals).map(([name,value])=><p key={name}>{name}：{value}</p>)}{onLearning&&<button type="button" disabled={busy||disabled} onClick={()=>onLearning(shown.learning!.versionId)}>查看处理方法</button>}</section>}
   {shown.extension==='zip'&&<p className="mf-warning">可以查看目录，或在对话中请助手按需解压并读取。RAR / 7z 暂不支持。</p>}
   <div className="mw-actions">
    {['pdf','docx','png','jpg','jpeg','webp'].includes(shown.extension)&&api?.preview&&<button type="button" disabled={busy||disabled} onClick={()=>preview?setPreview(null):loadPreview(1)}>{preview?'收起预览':'预览页面'}</button>}
    {['csv','xlsx'].includes(shown.extension)&&api?.inspect&&<button type="button" disabled={busy||disabled} onClick={()=>table?setTable(null):loadTable(0)}>{table?'收起表格':'查看表格'}</button>}
    <button type="button" disabled={busy||disabled} onClick={()=>text!==null?setText(null):void run(async()=>unwrap(await window.mailFiles!.read(id)),setText)}>{text!==null?'收起文本':shown.extension==='zip'?'查看压缩包目录':'查看提取文本'}</button>
    {api?.validate&&<button type="button" disabled={busy||disabled} onClick={checkFile}>检查文件</button>}
    <button type="button" disabled={busy||disabled} onClick={()=>void run(async()=>unwrap(await window.mailFiles!.export(id)),saved=>{if(saved)setNotice('文件已另存。');})}>另存文件</button>
    {onSelect&&<button type="button" disabled={busy||disabled} onClick={()=>onSelect(id)}>作为本轮资料</button>}
    {onAttach&&<button type="button" disabled={busy||disabled} onClick={()=>onAttach(id,shown.sourceMailId)}>加入邮件附件</button>}
   </div>
  </>}
  {busy&&<p className="mf-progress" role="status">正在处理文件…</p>}
  {error&&<p role="alert">{error}</p>}{notice&&<p role="status">{notice}</p>}
  {shown&&validation&&<section className="mf-validation" aria-label="文件检查结果">
   <strong>{validation.structuralPassed?'结构检查通过':'结构检查未通过'}</strong>
   {['pdf','docx','png','jpg','jpeg','webp'].includes(shown.extension)&&<p>{validation.rendered?`页面可渲染${validation.pageCount?` · 共 ${validation.pageCount} 页`:''}`:'页面渲染未通过或尚未完成'}</p>}
   {['csv','xlsx'].includes(shown.extension)&&validation.rowCount!==undefined&&<p>{validation.rowCount} 行 · {validation.columnCount??'?'} 列</p>}
   <p>{validation.visualReviewed?'已记录视觉复核；请结合预览核对内容。':'自动检查不代表内容或排版正确，请核对预览。'}</p>
   {validation.warnings.map((warning,i)=><p key={i} className="mf-warning">{warning}</p>)}
  </section>}
  {shown&&preview&&<section className="mf-preview" aria-label="文件页面预览">
   <div className="mf-pagination"><span>第 {preview.page} / {preview.pageCount} 页</span><button type="button" aria-label="预览上一页" disabled={busy||disabled||preview.page<=1} onClick={()=>loadPreview(preview.page-1)}>上一页</button><button type="button" aria-label="预览下一页" disabled={busy||disabled||preview.page>=preview.pageCount} onClick={()=>loadPreview(preview.page+1)}>下一页</button></div>
   <div className="mf-preview-image"><img src={preview.dataUrl} alt={`${shown.name} 第 ${preview.page} 页`} onError={()=>setError('页面图片无法显示，请重新预览。')}/></div>
  </section>}
  {shown&&table&&<section className="mf-table-preview" aria-label={shown.extension==='xlsx'?'XLSX 表格预览':'CSV 表格预览'}>
   <div className="mf-pagination"><span>{table.rowCount===0?'0 行':`${table.offset+1}–${table.offset+table.rows.length} / ${table.rowCount} 行`}</span><button type="button" aria-label="表格上一页" disabled={busy||disabled||table.offset===0} onClick={()=>loadTable(Math.max(0,table.offset-30))}>上一页</button><button type="button" aria-label="表格下一页" disabled={busy||disabled||table.nextOffset===null} onClick={()=>loadTable(table.nextOffset!)}>下一页</button></div>
   <div className="mf-table-scroll" tabIndex={0}><table><thead><tr>{table.columns.map((column,i)=><th key={i} scope="col">{column}</th>)}</tr></thead><tbody>{table.rows.map((row,i)=><tr key={table.offset+i}>{row.map((value,j)=><td key={j}>{value}</td>)}</tr>)}</tbody></table>{!table.rows.length&&<p>表格没有数据行。</p>}</div>
   {table.warnings.map((warning,i)=><p key={i} className="mf-warning">{warning}</p>)}
  </section>}
  {shown&&text!==null&&<pre className="mf-text">{text}</pre>}
 </div>;
}

/** Picking/importing is local only. AI upload happens only in the parent's explicit submit. */
export function FilePicker({ids,onChange,disabled=false,label='本轮参考资料',max=6,onBusy}:{ids:string[];onChange(ids:string[]):void;disabled?:boolean;label?:string;max?:number;onBusy?(value:boolean):void}){
 const [files,setFiles]=useState<FileRecord[]>([]),[busy,setBusy]=useState(false),[error,setError]=useState('');const lock=useRef(false);
 useEffect(()=>{onBusy?.(busy);return()=>onBusy?.(false);},[busy,onBusy]);
 const key=ids.join(',');
 async function load(){const values=unwrap(await window.mailFiles!.list());const missing=await Promise.all(ids.filter(id=>!values.some(f=>f.id===id)).map(async id=>unwrap(await window.mailFiles!.get(id))));setFiles([...missing,...values]);}
 useEffect(()=>{void load().catch(e=>setError(e.message));},[key]);
 async function run(work:()=>Promise<void>){if(lock.current)return;lock.current=true;setBusy(true);setError('');try{await work();}catch(e){setError(e instanceof Error?e.message:'文件操作未完成');await load().catch(()=>{});}finally{lock.current=false;setBusy(false);}}
 return <section className="mf-picker" aria-label={label}>
  <div className="mw-actions"><strong>{label} · 已选 {ids.length}/{max}</strong><button type="button" disabled={disabled||busy} onClick={()=>void run(async()=>{const values=unwrap(await window.mailFiles!.importLocal());if(!values)return;await load();const next=[...new Set([...ids,...values.map(v=>v.id)])];if(next.length>max){setError(`本轮最多 ${max} 份；文件已导入，请在下方选择。`);return;}onChange(next);})}>添加本地资料</button><button type="button" disabled={disabled||busy} onClick={()=>void run(load)}>刷新文件列表</button></div>
  <p>支持 PDF、Word（.docx）、CSV / XLSX、TXT / Markdown、PNG / JPEG / WebP 图片和 ZIP。添加会保留本地副本，尚不会发送给 AI。</p>
  {error&&<p role="alert">{error}</p>}
  <div className="mf-options">{files.map(file=><label key={file.id}><input type="checkbox" checked={ids.includes(file.id)} disabled={disabled||busy||(!ids.includes(file.id)&&ids.length>=max)} onChange={e=>onChange(e.target.checked?[...ids,file.id]:ids.filter(id=>id!==file.id))}/><span>{file.name}<small>{fileSize(file.byteSize)} · {file.sha256.slice(0,10)}</small></span></label>)}{!files.length&&<p>还没有资料。选择本地文件，或从邮件详情中读取一个附件。</p>}</div>
 </section>;
}
