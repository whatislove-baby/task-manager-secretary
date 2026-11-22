import test from 'node:test';
import assert from 'node:assert/strict';
import { dispatchPython } from '../src/main/python-desktop';

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
