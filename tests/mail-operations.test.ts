import {DatabaseSync} from 'node:sqlite';
import {test} from 'node:test';
import assert from 'node:assert/strict';
import {mkdtempSync,rmSync} from 'node:fs';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {MailOperations} from '../src/main/mail-operations';
import {MailAccountManager,MailWorkspaceStore,type MailConnectionFactory} from '../src/main/mail-workspace';
import {TaskRepository} from '../src/main/repository';
import {SettingsStore} from '../src/main/settings';
import {defaultMailFilter,type MailMessage} from '../src/shared/mail';
import type {Result} from '../src/shared/contracts';
import type {MailJobState} from '../src/shared/mail-operations';
const data=<T,>(v:Result<T>):T=>{if(!v.ok)throw new Error(v.error);return v.data;};
const vault={available:async()=>true,encrypt:async(v:string)=>Buffer.from('encrypted:'+v),decrypt:async(v:Buffer)=>({result:v.toString().slice(10),shouldReEncrypt:false})};
const target={baseUrl:'https://api.deepseek.com',model:'deepseek-flash'};
const item:MailMessage={id:'remote',subject:'School notice',sender:'teacher@school.edu',receivedAt:'2026-10-30T00:00:00Z',preview:'',body:'Read announcement',unread:true,webUrl:'',imported:false};
const reply=(notes='Summary')=>new Response(JSON.stringify({choices:[{finish_reason:'stop',message:{content:JSON.stringify({drafts:[],notes})}}]}));
async function until<T>(read:()=>Promise<T>,predicate:(value:T)=>boolean,timeout=6000){const limit=Date.now()+timeout;while(Date.now()<limit){const value=await read();if(predicate(value))return value;await new Promise(resolve=>setTimeout(resolve,5));}throw new Error('等待后台状态超时');}
function pending(signal:AbortSignal|undefined|null):Promise<Response>{return new Promise((_,reject)=>{if(signal?.aborted)reject(new Error('aborted'));else signal?.addEventListener('abort',()=>reject(new Error('aborted')),{once:true});});}
async function fixture(request:typeof fetch=async()=>reply(),factory?:MailConnectionFactory){
 const dir=mkdtempSync(join(tmpdir(),'mail-operations-')),store=new MailWorkspaceStore(dir),repo=new TaskRepository(join(dir,'tasks.sqlite')),settings=new SettingsStore(dir,vault);
 await settings.save({...target,apiKey:'fixture-key'});
 const manager=new MailAccountManager(dir,vault,async()=>{},factory);let completed=0,ops=new MailOperations(dir,manager,store,repo,settings,()=>{},()=>{completed++;},request);
 const put=(body=item.body,id='remote')=>store.put('binding','identity','a@example.com',{...item,body,id}).localId;
 return {dir,store,repo,settings,manager,put,get ops(){return ops;},get completed(){return completed;},
  state:(id:string,state:MailJobState)=>until(async()=>{const job=data(await ops.api.getJob(id));if(job.state==='failed'&&state!=='failed')throw new Error(job.error??'后台任务失败');return job;},j=>j.state===state),
  restart:()=>{ops.close();ops=new MailOperations(dir,manager,store,repo,settings,()=>{},()=>{completed++;},request);},
  close:async()=>{ops.close();await new Promise(resolve=>setTimeout(resolve,10));store.close();repo.close();rmSync(dir,{recursive:true,force:true});}};
}
test('启动立即返回，导入在后台运行；暂停取消网络，重启继续检查点而不重复下载已完成正文',async()=>{
 let secondBlocked=true,cancelled=0,release:(()=>void)|undefined;const calls=new Map<string,number>();
 const messages=[{...item,id:'one'},{...item,id:'two'},{...item,id:'three'}];
 const factory:MailConnectionFactory=()=>({auth:{getState:async()=>({clientId:'',account:{id:'identity',email:'a@example.com',name:'A'},filter:defaultMailFilter}),connect:async()=>{throw new Error();},configure:async()=>{throw new Error();},disconnect:async()=>{throw new Error();},saveFilter:async()=>{throw new Error();}},reader:{read:async()=>({messages,limited:false}),listFolders:async()=>[],getMessage:async(id:string)=>{calls.set(id,(calls.get(id)??0)+1);if(id==='two'&&secondBlocked)await new Promise<void>((_,reject)=>{release=()=>reject(new Error('paused'));});return messages.find(m=>m.id===id)!;},cancelPending:()=>{cancelled++;release?.();}}});
 const f=await fixture(undefined,factory);try{
  const account=await f.manager.addAccount('imap');const job=data(await f.ops.api.startImport({bindingIds:[account.id],onlyNew:true}));assert.equal(job.state,'queued');
  await until(async()=>calls.get('two')??0,n=>n===1);data(await f.ops.api.pauseJob(job.id));const paused=await f.state(job.id,'paused');assert.equal(paused.done,1);assert.ok(cancelled>0);
  f.restart();assert.equal(data(await f.ops.api.getJob(job.id)).state,'paused');secondBlocked=false;data(await f.ops.api.resumeJob(job.id));const done=await f.state(job.id,'completed');assert.equal(done.report?.added,3);assert.equal(calls.get('one'),1);assert.equal(f.store.listLocal({}).total,3);assert.equal(f.completed,1);
  const local=f.store.listLocal({}).messages[0];f.store.deleteLocal([local.localId]);const repeat=data(await f.ops.api.startImport({bindingIds:[account.id],onlyNew:false}));await f.state(repeat.id,'completed');assert.equal(f.store.listLocal({}).total,2);assert.ok(f.store.isDeleted(account.id,'identity',local.id));
 }finally{await f.close();}
});
test('分析分段暂停及重启后不重发已完成段，全部成功才标已分析',async()=>{
 let calls=0,block=true;const payloads:any[]=[];
 const request=(async(_url,options)=>{calls++;payloads.push(JSON.parse(String(options?.body)));if(calls===2&&block)return pending(options?.signal);return reply('Part '+calls);}) as typeof fetch;
 const f=await fixture(request);try{
  const id=f.put('START-MAIL-'+'a'.repeat(18000));const job=data(await f.ops.api.startAnalysis({messageIds:[id],instructions:'总结全文',target}));assert.equal(job.state,'queued');
  await until(async()=>calls,n=>n===2);assert.equal(f.store.getLocal(id).status,'unanalyzed');data(await f.ops.api.pauseJob(job.id));await f.state(job.id,'paused');f.restart();block=false;data(await f.ops.api.resumeJob(job.id));const done=await f.state(job.id,'completed');
  assert.equal(f.store.getLocal(id).status,'analyzed');assert.equal(done.analysisIds!.length,1);assert.equal(payloads.filter(p=>p.messages.at(-1).content.includes('START-MAIL-')).length,1);assert.equal(f.repo.list().length,0);assert.equal(payloads[0].thinking.type,'disabled');assert.equal(payloads[0].response_format.type,'json_object');
 }finally{await f.close();}
});
test('超过旧24万字符的邮件自动分批，模型上下文400会缩小当前批而不丢失全文',async()=>{
 let calls=0;const sent:string[]=[];const request=(async(_url,options)=>{calls++;const payload=JSON.parse(String(options?.body));sent.push(payload.messages.at(-1).content);if(calls===1)return new Response('context length exceeded',{status:400});return reply();}) as typeof fetch;
 const f=await fixture(request);try{const id=f.put('START-LONG-'+'b'.repeat(245000)+'-END-LONG');const job=data(await f.ops.api.startAnalysis({messageIds:[id],instructions:'总结',target}));const done=await f.state(job.id,'completed');assert.ok(calls>50);assert.ok(sent.some(v=>v.includes('-END-LONG')));assert.ok(done.total>50);assert.equal(f.store.getLocal(id).status,'analyzed');assert.equal(f.store.listAnalyses().length,1);const merged=f.store.listAnalyses().find(a=>a.complete===true)!;assert.ok(merged);assert.deepEqual(merged.messageIds,[id]);assert.ok(merged.id);assert.ok(done.analysisIds!.includes(merged.id));}finally{await f.close();}
});
test('纯对话保存本机并支持重启追问，后续请求含历史，空草稿答复仍有分析记录',async()=>{
 const payloads:any[]=[];const request=(async(_url,options)=>{payloads.push(JSON.parse(String(options?.body)));return reply('Remember my exam on Monday');}) as typeof fetch;
 const f=await fixture(request);try{
  const first=data(await f.ops.api.startConversation('记住周一考试',target));await f.state(first.id,'completed');f.restart();const existing=data(await f.ops.api.getConversation(first.conversationId!));assert.equal(existing.turns.length,2);assert.equal(existing.turns[0].content,'记住周一考试');
  const next=data(await f.ops.api.continueConversation(existing.id,'之前说的考试是哪天？',target));await f.state(next.id,'completed');const conversation=data(await f.ops.api.getConversation(existing.id));assert.equal(conversation.turns.length,4);assert.ok(payloads[1].messages.some((m:any)=>m.content==='记住周一考试'));assert.equal(f.repo.list().length,0);assert.equal(f.store.listAnalyses().length,2);
 }finally{await f.close();}
});
test('失败或暂停的对话重试不重复用户轮次，配置变化不会误记成功',async()=>{
 let calls=0,mode:'deny'|'block'|'ok'|'change'='deny';let f:Awaited<ReturnType<typeof fixture>>;
 const request=(async(_url,options)=>{calls++;if(mode==='deny')return new Response('unauthorized',{status:401});if(mode==='block')return pending(options?.signal);if(mode==='change')await f.settings.save({...target,model:'changed',apiKey:''});return reply();}) as typeof fetch;
 f=await fixture(request);try{
  const first=data(await f.ops.api.startConversation('讨论下学习计划',target));await f.state(first.id,'failed');mode='block';data(await f.ops.api.resumeJob(first.id));await until(async()=>calls,n=>n===2);data(await f.ops.api.pauseJob(first.id));await f.state(first.id,'paused');mode='ok';data(await f.ops.api.resumeJob(first.id));await f.state(first.id,'completed');assert.equal(data(await f.ops.api.getConversation(first.conversationId!)).turns.length,2);
  const id=f.put('Changed config source');mode='change';const changed=data(await f.ops.api.startAnalysis({messageIds:[id],instructions:'总结',target}));await f.state(changed.id,'failed');assert.equal(f.store.getLocal(id).status,'unanalyzed');
 }finally{await f.close();}
});
test('关闭后台引擎中止请求并留下可继续状态，不产生未处理的拒绝',async()=>{
 let called=false;const unhandled:unknown[]=[];const listener=(e:unknown)=>unhandled.push(e);process.on('unhandledRejection',listener);
 const request=(async(_url,options)=>{called=true;return pending(options?.signal);}) as typeof fetch;
 const f=await fixture(request);try{const first=data(await f.ops.api.startConversation('hello',target));await until(async()=>called,Boolean);f.restart();assert.equal(data(await f.ops.api.getJob(first.id)).state,'paused');await new Promise(resolve=>setTimeout(resolve,20));assert.equal(unhandled.length,0);}finally{process.off('unhandledRejection',listener);await f.close();}
});

test('对话模型结果已落检查点但本地提交临时失败，恢复不重复调用模型或轮次',async()=>{
 let calls=0;const request=(async()=>{calls++;return reply('Durable answer');}) as typeof fetch;
 const f=await fixture(request);const original=f.store.saveAnalyses.bind(f.store);let fail=true;
 f.store.saveAnalyses=analyses=>{if(fail){fail=false;throw new Error('temporary storage failure');}return original(analyses);};
 try{const job=data(await f.ops.api.startConversation('保持这轮结果',target));const failed=await f.state(job.id,'failed');assert.ok(failed.error?.includes('temporary'));assert.equal(calls,1);f.restart();data(await f.ops.api.resumeJob(job.id));await f.state(job.id,'completed');assert.equal(calls,1);assert.equal(data(await f.ops.api.getConversation(job.conversationId!)).turns.length,2);assert.equal(f.store.listAnalyses().length,1);}finally{await f.close();}
});

test('长对话生成摘要记忆，重启追问保留早期关键约束且完整对话记录不删',async()=>{
 const constraint='用户约束：每周至少休息一天。';const requests:any[]=[];let ordinary=0,summaries=0;
 const request=(async(_url,options)=>{const payload=JSON.parse(String(options?.body));requests.push(payload);
  if(payload.messages[0].content.includes('为继续对话保存摘要记忆')){summaries++;assert.ok(payload.messages.at(-1).content.includes('每周至少休息一天'));return reply(constraint);}
  ordinary++;return reply(ordinary===1?constraint+'长分析记录'.repeat(3400):'继续遵守每周至少休息一天。');
 }) as typeof fetch;
 const f=await fixture(request);try{
  const first=data(await f.ops.api.startConversation(constraint,target));await f.state(first.id,'completed');
  const second=data(await f.ops.api.continueConversation(first.conversationId!,'请据此调整下周安排',target));await f.state(second.id,'completed');assert.ok(summaries>0);
  const normal=requests.at(-1);assert.ok(normal.messages.some((m:any)=>m.role==='assistant'&&m.content.includes('摘要记忆')&&m.content.includes('每周至少休息一天')));
  const beforeRestart=data(await f.ops.api.getConversation(first.conversationId!));assert.equal(beforeRestart.turns.length,4);assert.ok(beforeRestart.turns[1].content.length>15000);const count=summaries;
  f.restart();const third=data(await f.ops.api.continueConversation(first.conversationId!,'再补充周五的安排',target));await f.state(third.id,'completed');
  assert.equal(summaries,count);assert.ok(requests.at(-1).messages.some((m:any)=>m.role==='assistant'&&m.content.includes('摘要记忆')&&m.content.includes('每周至少休息一天')));
  const all=data(await f.ops.api.getConversation(first.conversationId!));assert.equal(all.turns.length,6);assert.equal(all.turns[0].content,constraint);assert.equal(all.turns[1].content,beforeRestart.turns[1].content);
 }finally{await f.close();}
});
test('请求完全忽略AbortSignal时仍可主动暂停，队列后续新任务继续运行',async()=>{
 let calls=0;const request=(async()=>{calls++;if(calls===1)return new Promise<Response>(()=>{});return reply('后续任务正常');}) as typeof fetch;
 const f=await fixture(request);try{
  const stalled=data(await f.ops.api.startConversation('先暂停这轮',target));await until(async()=>calls,n=>n===1);data(await f.ops.api.pauseJob(stalled.id));
  await until(async()=>data(await f.ops.api.getJob(stalled.id)),j=>j.state==='paused',1000);
  const next=data(await f.ops.api.startConversation('新对话继续处理',target));await f.state(next.id,'completed');assert.equal(calls,2);assert.equal(data(await f.ops.api.getJob(stalled.id)).state,'paused');
 }finally{await f.close();}
});


test('选择15封短邮件默认逐封请求，每次只含一封且来源由程序绑定，不依赖模型猜测',async()=>{
 const sent:string[]=[];const request=(async(_url,options)=>{const input=JSON.parse(String(options?.body)).messages.at(-1).content as string;sent.push(input);return new Response(JSON.stringify({choices:[{finish_reason:'stop',message:{content:JSON.stringify({drafts:[{tempId:'1',parentTempId:null,title:'Follow this email',notes:'',due:{mode:'none'},source:'AI paraphrase, not a verbatim quote',sourceMailIds:['wrong-model-id']}],notes:'Summary'})}}]}));}) as typeof fetch;
 const f=await fixture(request);try{
  const ids=Array.from({length:15},(_,i)=>f.put(`MAIL-${String(i+1).padStart(2,'0')} unique contents`,`remote-${i+1}`));
  const job=data(await f.ops.api.startAnalysis({messageIds:ids,instructions:'逐封提取待办',target}));const completed=await f.state(job.id,'completed');
  assert.equal(completed.messageCount,15);assert.equal(completed.total,15);assert.equal(sent.length,15);
  sent.forEach(text=>{assert.equal((text.match(/邮件标识：/g)??[]).length,1);assert.equal(new Set(text.match(/MAIL-\d+/g)).size,1);});
  const analyses=f.store.listAnalyses();assert.equal(analyses.length,15);assert.deepEqual(new Set(analyses.flatMap(a=>a.messageIds)),new Set(ids));
  for(const a of analyses){assert.equal(a.messageIds.length,1);assert.deepEqual(a.drafts[0].sourceMailIds,a.messageIds);assert.ok(a.drafts[0].notes?.includes('本地邮件：'+a.messageIds[0]));assert.ok(!a.drafts[0].notes?.includes('待核实'));}
 }finally{await f.close();}
});

test('逐封任务暂停重启后保留已完成邮件，恢复只处理尚未完成的邮件',async()=>{
 const sent:string[]=[];let blocked=true;const request=(async(_url,options)=>{const text=JSON.parse(String(options?.body)).messages.at(-1).content as string;sent.push(text);if(text.includes('SECOND-MAIL')&&blocked)return pending(options?.signal);return reply();}) as typeof fetch;
 const f=await fixture(request);try{
  const first=f.put('FIRST-MAIL','first'),second=f.put('SECOND-MAIL','second');const job=data(await f.ops.api.startAnalysis({messageIds:[first,second],instructions:'分析',target}));
  await until(async()=>sent.length,n=>n===2);data(await f.ops.api.pauseJob(job.id));const paused=await f.state(job.id,'paused');assert.equal(paused.done,1);f.restart();blocked=false;data(await f.ops.api.resumeJob(job.id));await f.state(job.id,'completed');
  assert.equal(sent.filter(text=>text.includes('FIRST-MAIL')).length,1);assert.equal(sent.filter(text=>text.includes('SECOND-MAIL')).length,2);assert.ok(sent.every(text=>!(text.includes('FIRST-MAIL')&&text.includes('SECOND-MAIL'))));
 }finally{await f.close();}
});

test('完成提示在未确认时跨重启仍可查看，点击或关闭后的确认持久化且不重复提醒',async()=>{
 const f=await fixture();try{
  const first=data(await f.ops.api.startConversation('第一轮完成提醒',target));await f.state(first.id,'completed');assert.notEqual(data(await f.ops.api.getJob(first.id)).notificationAcknowledged,true);
  f.restart();assert.notEqual(data(await f.ops.api.getJob(first.id)).notificationAcknowledged,true);assert.equal(f.completed,1);
  data(await f.ops.api.acknowledgeJob(first.id));data(await f.ops.api.acknowledgeJob(first.id));assert.equal(data(await f.ops.api.getJob(first.id)).notificationAcknowledged,true);
  f.restart();assert.equal(data(await f.ops.api.getJob(first.id)).notificationAcknowledged,true);assert.equal(data(await f.ops.api.listJobs()).find(j=>j.id===first.id)!.notificationAcknowledged,true);assert.equal(f.completed,1);
  const second=data(await f.ops.api.startConversation('新的结果需要单独提醒',target));const done=await f.state(second.id,'completed');assert.notEqual(done.notificationAcknowledged,true);assert.equal(f.completed,2);
 }finally{await f.close();}
});

test('升级时旧完成记录不补发提示，已确认失败任务重新执行的新结果允许提醒',async()=>{
 let denied=true;const request=(async()=>denied?new Response('denied',{status:401}):reply()) as typeof fetch;
 const f=await fixture(request);try{
  const failed=data(await f.ops.api.startConversation('这轮先失败',target));await f.state(failed.id,'failed');data(await f.ops.api.acknowledgeJob(failed.id));denied=false;data(await f.ops.api.resumeJob(failed.id));const completed=await f.state(failed.id,'completed');assert.equal(completed.notificationAcknowledged,false);
  f.ops.close();const db=new DatabaseSync(join(f.dir,'mail-operations.sqlite'));try{db.prepare("DELETE FROM operation_meta WHERE key='notification-ack-v1'").run();db.prepare("UPDATE jobs SET payload=json_remove(payload,'$.notificationAcknowledged') WHERE id=?").run(failed.id);}finally{db.close();}
  f.restart();assert.equal(data(await f.ops.api.getJob(failed.id)).notificationAcknowledged,true);assert.equal(f.completed,1);
 }finally{await f.close();}
});
