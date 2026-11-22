import { test } from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { TaskRepository } from '../src/main/repository';
import { SettingsStore, type KeyVault } from '../src/main/settings';
import { CanvasFeedStore } from '../src/main/canvas';
import { createBackendService } from '../src/main/service';
import type { Result } from '../src/shared/contracts';
const data = <T,>(r: Result<T>): T => { if (!r.ok) throw new Error(r.error); return r.data; };
const vault: KeyVault = { available: async()=>true, encrypt:async value=>Buffer.from('test:'+Buffer.from(value).toString('base64')), decrypt:async value=>({result:Buffer.from(value.toString().slice(5),'base64').toString(),shouldReEncrypt:false}) };
const ports = {chooseText:async()=>null,writeBackup:async()=>true,chooseBackup:async()=>null};
const feed = (title='Essay', due='20261105T120000Z') => `BEGIN:VCALENDAR\r\nVERSION:2.0\r\nPRODID:-//Test//Canvas//EN\r\nBEGIN:VEVENT\r\nUID:assignment-123\r\nDTSTART:${due}\r\nSUMMARY:${title}\r\nDESCRIPTION:Original notes\r\nEND:VEVENT\r\nEND:VCALENDAR\r\n`;
test('一次同步直接入库，并发合并、重复不新增、更新保留本地状态并报告', async()=>{
 const dir=mkdtempSync(join(tmpdir(),'canvas-service-')); const repo=new TaskRepository(join(dir,'db.sqlite'));
 try {
  let body=feed(), requests=0;
  const request=(async()=>{requests++;return new Response(body);}) as typeof fetch;
  const api=createBackendService(repo,new SettingsStore(dir,vault),ports,request,new CanvasFeedStore(dir,vault),()=>new Date('2026-10-30T12:00:00Z'));
  data(await api.saveCanvasFeed('https://canvas.example/feeds/private-token.ics'));
  const [first,duplicate]=await Promise.all([api.syncCanvas(),api.syncCanvas()]);
  assert.equal(data(first).added,1);assert.equal(data(duplicate).added,1);assert.equal(requests,1);
  const todo=repo.list()[0]!;repo.update(todo.id,{notes:'My local notes'},todo.revision);repo.setCompleted(todo.id,true,repo.get(todo.id).revision);
  assert.equal(data(await api.syncCanvas()).unchanged,1);
  body=feed('Changed essay','20261106T120000Z');
  const report=data(await api.syncCanvas());assert.equal(report.updated,1);assert.equal(report.changes[0]?.previousTitle,'Essay');
  assert.equal(repo.list().length,1);assert.equal(repo.get(todo.id).notes,'My local notes');assert.equal(repo.get(todo.id).completed,true);
  assert.equal(JSON.stringify(repo.exportBackup()).includes('private-token'),false);
 } finally {repo.close();rmSync(dir,{recursive:true,force:true});}
});
test('拉取过程中替换订阅，旧响应不能写入',async()=>{
 const dir=mkdtempSync(join(tmpdir(),'canvas-race-'));const repo=new TaskRepository(join(dir,'db.sqlite'));
 try {
  let resolve:((value:Response)=>void)|undefined;
  const request=(async()=>await new Promise<Response>(done=>{resolve=done;})) as typeof fetch;
  const api=createBackendService(repo,new SettingsStore(dir,vault),ports,request,new CanvasFeedStore(dir,vault),()=>new Date('2026-10-30T12:00:00Z'));
  data(await api.saveCanvasFeed('https://canvas.example/feeds/old-secret.ics'));
  const pending=api.syncCanvas();while(!resolve)await new Promise<void>(done=>setImmediate(done));
  data(await api.saveCanvasFeed('https://canvas.example/feeds/new-secret.ics'));resolve(new Response(feed()));
  const result=await pending;assert.equal(result.ok,false);assert.equal(repo.list().length,0);
  assert.equal(JSON.stringify(result).includes('old-secret'),false);
 }finally{repo.close();rmSync(dir,{recursive:true,force:true});}
});


test('长说明独立读取和更新，单项失败报告且正常事项继续导入，原文只由后台打开', async()=>{
 const dir=mkdtempSync(join(tmpdir(),'canvas-reader-service-'));const repo=new TaskRepository(join(dir,'db.sqlite'));
 try {
  const description='Long assignment requirements. '.repeat(1000).trim();
  let body=feed().replace('DESCRIPTION:Original notes','DESCRIPTION:'+description+'\r\nURL:https://canvas.example/courses/1/assignments/2');
  const opened:string[]=[];
  const api=createBackendService(repo,new SettingsStore(dir,vault),{...ports,openExternal:async url=>{opened.push(url);}},(async()=>new Response(body)) as typeof fetch,new CanvasFeedStore(dir,vault),()=>new Date('2026-10-30T12:00:00Z'));
  data(await api.saveCanvasFeed('https://canvas.example/feeds/secret-token.ics'));
  assert.equal(data(await api.syncCanvas()).added,1);
  const todo=repo.list()[0];assert.equal(todo.notes,'');
  const stored=data(await api.getCanvasSource(todo.id));assert.ok(stored?.description === description);assert.equal(stored?.url,'https://canvas.example/courses/1/assignments/2');
  data(await api.openCanvasOriginal(todo.id));assert.deepEqual(opened,['https://canvas.example/courses/1/assignments/2']);
  repo.update(todo.id,{notes:'My notes'},todo.revision);
  body=body.replace(description,description+' Additional requirement.');
  const changed=data(await api.syncCanvas());assert.equal(changed.updated,1);assert.equal(changed.changes[0].descriptionChanged,true);
  assert.equal(repo.get(todo.id).notes,'My notes');
  body=body.replace('END:VCALENDAR','BEGIN:VEVENT\r\nUID:invalid-item\r\nSUMMARY:Invalid assignment\r\nEND:VEVENT\r\nEND:VCALENDAR');
  const partial=data(await api.syncCanvas());assert.equal(partial.unchanged,1);assert.equal(partial.skipped,1);assert.equal(partial.issues?.[0].title,'Invalid assignment');
  assert.equal(JSON.stringify(partial).includes('secret-token'),false);
  const local=repo.create({title:'Local'}).value;
  assert.equal(data(await api.getCanvasSource(local.id)),null);assert.equal((await api.openCanvasOriginal(local.id)).ok,false);
  assert.equal((await api.getCanvasSource('not-an-id')).ok,false);
  const failing=createBackendService(repo,new SettingsStore(dir,vault),{...ports,openExternal:async()=>{throw new Error('https://private.example/token');}});
  const failure=await failing.openCanvasOriginal(todo.id);assert.equal(failure.ok,false);assert.equal(JSON.stringify(failure).includes('private.example'),false);
 }finally{repo.close();rmSync(dir,{recursive:true,force:true});}
});


test('Canvas只同步今天及之后的日期，分钟按本机日历日；不删除或覆盖既有历史事项',async()=>{
 const dir=mkdtempSync(join(tmpdir(),'canvas-upcoming-'));const repo=new TaskRepository(join(dir,'db.sqlite'));
 const previousZone=process.env.TZ;process.env.TZ='Asia/Shanghai';
 try {
  let now=new Date('2026-10-30T12:00:00+08:00');
  const entries=[['past','20261029'],['today','20261030'],['future','20261031']].map(([uid,date])=>`BEGIN:VEVENT\r\nUID:${uid}\r\nDTSTART;VALUE=DATE:${date}\r\nSUMMARY:${uid}\r\nEND:VEVENT`);
  // UTC上一日16点为上海今天零点；今天具体时刻已过也导入。
  entries.push(...[['midnight','20261029T160000Z'],['last-minute','20261029T155900Z']].map(([uid,at])=>`BEGIN:VEVENT\r\nUID:${uid}\r\nDTSTART:${at}\r\nSUMMARY:${uid}\r\nEND:VEVENT`));
  const body=`BEGIN:VCALENDAR\r\nVERSION:2.0\r\n${entries.join('\r\n')}\r\nEND:VCALENDAR`;
  const api=createBackendService(repo,new SettingsStore(dir,vault),ports,(async()=>new Response(body)) as typeof fetch,new CanvasFeedStore(dir,vault),()=>now);
  data(await api.saveCanvasFeed('https://canvas.example/feed.ics'));
  const first=data(await api.syncCanvas());assert.equal(first.added,3);assert.equal(first.expired,2);assert.equal(first.skipped,2);
  assert.deepEqual(repo.list().map(todo=>todo.title).sort(),['future','midnight','today']);
  const before=repo.list(true);now=new Date('2026-10-31T01:00:00+08:00');
  const next=data(await api.syncCanvas());assert.equal(next.added,0);assert.equal(next.unchanged,1);assert.equal(next.expired,4);assert.deepEqual(repo.list(true),before);
 }finally{repo.close();rmSync(dir,{recursive:true,force:true});if(previousZone===undefined)delete process.env.TZ;else process.env.TZ=previousZone;}
});
