import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import {mkdtemp,readFile,readdir,rm} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {dirname,join} from 'node:path';
import { dispatchPython } from '../src/main/python-desktop';
import type {AgentConversation} from '../src/shared/agent';

test('Python desktop dispatcher rejects untrusted frames before execution', async () => {
  let calls = 0;
  const backend = {call: async () => {calls++; return {ok:true as const,data:[]};}};
  const result = await dispatchPython(backend, false, 'backend:listTodos', [], () => {});
  assert.equal(result.ok, false);
  assert.equal(calls, 0);
});

test('secretary, child execution and saved briefing use the trusted IPC bridge with exact inputs',async()=>{
 const calls:unknown[][]=[];let changed=0;const backend={call:async(...args:unknown[])=>{calls.push(args);return {ok:true as const,data:{id:'value'}};}};
 const child={parentConversationId:'secretary',title:'课程报告',goal:'按参考资料写报告',mailIds:['mail-id'],fileIds:['file-id']};
 for(const channel of ['agent:getSecretary','agent:createChild','agent:getBriefing'])assert.equal((await dispatchPython(backend,false,channel,[child],()=>changed++)).ok,false);
 assert.deepEqual(calls,[]);await dispatchPython(backend,true,'agent:getSecretary',[],()=>changed++);await dispatchPython(backend,true,'agent:createChild',[child],()=>changed++);await dispatchPython(backend,true,'agent:getBriefing',['run-id'],()=>changed++);
 assert.deepEqual(calls,[['agent:getSecretary'],['agent:createChild',child],['agent:getBriefing','run-id']]);assert.equal(changed,1);
});

test('one-click inbox sync reaches only the trusted Python bridge and broadcasts changes', async () => {
  const calls:unknown[][]=[];let broadcasts=0;
  const backend={call:async(...args:unknown[])=>{calls.push(args);return {ok:true as const,data:{id:'sync',kind:'import',state:'queued'}};}};
  const changed=()=>broadcasts++;
  assert.equal((await dispatchPython(backend,false,'mail-operations:startInboxSync',[],changed)).ok,false);
  assert.deepEqual(calls,[]);
  assert.equal((await dispatchPython(backend,true,'mail-operations:startInboxSync',[],changed)).ok,true);
  assert.deepEqual(calls,[['mail-operations:startInboxSync']]);assert.equal(broadcasts,1);
});

test('successful task mutation broadcasts while reads and failures do not', async () => {
  let broadcasts = 0;
  const backend = {call: async (method:string) => method === 'updateTodo' ? {ok:false as const,error:'conflict'} : {ok:true as const,data:[]}};
  const changed = () => broadcasts++;
  await dispatchPython(backend, true, 'backend:listTodos', [], changed);
  await dispatchPython(backend, true, 'backend:createTodo', [{title:'a'}], changed);
  await dispatchPython(backend, true, 'backend:updateTodo', [], changed);
  assert.equal(broadcasts, 1);
  const unavailable = await dispatchPython(backend, true, 'mail:startAnalysis', [], changed);
  assert.equal(unavailable.ok, false);
  assert.equal(broadcasts, 1);
});

test('backup dialogs supply paths and renderer cannot supply arbitrary file paths', async () => {
  const calls:unknown[]=[];
  const backend={call:async(...args:unknown[])=>{calls.push(args);return {ok:true as const,data:true};}};
  const ports={chooseBackup:async()=>'/selected.json',saveBackup:async()=>'/export.json'};
  let broadcasts=0;
  assert.equal((await dispatchPython(backend,true,'backend:importBackup',[],()=>broadcasts++,ports)).ok,true);
  assert.deepEqual(calls,[['importBackupFile','/selected.json']]);
  assert.equal(broadcasts,1);
  assert.equal((await dispatchPython(backend,true,'backend:exportBackup',['/untrusted'],()=>{},ports)).ok,false);
  assert.equal((await dispatchPython(backend,true,'backend:importBackupFile',['/untrusted'],()=>{},ports)).ok,false);
  assert.equal(calls.length,1);
});


test('file import and export accept only native picker paths and opaque IDs', async () => {
  const calls:unknown[][]=[];let changed=0;
  const backend={call:async(...args:unknown[])=>{calls.push(args);return {ok:true as const,data:args[0]==='mail-files:get'?{name:'report.pdf'}:{id:'safe-id'}};}};
  const ports={chooseFiles:async()=>['/selected/reference.pdf','/selected/notes.txt'],saveFile:async(name:string)=>'/chosen/'+name};
  assert.equal((await dispatchPython(backend,true,'mail-files:importLocal',['/etc/passwd'],()=>{},ports)).ok,false);
  assert.equal((await dispatchPython(backend,true,'mail-files:importLocalFile',['/etc/passwd'],()=>{},ports)).ok,false);
  assert.equal((await dispatchPython(backend,true,'mail-files:exportFile',['id','/etc/passwd'],()=>{},ports)).ok,false);
  assert.equal((await dispatchPython(backend,false,'mail-files:importLocal',[],()=>{},ports)).ok,false);
  assert.deepEqual(calls,[]);
  const imported=await dispatchPython(backend,true,'mail-files:importLocal',[],()=>changed++,ports);
  assert.deepEqual(imported,{ok:true,data:[{id:'safe-id'},{id:'safe-id'}]});
  assert.deepEqual(calls,[['mail-files:importLocalFile','/selected/reference.pdf'],['mail-files:importLocalFile','/selected/notes.txt']]);
  assert.equal(changed,1);
  await dispatchPython(backend,true,'mail-files:export',['safe-id'],()=>{},ports);
  assert.deepEqual(calls.slice(2),[['mail-files:get','safe-id'],['mail-files:exportFile','safe-id','/chosen/report.pdf']]);
});

test('canceled file selection does no I/O and a partial import is visible', async () => {
  let calls=0,changed=0;
  const backend={call:async()=>++calls===1?{ok:true as const,data:{id:'first'}}:{ok:false as const,error:'Unreadable PDF'}};
  assert.deepEqual(await dispatchPython(backend,true,'mail-files:importLocal',[],()=>changed++,{chooseFiles:async()=>null}),{ok:true,data:null});
  assert.equal(calls,0);
  const result=await dispatchPython(backend,true,'mail-files:importLocal',[],()=>changed++,{chooseFiles:async()=>['a','b']});
  assert.equal(result.ok,false);
  assert.equal(changed,1);
  if(!result.ok)assert.match(result.error,/已成功导入/);
});

const exportId='4829cf2e-ef56-401d-9335-af997d587bee';
const exportInput={id:exportId,includeSources:false};
const exportDate='2026-11-21T01:00:00Z';
const exportConversation:AgentConversation={id:exportId,title:'课程讨论',createdAt:exportDate,updatedAt:exportDate,turnCount:1,turns:[{id:'turn',role:'user',content:'从本地后端读取的消息',createdAt:exportDate}]};

test('conversation export rejects untrusted callers and accepts only a conversation UUID and boolean',async()=>{
 const backend={call:async()=>{throw Error('unexpected backend call');}};
 const ports={saveFile:async()=>{throw Error('unexpected save dialog');}};
 const changed=()=>assert.fail('export must not broadcast');
 assert.match((await dispatchPython(backend,false,'agent:exportConversation',[exportInput],changed,ports) as {error:string}).error,/未知页面/);
 for(const args of [[],[exportId],[null],[{id:exportId}],[{...exportInput,id:'../notes.md'}],[{...exportInput,includeSources:'false'}],[{...exportInput,path:'/untrusted.md'}],[{...exportInput,content:'untrusted content'}],[exportInput,true]]){
  assert.match((await dispatchPython(backend,true,'agent:exportConversation',args,changed,ports) as {error:string}).error,/无效/);
 }
});

test('conversation export writes only fresh backend content to the path chosen by the desktop',async t=>{
 const directory=await mkdtemp(join(tmpdir(),'tm-conversation-export-'));
 t.after(()=>rm(directory,{recursive:true,force:true}));
 const path=join(directory,'chosen.md');
 await fs.writeFile(path,'previous saved version');
 const calls:unknown[][]=[];
 const source={kind:'file' as const,id:'private-id',title:'参考.pdf',excerpt:'私有摘要'};
 const current={...exportConversation,turns:exportConversation.turns.map(turn=>({...turn,sources:[source]}))};
 const backend={call:async(...args:unknown[])=>{calls.push(args);return {ok:true as const,data:current};}};
 const ports={saveFile:async(name:string)=>{assert.equal(name,'课程讨论.md');return path;}};
 const result=await dispatchPython(backend,true,'agent:exportConversation',[{...exportInput,includeSources:true}],()=>assert.fail('export must not broadcast'),ports);
 assert.deepEqual(result,{ok:true,data:true});
 assert.deepEqual(calls,[['agent:getConversation',exportId]]);
 const content=await readFile(path,'utf8');
 assert.match(content,/从本地后端读取的消息/);
 assert.match(content,/参考\.pdf/);
 assert.doesNotMatch(content,/私有摘要|private-id|previous saved version/);
 assert.deepEqual(await readdir(directory),['chosen.md']);
});

test('canceling conversation export leaves the destination untouched',async t=>{
 const directory=await mkdtemp(join(tmpdir(),'tm-conversation-cancel-'));
 t.after(()=>rm(directory,{recursive:true,force:true}));
 const destination=join(directory,'chosen.md');
 await fs.writeFile(destination,'previous saved version');
 let dialogs=0;
 const backend={call:async()=>({ok:true as const,data:exportConversation})};
 const canceled=await dispatchPython(backend,true,'agent:exportConversation',[exportInput],()=>assert.fail('export must not broadcast'),{saveFile:async()=>{dialogs++;return null;}});
 assert.deepEqual(canceled,{ok:true,data:false});
 assert.equal(dialogs,1);
 assert.equal(await readFile(destination,'utf8'),'previous saved version');
 assert.deepEqual(await readdir(directory),['chosen.md']);
});

test('empty conversation export reports the empty state before the dialog',async()=>{
 const backend={call:async()=>({ok:true as const,data:{...exportConversation,turns:[],turnCount:0}})};
 const result=await dispatchPython(backend,true,'agent:exportConversation',[exportInput],()=>assert.fail('export must not broadcast'),{saveFile:async()=>assert.fail('should not open a save dialog')});
 assert.deepEqual(result,{ok:false,error:'这段对话没有可导出的内容。'});
});

test('conversation export reports write failure without exposing the filesystem path',async t=>{
 const directory=await mkdtemp(join(tmpdir(),'tm-conversation-failure-'));
 t.after(()=>rm(directory,{recursive:true,force:true}));
 const backend={call:async()=>({ok:true as const,data:exportConversation})};
 const destination=join(directory,'missing-directory','chosen.md');
 const result=await dispatchPython(backend,true,'agent:exportConversation',[exportInput],()=>assert.fail('export must not broadcast'),{saveFile:async()=>destination});
 assert.deepEqual(result,{ok:false,error:'保存对话失败，请重试。'});
});

test('a partial export write preserves the previous file and removes only its own temporary file',async t=>{
 const directory=await mkdtemp(join(tmpdir(),'tm-conversation-partial-write-'));
 t.after(()=>rm(directory,{recursive:true,force:true}));
 const destination=join(directory,'chosen.md');
 const unrelated=join(directory,'unrelated.tmp');
 const realWriteFile=fs.writeFile;
 await realWriteFile(destination,'previous saved version');
 await realWriteFile(unrelated,'unrelated contents');
 const write=t.mock.method(fs,'writeFile',async (target:Parameters<typeof fs.writeFile>[0])=>{
  await realWriteFile(target,'partially written export');
  throw Object.assign(new Error('No space left on device'),{code:'ENOSPC'});
 });
 const backend={call:async()=>({ok:true as const,data:exportConversation})};
 const result=await dispatchPython(backend,true,'agent:exportConversation',[exportInput],()=>assert.fail('export must not broadcast'),{saveFile:async()=>destination});
 assert.equal(write.mock.callCount(),1);
 assert.deepEqual(result,{ok:false,error:'保存对话失败，请重试。'});
 assert.equal(await readFile(destination,'utf8'),'previous saved version');
 assert.equal(await readFile(unrelated,'utf8'),'unrelated contents');
 assert.deepEqual((await readdir(directory)).sort(),['chosen.md','unrelated.tmp']);
});

test('a failed export replacement keeps the previous file and removes the completed temporary export',async t=>{
 const directory=await mkdtemp(join(tmpdir(),'tm-conversation-replace-failure-'));
 t.after(()=>rm(directory,{recursive:true,force:true}));
 const destination=join(directory,'chosen.md');
 await fs.writeFile(destination,'previous saved version');
 let staged='';
 const rename=t.mock.method(fs,'rename',async (...[temporary,path]:Parameters<typeof fs.rename>)=>{
  assert.equal(path,destination);
  assert.ok(typeof temporary==='string');
  assert.equal(dirname(temporary),directory);
  staged=await readFile(temporary,'utf8');
  throw Object.assign(new Error('Destination is locked'),{code:'EACCES'});
 });
 const backend={call:async()=>({ok:true as const,data:exportConversation})};
 const result=await dispatchPython(backend,true,'agent:exportConversation',[exportInput],()=>assert.fail('export must not broadcast'),{saveFile:async()=>destination});
 assert.equal(rename.mock.callCount(),1);
 assert.match(staged,/从本地后端读取的消息/);
 assert.deepEqual(result,{ok:false,error:'保存对话失败，请重试。'});
 assert.equal(await readFile(destination,'utf8'),'previous saved version');
 assert.deepEqual(await readdir(directory),['chosen.md']);
});
