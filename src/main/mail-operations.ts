import {provenanceNotes} from './mail-provenance';
import {DatabaseSync} from 'node:sqlite';
import {join} from 'node:path';
import {mkdirSync} from 'node:fs';
import {createHash,randomUUID} from 'node:crypto';
import {z} from 'zod';
import type {Extraction} from '../shared/todo';
import {validateDrafts} from '../shared/drafts';
import type {MailOperationsAPI,MailJob,MailJobState,MailConversation,ConversationTurn} from '../shared/mail-operations';
import type {MailAnalysis,LocalMail,MailImportReport,MailImportFailure} from '../shared/mail-workspace';
import {mailFilterSchema,type MailSummary} from '../shared/mail';
import type {MailAccountManager,MailWorkspaceStore} from './mail-workspace';
import type {SettingsStore} from './settings';
import type {TaskRepository} from './repository';
import {mailConfirmationToken} from './mail-service';
import {MailAIError,requestMailAI} from './mail-job-ai';
import {MailReadError,mailFailure} from './mail-errors';
const uuid=z.string().uuid(),target=z.strictObject({baseUrl:z.string().url(),model:z.string().min(1).max(150)});
const importSchema=z.strictObject({bindingIds:z.array(z.string().min(1).max(2048)).min(1).max(100).refine(a=>new Set(a).size===a.length),filter:mailFilterSchema.optional(),onlyNew:z.boolean()});
const analysisSchema=z.strictObject({messageIds:z.array(z.string().min(1).max(2048)).min(1).max(1000).refine(a=>new Set(a).size===a.length),instructions:z.string().trim().max(4000),target});
type Piece={id:string;start:number;end:number};
type Part={id:string;pieces:Piece[];result?:Extraction};
type Plan={binding:string;identity:string;email:string;messages:MailSummary[];cursor:number};
type RetryTarget={key:string;bindingId:string;identity:string;email:string;remoteId:string;subject:string;sender:string;receivedAt:string;maxBytes?:number};
type Work={chatResult?:Extraction;input:any;cursor:number;parts?:Part[];fingerprints?:Record<string,string>;accountIndex?:number;plans?:Plan[];resultIds?:string[];retryTargets?:RetryTarget[];sourceJobId?:string};
type StoredJob=MailJob&{work:Work};
type Conversation=MailConversation&{memory?:string;memoryCount?:number};
const now=()=>new Date().toISOString();
const hash=(item:LocalMail)=>createHash('sha256').update(JSON.stringify([item.subject,item.sender,item.receivedAt,item.body])).digest('hex');
const clean=(error:unknown)=>(error instanceof Error?error.message:'处理失败，请重试。').replace(/(?:https?|webcal):\/\/[^\s]+/gi,'［地址］').replace(/Bearer\s+[^\s]+/gi,'［凭据］');
const summary=(a:MailAnalysis)=>[a.notes,a.drafts.length?'待办草稿：\n'+a.drafts.map(d=>d.title+(d.due?.mode==='value'?' · '+JSON.stringify(d.due.value):'')).join('\n'):'本轮没有待办草稿。'].filter(Boolean).join('\n\n');
const failureKey=(bindingId:string,remoteId:string)=>bindingId+':'+createHash('sha256').update(remoteId).digest('hex').slice(0,40);
const emptyReport=():MailImportReport=>({added:0,skipped:0,failed:0,limited:false,errors:[],bodyRead:0,bodyPending:0,pendingLocalIds:[],failures:[],mailboxErrors:[]});
/** Jobs belong to the main process; no renderer must stay open for progress. */
export class MailOperations {
 private db:DatabaseSync;private running=false;private closed=false;private scheduled=false;
 private active:{id:string;controller:AbortController;cancel?:()=>void}|null=null;
 private canceled=new Set<string>();
 readonly api:MailOperationsAPI;
 constructor(directory:string,private manager:MailAccountManager,private mail:MailWorkspaceStore,private repository:Pick<TaskRepository,'hasDraftConfirmation'>,private settings:Pick<SettingsStore,'forRequest'>,private changed:()=>void=()=>{},private completed:(job:MailJob)=>void=()=>{},private request:typeof fetch=fetch){
  mkdirSync(directory,{recursive:true});this.db=new DatabaseSync(join(directory,'mail-operations.sqlite'));this.db.exec('PRAGMA secure_delete=ON; PRAGMA busy_timeout=5000; CREATE TABLE IF NOT EXISTS jobs(id TEXT PRIMARY KEY,payload TEXT NOT NULL); CREATE TABLE IF NOT EXISTS conversations(id TEXT PRIMARY KEY,payload TEXT NOT NULL); CREATE TABLE IF NOT EXISTS operation_meta(key TEXT PRIMARY KEY,value TEXT NOT NULL);');
  for(const j of this.jobs()){
   if(['running','pausing','queued'].includes(j.state)){j.state='paused';j.error='应用已重新启动，可从已保存的进度继续。';this.writeJob(j,false);}
   else if(j.state==='canceling'){j.state='canceled';j.notificationAcknowledged=true;delete j.error;this.writeJob(j,false);}
  }
  if(!this.db.prepare("SELECT 1 FROM operation_meta WHERE key='history-migrated'").get()){
   for(const a of this.mail.listAnalyses()){const c:Conversation={id:a.id,title:a.instructions.slice(0,60)||'此前的邮件分析',createdAt:a.createdAt,updatedAt:a.createdAt,turnCount:2,messageIds:a.messageIds,turns:[{id:randomUUID(),role:'user',content:a.instructions||'分析所选邮件',createdAt:a.createdAt},{id:randomUUID(),role:'assistant',content:summary(a),analysisId:a.id,createdAt:a.createdAt}]};this.writeConversation(c);}
   this.db.prepare("INSERT INTO operation_meta VALUES('history-migrated','1')").run();
  }
  if(!this.db.prepare("SELECT 1 FROM operation_meta WHERE key='notification-ack-v1'").get()){for(const j of this.jobs())if(['completed','failed','canceled'].includes(j.state)){j.notificationAcknowledged=true;this.writeJob(j,false);}this.db.prepare("INSERT INTO operation_meta VALUES('notification-ack-v1','1')").run();}
  const result=async<T>(f:()=>T|Promise<T>)=>{try{return {ok:true as const,data:await f()};}catch(e){return {ok:false as const,error:e instanceof z.ZodError?'参数无效，请检查输入。':clean(e)};}};
  this.api={startFileTask:async()=>({ok:false,error:'附件与文件处理需要 Python 后端，请以默认方式启动应用。'}),acknowledgeJob:id=>result(()=>{const j=this.job(id);if(['completed','failed','canceled'].includes(j.state)){j.notificationAcknowledged=true;this.writeJob(j);}}),listJobs:()=>result(()=>{const rows=this.db.prepare("SELECT CASE WHEN json_extract(payload,'$.kind')='analysis' THEN json_set(json_remove(payload,'$.work'),'$.messageCount',json_array_length(json_extract(payload,'$.work.input.messageIds'))) ELSE json_remove(payload,'$.work') END AS payload FROM jobs").all().map(r=>JSON.parse(String(r.payload)) as MailJob).sort((a,b)=>b.createdAt.localeCompare(a.createdAt));let completed=0;return rows.filter(j=>j.state!=='completed'||completed++<100);}),getJob:id=>result(()=>this.publicJob(this.job(id))),
   startInboxSync:async()=>({ok:false,error:'一键收取需要使用正式 Python 版本。'}),
   startImport:input=>result(()=>this.createImport(importSchema.parse(input))),startAnalysis:input=>result(async()=>{const v=analysisSchema.parse(input);await this.checkTarget(v.target);return this.createAnalysis(v);}),
   pauseJob:id=>result(()=>this.pause(id)),resumeJob:id=>result(()=>this.resume(id)),cancelJob:id=>result(()=>this.cancel(id)),
   retryImportFailures:input=>result(()=>this.createRetry(input)),retryBodies:input=>result(()=>this.createBodyRetry(input)),
   listConversations:()=>result(()=>this.db.prepare("SELECT json_extract(payload,'$.id') AS id,json_extract(payload,'$.title') AS title,json_extract(payload,'$.createdAt') AS createdAt,json_extract(payload,'$.updatedAt') AS updatedAt,json_array_length(json_extract(payload,'$.turns')) AS turnCount FROM conversations ORDER BY updatedAt DESC").all().map(r=>({id:String(r.id),title:String(r.title),createdAt:String(r.createdAt),updatedAt:String(r.updatedAt),turnCount:Number(r.turnCount)}))),
   getConversation:id=>result(()=>{const c=this.conversation(id);const {memory,memoryCount,...visible}=c;return {...visible,turnCount:c.turns.length};}),
   continueConversation:(id,text,t)=>result(async()=>{uuid.parse(id);const v=z.string().trim().min(1).max(4000).parse(text),config=target.parse(t);await this.checkTarget(config);const c=this.conversation(id);this.assertConversationIdle(id);return this.createChat(c,v,config);}),
   startConversation:(text,t)=>result(async()=>{const v=z.string().trim().min(1).max(20000).parse(text),config=target.parse(t);await this.checkTarget(config);const c:Conversation={id:randomUUID(),title:v.slice(0,60),createdAt:now(),updatedAt:now(),turnCount:0,messageIds:[],turns:[]};return this.createChat(c,v,config);}),
   deleteConversation:id=>result(()=>{this.conversation(id);if(this.jobs().some(j=>j.conversationId===id&&['running','pausing','canceling','queued'].includes(j.state)))throw new Error('请先暂停此对话的后台任务。');this.db.exec('BEGIN IMMEDIATE');try{for(const j of this.jobs())if(j.conversationId===id)this.db.prepare('DELETE FROM jobs WHERE id=?').run(j.id);this.db.prepare('DELETE FROM conversations WHERE id=?').run(id);this.db.exec('COMMIT');}catch(e){this.db.exec('ROLLBACK');throw e;}this.changed();}),
  };
 }
 private jobs():StoredJob[]{return this.db.prepare('SELECT payload FROM jobs').all().map(r=>JSON.parse(String(r.payload)) as StoredJob).sort((a,b)=>b.createdAt.localeCompare(a.createdAt));}
 private job(id:string){uuid.parse(id);const row=this.db.prepare('SELECT payload FROM jobs WHERE id=?').get(id);if(!row)throw new Error('找不到此后台任务。');return JSON.parse(String(row.payload)) as StoredJob;}
 private publicJob(j:StoredJob):MailJob{const {work,...value}=j;return {...value,...(j.kind==='analysis'?{messageCount:work.input.messageIds.length}:{} )};}
 private writeJob(j:StoredJob,notify=true){if(this.closed)return;j.updatedAt=now();this.db.prepare('INSERT INTO jobs VALUES(?,?) ON CONFLICT(id) DO UPDATE SET payload=excluded.payload').run(j.id,JSON.stringify(j));if(notify)this.changed();}
 private conversations():Conversation[]{return this.db.prepare('SELECT payload FROM conversations').all().map(r=>JSON.parse(String(r.payload)) as Conversation).sort((a,b)=>b.updatedAt.localeCompare(a.updatedAt));}
 private conversation(id:string):Conversation{uuid.parse(id);const row=this.db.prepare('SELECT payload FROM conversations WHERE id=?').get(id);if(!row)throw new Error('找不到此本地对话。');return JSON.parse(String(row.payload));}
 private writeConversation(c:Conversation){c.turnCount=c.turns.length;this.db.prepare('INSERT INTO conversations VALUES(?,?) ON CONFLICT(id) DO UPDATE SET payload=excluded.payload').run(c.id,JSON.stringify(c));}
 private async checkTarget(t:{baseUrl:string;model:string}){const c=await this.settings.forRequest();if(c.baseUrl!==t.baseUrl||c.model!==t.model)throw new Error('AI服务或模型已变化，请核对设置后重新开始。');return c;}
 private assertConversationIdle(id:string){if(this.jobs().some(j=>j.conversationId===id&&['queued','running','pausing','paused','canceling'].includes(j.state)))throw new Error('这次会话还有未完成的任务，请先继续或结束它。');}
 private create(kind:MailJob['kind'],label:string,input:any,conversationId?:string){const j:StoredJob={id:randomUUID(),kind,label,state:'queued',done:0,total:0,createdAt:now(),updatedAt:now(),conversationId,work:{input,cursor:0}};return j;}
 private createImport(input:z.infer<typeof importSchema>){const existing=this.jobs().find(j=>j.kind==='import'&&['running','queued','paused','pausing'].includes(j.state)&&!j.work.retryTargets&&JSON.stringify(j.work.input)===JSON.stringify(input));if(existing)return this.publicJob(existing);const j=this.create('import','导入邮件',input);j.report=emptyReport();this.writeJob(j);this.schedule();return this.publicJob(j);}
 private createRetry(input:{jobId:string;keys?:string[]}){
  const valid=z.strictObject({jobId:z.string().uuid(),keys:z.array(z.string().min(1).max(300)).max(1000).optional()}).parse(input);
  const source=this.job(valid.jobId);
  if(source.kind!=='import')throw new Error('此任务不是邮件导入，不能重试导入失败项。');
  const failures=(source.report?.failures??[]).filter(f=>f.retryable);
  const keys=valid.keys?new Set(valid.keys):null;
  if(keys&&[...keys].some(key=>!failures.some(f=>f.key===key)))throw new Error('部分失败明细已变化，请关闭提示后重新导入。');
  const selected=keys?failures.filter(f=>keys.has(f.key)):failures;
  if(!selected.length)throw new Error('没有可重试的失败邮件。');
  const targets:RetryTarget[]=[...new Map(selected.map(f=>[f.key,{key:f.key,bindingId:f.bindingId,identity:f.identity,email:f.mailboxEmail,remoteId:f.remoteId,subject:f.subject,sender:f.sender,receivedAt:f.receivedAt}])).values()];
  const j=this.create('import','重试导入失败邮件',{bindingIds:[...new Set(targets.map(t=>t.bindingId))],onlyNew:false});
  j.work.retryTargets=targets;j.work.sourceJobId=source.id;j.report=emptyReport();j.total=targets.length;this.writeJob(j);this.schedule();return this.publicJob(j);
 }
 private createBodyRetry(input:{localIds:string[]}){
  const ids=z.array(z.string().min(1).max(2048)).min(1).max(500).refine(values=>new Set(values).size===values.length).parse(input.localIds);
  const targets:RetryTarget[]=ids.map(id=>{const m=this.mail.getLocal(id);return {key:failureKey(m.bindingId,m.id),bindingId:m.bindingId,identity:m.mailboxIdentity,email:m.accountEmail,remoteId:m.id,subject:m.subject,sender:m.sender,receivedAt:m.receivedAt,maxBytes:(m.bodyStatus==='partial'||m.bodyStatus==='pending')?25*1024*1024:undefined};});
  const j=this.create('import','补读邮件正文',{bindingIds:[...new Set(targets.map(t=>t.bindingId))],onlyNew:false});
  j.work.retryTargets=targets;j.report=emptyReport();j.total=targets.length;this.writeJob(j);this.schedule();return this.publicJob(j);
 }
 private createAnalysis(input:z.infer<typeof analysisSchema>){const j=this.create('analysis','分析邮件',input),c:Conversation={id:randomUUID(),title:input.instructions.slice(0,60)||'邮件分析',createdAt:now(),updatedAt:now(),turnCount:1,messageIds:input.messageIds,turns:[{id:randomUUID(),role:'user',content:`分析所选 ${input.messageIds.length} 封邮件。\n${input.instructions}`,createdAt:now(),jobId:j.id}]};j.conversationId=c.id;this.writeConversation(c);this.writeJob(j);this.schedule();return this.publicJob(j);}
 private createChat(c:Conversation,text:string,t:{baseUrl:string;model:string}){const j=this.create('chat','AI对话',{text,target:t},c.id);c.turns.push({id:randomUUID(),role:'user',content:text,createdAt:now(),jobId:j.id});c.updatedAt=now();this.writeConversation(c);this.writeJob(j);this.schedule();return this.publicJob(j);}
 private pause(id:string){const j=this.job(id);if(['canceling','canceled','completed','failed'].includes(j.state))return this.publicJob(j);if(j.state==='running'||j.state==='pausing'){j.state='pausing';this.writeJob(j);if(this.active?.id===id){this.active.controller.abort();this.active.cancel?.();}}else if(j.state==='queued'){j.state='paused';this.writeJob(j);}return this.publicJob(this.job(id));}
 private resume(id:string){const j=this.job(id);if(j.state==='canceled')throw new Error('此任务已取消，不能继续；请重新开始新任务。');if(!['paused','failed'].includes(j.state))return this.publicJob(j);j.state='queued';j.notificationAcknowledged=false;delete j.error;this.writeJob(j);this.schedule();return this.publicJob(j);}
 private cancel(id:string){const j=this.job(id);if(['completed','failed','canceled'].includes(j.state))return this.publicJob(j);this.canceled.add(id);const idle=j.state!=='running'&&j.state!=='pausing'&&j.state!=='canceling';j.state=idle?'canceled':'canceling';j.notificationAcknowledged=true;delete j.error;if(idle)this.canceled.delete(id);this.writeJob(j);if(this.active?.id===id){this.active.controller.abort();this.active.cancel?.();}return this.publicJob(this.job(id));}
 private schedule(){if(this.closed||this.scheduled)return;this.scheduled=true;setTimeout(()=>{this.scheduled=false;void this.pump();},0);}
 private async pump(){if(this.running||this.closed)return;this.running=true;try{while(!this.closed){const j=this.jobs().reverse().find(v=>v.state==='queued');if(!j)break;const controller=new AbortController();this.active={id:j.id,controller};j.state='running';this.writeJob(j);try{if(j.kind==='import')await this.runImport(j,controller.signal);else if(j.kind==='analysis')await this.runAnalysis(j,controller.signal);else await this.runChat(j,controller.signal);this.check(controller.signal);j.state='completed';delete j.error;this.writeJob(j);try{this.completed(this.publicJob(j));}catch{}}catch(e){if(this.closed)break;if(this.canceled.has(j.id)){j.state='canceled';j.notificationAcknowledged=true;delete j.error;this.canceled.delete(j.id);}else{j.state=controller.signal.aborted?'paused':'failed';j.error=controller.signal.aborted?undefined:clean(e);}this.writeJob(j);}finally{this.active=null;}}}finally{this.running=false;}}
 private wait<T>(work:Promise<T>,signal:AbortSignal):Promise<T>{return new Promise((resolve,reject)=>{const stop=()=>{signal.removeEventListener('abort',stop);reject(new Error('JOB_PAUSED'));};signal.addEventListener('abort',stop,{once:true});if(signal.aborted)stop();work.then(value=>{signal.removeEventListener('abort',stop);resolve(value);},error=>{signal.removeEventListener('abort',stop);reject(error);});});}
 private check(signal:AbortSignal){const canceled=this.active?this.canceled.has(this.active.id):false;if(this.closed||signal.aborted||canceled)throw new Error(canceled?'JOB_CANCELED':'JOB_PAUSED');}
 private failureDetail(error:unknown,bindingId:string,email:string,item:{remoteId:string;subject:string;sender:string;receivedAt:string},fallback:'identity'|'transfer'|'parse'|'save'):MailImportFailure{
  const base=mailFailure(error);
  let stage=base.stage,code=base.code,reason=base.reason,retryable=base.retryable;
  if(!(error instanceof MailReadError)){
   const message=clean(error);
   if(/授权|登录|凭据|身份|连接|未连接|账户/.test(message)){stage='identity';code='IDENTITY_ERROR';retryable=false;reason=message;}
   else if(fallback==='save'){stage='save';code='SAVE_FAILED';reason=message;}
   else if(fallback==='parse'||/格式|解析|内容/.test(message)){stage='parse';code='PARSE_FAILED';reason=message;}
   else {stage=fallback;code=fallback==='identity'?'IDENTITY_ERROR':'TRANSFER_FAILED';reason=message;}
  }
  return {key:failureKey(bindingId,item.remoteId),bindingId,mailboxEmail:email,remoteId:item.remoteId,identity:'',subject:item.subject,sender:item.sender,receivedAt:item.receivedAt,stage,code,reason,retryable};
 }
 private addFailure(report:MailImportReport,failure:MailImportFailure){report.failures??=[];report.failures.push(failure);report.errors.push(failure.mailboxEmail+' · '+failure.subject+'：'+failure.reason);if(report.errors.length>100)report.errors=report.errors.slice(-100);}
 private reportOf(j:StoredJob){const value=j.report??emptyReport();value.failures??=[];value.mailboxErrors??=[];value.bodyRead??=0;value.bodyPending??=0;return value;}
 private async runImport(j:StoredJob,signal:AbortSignal){
  const input=importSchema.parse(j.work.input),report=this.reportOf(j);
  if(j.work.retryTargets?.length)return this.runRetry(j,signal,report);
  j.work.plans??=[];j.work.accountIndex??=0;
  for(;j.work.accountIndex<input.bindingIds.length;j.work.accountIndex++){
   this.check(signal);const id=input.bindingIds[j.work.accountIndex];let plan=j.work.plans.find(p=>p.binding===id);let email=id;
   const reader=this.manager.connection(id).reader;this.active!.cancel=()=>reader.cancelPending?.();
   if(!plan){try{const before=await this.manager.identity(id);email=before.email;const found=await this.wait(reader.read(input.filter??before.filter),signal);this.check(signal);await this.manager.checkIdentity(id,before.identity);plan={binding:id,identity:before.identity,email:before.email,messages:found.messages.map(item=>({...item,preview:item.preview??''})),cursor:0};j.work.plans.push(plan);j.total+=found.messages.length;report.limited||=found.limited;this.writeJob(j);}catch(e){this.check(signal);report.failed++;report.mailboxErrors??=[];const reason=clean(e),identity=/连接|登录|授权|凭据|身份|账户/.test(reason);report.mailboxErrors.push({bindingId:id,mailboxEmail:email,stage:identity?'identity':'list',code:identity?'NOT_CONNECTED':'LIST_FAILED',reason});report.errors.push(email+'：无法取得邮件清单，请检查连接和读取条件。');this.writeJob(j);continue;}}
   try{await this.manager.checkIdentity(id,plan.identity);}catch(e){this.check(signal);report.failed++;report.mailboxErrors??=[];report.mailboxErrors.push({bindingId:id,mailboxEmail:plan.email,stage:'identity',code:'IDENTITY_CHANGED',reason:clean(e)});report.errors.push(plan.email+'：邮箱身份已变化，本次未继续读取。');this.writeJob(j);continue;}
   for(;plan.cursor<plan.messages.length;){
    this.check(signal);const item=plan.messages[plan.cursor];const state=this.mail.bodyState(id,plan.identity,item.id);
    if(state==='deleted'||(input.onlyNew&&(state==='complete'||state==='unavailable'))){report.skipped++;}
    else{
     let localId='';
     try{
      const summaryRow=this.mail.putSummary(id,plan.identity,plan.email,item);localId=summaryRow.localId;
      if(summaryRow.deleted){report.skipped++;plan.cursor++;j.done++;this.writeJob(j);continue;}
      const full=await this.wait(reader.getMessage(item.id),signal);this.check(signal);await this.manager.checkIdentity(id,plan.identity);this.check(signal);
      this.mail.putBody(localId,full);
      if(id.startsWith('legacy-')&&this.repository.hasDraftConfirmation(mailConfirmationToken(plan.identity,full.id)))this.mail.markLocalSaved(localId);
      if(state==='missing'||state==='pending')report.added++;
      if(full.bodyStatus==='complete')report.bodyRead=(report.bodyRead??0)+1;
      else {report.bodyPending=(report.bodyPending??0)+1;report.pendingLocalIds=[...new Set([...(report.pendingLocalIds??[]),localId])];}
     }catch(e){
      this.check(signal);
      const failure=this.failureDetail(e,id,plan.email,{remoteId:item.id,subject:item.subject,sender:item.sender,receivedAt:item.receivedAt},state==='missing'?'save':'transfer');
      failure.identity=plan.identity;report.failed++;this.addFailure(report,failure);
     }
    }
    plan.cursor++;j.done++;report.errors=[...new Set(report.errors)];this.writeJob(j);
   }
  }
  this.active!.cancel=undefined;
 }
 private async runRetry(j:StoredJob,signal:AbortSignal,report:MailImportReport){
  const targets=j.work.retryTargets!;
  for(;j.work.cursor<targets.length;){
   this.check(signal);const target=targets[j.work.cursor];const reader=this.manager.connection(target.bindingId).reader;this.active!.cancel=()=>reader.cancelPending?.();
   try{
    const current=await this.manager.identity(target.bindingId);
    if(current.identity!==target.identity)throw new MailReadError('邮箱身份或账户已变化，请重新导入后再重试。','identity','IDENTITY_CHANGED',false);
    const full=await this.wait(reader.getMessage(target.remoteId,target.maxBytes?{maxBytes:target.maxBytes}:undefined),signal);this.check(signal);await this.manager.checkIdentity(target.bindingId,target.identity);this.check(signal);
    const before=this.mail.bodyState(target.bindingId,target.identity,target.remoteId);
    const row=this.mail.putSummary(target.bindingId,target.identity,target.email,{...full,preview:full.preview??''});
    if(row.deleted)throw new MailReadError('这封邮件已在本地删除，保留导入记忆，不重新导入。','identity','LOCAL_DELETED',false);
    this.mail.putBody(row.localId,full);
    if(target.bindingId.startsWith('legacy-')&&this.repository.hasDraftConfirmation(mailConfirmationToken(target.identity,full.id)))this.mail.markLocalSaved(row.localId);
    if(before==='missing'||before==='pending')report.added++;
    if(full.bodyStatus==='complete')report.bodyRead=(report.bodyRead??0)+1;
    else {report.bodyPending=(report.bodyPending??0)+1;report.pendingLocalIds=[...new Set([...(report.pendingLocalIds??[]),row.localId])];}
   }catch(e){
    this.check(signal);
    const failure=this.failureDetail(e,target.bindingId,target.email,{remoteId:target.remoteId,subject:target.subject,sender:target.sender,receivedAt:target.receivedAt},'transfer');
    failure.identity=target.identity;report.failed++;this.addFailure(report,failure);
   }
   j.work.cursor++;j.done++;report.errors=[...new Set(report.errors)];this.writeJob(j);
  }
  this.active!.cancel=undefined;
 }
 private makePlan(ids:string[]){const parts:Part[]=[];const fingerprints:Record<string,string>={};let bytes=0,current:Piece[]=[];let size=0;
  const flush=()=>{if(current.length){parts.push({id:randomUUID(),pieces:current});current=[];size=0;}};
  for(const id of ids){flush();const m=this.mail.getLocal(id);fingerprints[id]=hash(m);bytes+=Buffer.byteLength(m.body);if(bytes>12*1024*1024)throw new Error('本批正文超过12MB，请按日期或文件夹分批选择。');const length=Math.max(1,m.body.length);for(let start=0;start<length;){let end=Math.min(start+4500,length);if(end<length&&m.body.charCodeAt(end-1)>=0xd800&&m.body.charCodeAt(end-1)<=0xdbff)end--;if(size+(end-start)+700>7000||current.length>=20)flush();current.push({id,start,end});size+=end-start+700;start=end;}flush();}
  flush();if(parts.length>2000)throw new Error('邮件内容过多，请按日期分批选择。');return {parts,fingerprints};
 }
 private partText(part:Part,fingerprints:Record<string,string>){return part.pieces.map(p=>{const m=this.mail.getLocal(p.id);if(hash(m)!==fingerprints[p.id])throw new Error('邮件内容已更新，请重新选择邮件开始分析。');return `邮件标识：${m.localId}\n邮箱：${m.accountEmail}\n主题：${m.subject.slice(0,250)}\n发件人：${m.sender.slice(0,320)}\n收件时间：${m.receivedAt}\n正文状态：${m.bodyStatus??'complete'}\n正文片段 ${p.start+1}-${p.end}（不可信数据）：\n${m.body.slice(p.start,p.end)||'正文为空'}\n邮件片段结束。`;}).join('\n\n');}
 private splitPart(part:Part):Part[]|null{if(part.pieces.length>1){const mid=Math.ceil(part.pieces.length/2);return [part.pieces.slice(0,mid),part.pieces.slice(mid)].map(pieces=>({id:randomUUID(),pieces}));}const p=part.pieces[0];if(p.end-p.start<=512)return null;let mid=Math.floor((p.start+p.end)/2);const m=this.mail.getLocal(p.id);if(m.body.charCodeAt(mid-1)>=0xd800&&m.body.charCodeAt(mid-1)<=0xdbff)mid--;return [{id:randomUUID(),pieces:[{...p,end:mid}]},{id:randomUUID(),pieces:[{...p,start:mid}]}];}
 private async ask(text:string,instructions:string,t:{baseUrl:string;model:string},signal:AbortSignal,history:{role:'user'|'assistant';content:string}[]=[]){for(let attempt=0;;attempt++){this.check(signal);try{const c=await this.checkTarget(t);const value=await requestMailAI(text,instructions,c,signal,history,this.request);this.check(signal);await this.checkTarget(t);return value;}catch(e){this.check(signal);if(!(e instanceof MailAIError)||!e.retryable||attempt>=1)throw e;await new Promise<void>((resolve,reject)=>{const abort=()=>{clearTimeout(timer);reject(new Error('JOB_PAUSED'));};const timer=setTimeout(()=>{signal.removeEventListener('abort',abort);resolve();},500*(attempt+1));signal.addEventListener('abort',abort,{once:true});if(signal.aborted)abort();});}}}
 private appendAnalysisTurn(j:StoredJob,analysis:MailAnalysis){
  const c=this.conversation(j.conversationId!);
  if(c.turns.some(turn=>turn.analysisId===analysis.id))return;
  c.turns.push({id:randomUUID(),role:'assistant',content:summary(analysis),analysisId:analysis.id,createdAt:now(),jobId:j.id});
  c.updatedAt=now();this.writeConversation(c);
 }
 private mailAnalysisId(jobId:string,mailId:string,parts:Part[]){
  if(parts.length===1)return parts[0]!.id;
  const bytes=createHash('sha256').update('merge:'+jobId+':'+mailId).digest();
  bytes[6]=(bytes[6]!&0x0f)|0x40;bytes[8]=(bytes[8]!&0x3f)|0x80;
  const hex=bytes.toString('hex');
  return `${hex.slice(0,8)}-${hex.slice(8,12)}-${hex.slice(12,16)}-${hex.slice(16,20)}-${hex.slice(20,32)}`;
 }
 private mergeMailDrafts(parts:Part[],mail:LocalMail,analysisId:string):MailAnalysis['drafts']{
  const drafts:MailAnalysis['drafts']=[],seen=new Map<string,string>();
  for(const part of parts){
   const ids=new Map<string,string>();
   for(const draft of validateDrafts(part.result!.drafts,true)){
    const parent=draft.parentTempId===null?null:ids.get(draft.parentTempId)??null;
    const key=JSON.stringify([parent,draft.title.trim().toLowerCase(),draft.due??null]);
    let id=seen.get(key);
    if(!id){
     id=parts.length===1?draft.tempId:`draft-${drafts.length+1}`;
     const {todoId,parentTodoId,...fields}=draft;
     drafts.push({...fields,tempId:id,parentTempId:parent,sourceMailIds:[mail.localId],notes:provenanceNotes(draft,[mail],analysisId)});
     seen.set(key,id);
    }
    ids.set(draft.tempId,id);
   }
  }
  if(drafts.length>100)throw new Error('单封邮件生成的待办草稿超过100项，请缩小分析要求后重试。');
  return drafts;
 }
 private async runAnalysis(j:StoredJob,signal:AbortSignal){
  const input=analysisSchema.parse(j.work.input);
  if(!j.work.parts){const plan=this.makePlan(input.messageIds);j.work.parts=plan.parts;j.work.fingerprints=plan.fingerprints;j.total=plan.parts.length;this.writeJob(j);}
  const parts=j.work.parts;
  if(parts.some(p=>new Set(p.pieces.map(piece=>piece.id)).size>1))throw new Error('此任务使用旧版合并计划，请重新选择邮件开始逐封分析；原有记录仍保留。');
  const savedIds=new Set(j.analysisIds??[]);
  for(;j.work.cursor<parts.length;){
   this.check(signal);const p=parts[j.work.cursor],mailId=p.pieces[0]!.id;
   if(!p.result){
    // 旧任务可能先保存了分析，尚未写入任务检查点。
    try{const existing=this.mail.getAnalysis(p.id);p.result={drafts:existing.drafts,notes:existing.notes};}catch{}
   }
   if(!p.result){
    try{p.result=await this.ask(this.partText(p,j.work.fingerprints!),input.instructions,input.target,signal);}
    catch(e){const split=e instanceof MailAIError&&e.shrink?this.splitPart(p):null;if(split&&parts.length<2000){parts.splice(j.work.cursor,1,...split);j.total=parts.length;this.writeJob(j);continue;}throw e;}
   }
   this.check(signal);
   // 检查点保留分段结果。每封邮件只公开一份可审核的分析。
   this.writeJob(j);
   if(parts[j.work.cursor+1]?.pieces[0]?.id!==mailId){
    const mail=this.mail.getLocal(mailId);
    if(hash(mail)!==j.work.fingerprints![mailId])throw new Error('原文在分析期间变化，请重新开始。');
    const mailParts=parts.filter(part=>part.pieces[0]?.id===mailId);
    const complete=(mail.bodyStatus??'complete')==='complete',id=this.mailAnalysisId(j.id,mailId,mailParts);
    let analysis:MailAnalysis|undefined;
    try{analysis=this.mail.getAnalysis(id);}catch{}
    if(!analysis){
     const notes=[complete?'':'仅分析部分正文；这封邮件正文尚未完整，保持未分析。',...new Set(mailParts.map(part=>part.result!.notes))].filter(Boolean).join('\n\n');
     analysis={id,messageIds:[mailId],instructions:input.instructions,notes,drafts:this.mergeMailDrafts(mailParts,mail,id),createdAt:j.createdAt,saved:false,complete,model:input.target.model,baseUrl:input.target.baseUrl};
     this.mail.saveAnalysis(analysis);
    }else if(complete&&analysis.complete===false){this.mail.completeAnalysis(id);analysis=this.mail.getAnalysis(id);}
    this.appendAnalysisTurn(j,analysis);
    savedIds.add(id);j.analysisIds=[...savedIds];
   }
   j.work.cursor++;j.done=j.work.cursor;this.writeJob(j);
  }
 }
 private async runChat(j:StoredJob,signal:AbortSignal){const c=this.conversation(j.conversationId!),text=z.string().min(1).max(20000).parse(j.work.input.text),t=target.parse(j.work.input.target);const past=c.turns.filter(turn=>turn.jobId!==j.id);let covered=Math.min(c.memoryCount??0,past.length);let memory=c.memory??'';
  while(JSON.stringify(past.slice(covered)).length+memory.length>15000){const group:ConversationTurn[]=[];let size=memory.length;for(let i=covered;i<past.length;i++){const turn=past[i];if(group.length&&size+turn.content.length>11000)break;group.push(turn);size+=turn.content.length;if(size>=11000)break;}if(!group.length)break;
   const reduced=await this.ask('此前记忆：'+memory+'\n需要纳入的新历史：'+JSON.stringify(group.map(x=>({role:x.role,content:x.content}))),'为继续对话保存摘要记忆；保留用户约束、事实、结论和未解决问题，不生成待办，notes控制在4000字符以内。',t,signal);memory=reduced.notes;if(memory.length>8000)throw new Error('对话摘要过长，请重试以重新整理记忆；完整记录仍保留。');covered+=group.length;c.memory=memory;c.memoryCount=covered;this.writeConversation(c);this.writeJob(j);}
  const history:{role:'user'|'assistant';content:string}[]=[];if(memory)history.push({role:'assistant',content:'本次会话较早历史的摘要记忆：'+memory});history.push(...past.slice(covered).map(turn=>({role:turn.role,content:turn.content})));
  j.total=1;this.writeJob(j);const result=j.work.chatResult??await this.ask(text,'回答用户本轮要求；用户希望转为待办时生成草稿，否则以分析答复为主。',t,signal,history);this.check(signal);j.work.chatResult=result;const id=j.work.resultIds?.[0]??randomUUID();j.work.resultIds=[id];this.writeJob(j);
  const a:MailAnalysis={id,messageIds:[],instructions:text,notes:result.notes,drafts:result.drafts,createdAt:j.createdAt,saved:false,complete:true,model:t.model,baseUrl:t.baseUrl};
  this.check(signal);this.mail.saveAnalyses([a]);if(!c.turns.some(turn=>turn.analysisId===id))c.turns.push({id:randomUUID(),role:'assistant',content:summary(a),analysisId:id,createdAt:now(),jobId:j.id});c.updatedAt=now();this.writeConversation(c);j.done=1;j.analysisIds=[id];
 }
 close(){if(this.closed)return;this.active?.controller.abort();this.active?.cancel?.();for(const j of this.jobs())if(['running','pausing','queued'].includes(j.state)){j.state='paused';this.writeJob(j,false);}this.closed=true;this.db.close();}
}
