import React,{useEffect,useState} from 'react';
import type {LocalMailFolder} from '../shared/mail-workspace';
export function MailArchive({selected,folder,onFolder,onChanged,disabled,mode='all'}:{selected:string[];folder:string|undefined;onFolder(value:string|undefined):void;onChanged():Promise<void>;disabled:boolean;mode?:'all'|'navigation'|'actions'}){
 const [folders,setFolders]=useState<LocalMailFolder[]>([]),[newName,setNewName]=useState(''),[creating,setCreating]=useState(false),[editingId,setEditingId]=useState<string|null>(null),[editName,setEditName]=useState(''),[moveTo,setMoveTo]=useState(''),[remove,setRemove]=useState(false),[error,setError]=useState(''),[working,setWorking]=useState(false);
 const unwrap=<T,>(r:import('../shared/contracts').Result<T>)=>{if(!r.ok)throw Error(r.error);return r.data;};
 async function load(){setFolders(unwrap(await window.mailWorkspace.listFoldersLocal()));}
 useEffect(()=>{void load().catch(e=>setError(e.message));},[]);
 async function run(f:()=>Promise<void>){setWorking(true);setError('');try{await f();await load();await onChanged();}catch(e){setError(e instanceof Error?e.message:'本地归档失败');}finally{setWorking(false);}}
 const locked=disabled||working;
 function startRename(item:LocalMailFolder){setEditingId(item.id);setEditName(item.name);}
 function cancelRename(){setEditingId(null);setEditName('');}
 return <section className="mw-archive"><h3>{mode==='actions'?'选中邮件操作':'本地邮件'}</h3>{error&&<p role="alert">{error}</p>}
 {mode!=='actions'&&<nav className="mw-local-tree" aria-label="本地文件夹">
  <ul role="tree">
   <li role="treeitem" aria-selected={folder===undefined}><button type="button" className={'mw-tree-folder'+(folder===undefined?' active':'')} disabled={locked} onClick={()=>onFolder(undefined)}>全部本地邮件</button></li>
   <li role="treeitem" aria-selected={folder===''}><button type="button" className={'mw-tree-folder'+(folder===''?' active':'')} disabled={locked} onClick={()=>onFolder('')}>未归类</button></li>
   <li role="treeitem" aria-expanded="true"><span className="mw-tree-group">本地文件夹</span>
    <ul role="group">{folders.map(item=><li key={item.id} role="treeitem" aria-selected={folder===item.id}>
     {editingId===item.id?<form className="mw-inline-edit" onSubmit={event=>{event.preventDefault();void run(async()=>{unwrap(await window.mailWorkspace.renameLocalFolder(item.id,editName.trim()));cancelRename();});}}><input autoFocus aria-label={`新文件夹名称 ${item.name}`} maxLength={80} value={editName} disabled={locked} onChange={event=>setEditName(event.target.value)}/><button type="submit" disabled={locked||!editName.trim()}>保存</button><button type="button" disabled={locked} onClick={cancelRename}>取消</button></form>
     :<div className="mw-folder-row"><button type="button" className={'mw-tree-folder'+(folder===item.id?' active':'')} disabled={locked} onClick={()=>onFolder(item.id)}>{item.name}</button><button type="button" className="mw-folder-action" aria-label={`重命名文件夹 ${item.name}`} disabled={locked} onClick={()=>startRename(item)}>重命名</button><button type="button" className="mw-folder-action" aria-label={`删除文件夹 ${item.name}`} disabled={locked} onClick={()=>void run(async()=>{unwrap(await window.mailWorkspace.deleteLocalFolder(item.id));if(folder===item.id)onFolder(undefined);if(editingId===item.id)cancelRename();})}>删除</button></div>}
    </li>)}</ul>
   </li>
  </ul>
  {creating?<form className="mw-inline-edit mw-new-folder" onSubmit={event=>{event.preventDefault();void run(async()=>{unwrap(await window.mailWorkspace.createLocalFolder(newName.trim()));setNewName('');setCreating(false);});}}><input autoFocus aria-label="新文件夹名称" maxLength={80} value={newName} disabled={locked} onChange={event=>setNewName(event.target.value)}/><button type="submit" disabled={locked||!newName.trim()}>保存</button><button type="button" disabled={locked} onClick={()=>{setCreating(false);setNewName('');}}>取消</button></form>
  :<button type="button" className="mw-new-folder" disabled={locked} onClick={()=>{setCreating(true);setNewName('');}}>新建文件夹</button>}
 </nav>}
 {mode!=='navigation'&&<><label>将勾选邮件移至<select value={moveTo} disabled={locked} onChange={e=>setMoveTo(e.target.value)}><option value="">未归类</option>{folders.map(f=><option key={f.id} value={f.id}>{f.name}</option>)}</select></label><div className="mw-actions"><button disabled={locked||!selected.length} onClick={()=>void run(async()=>{unwrap(await window.mailWorkspace.moveLocal(selected,moveTo||null));})}>移动 {selected.length} 封</button><button disabled={locked||!selected.length} onClick={()=>setRemove(true)}>删除本地邮件</button></div>{remove&&<div className="mw-delete-confirm"><strong>删除选中的 {selected.length} 封本地邮件？</strong><p>正文会从本机邮件库删除；导入与分析记忆保留，下次同步不会自动恢复，也不会删除邮箱服务器上的邮件。</p><button disabled={locked} onClick={()=>void run(async()=>{unwrap(await window.mailWorkspace.deleteLocal(selected));setRemove(false);})}>确认删除正文</button><button disabled={locked} onClick={()=>setRemove(false)}>取消</button></div>}</>}
 </section>;
}