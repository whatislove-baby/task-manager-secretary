import { DatabaseSync } from 'node:sqlite';
import { createHash, randomUUID } from 'node:crypto';
import { existsSync, readFileSync, writeFileSync, renameSync, mkdirSync, rmSync, statSync } from 'node:fs';
import { join } from 'node:path';
import { z } from 'zod';
import type { KeyVault } from './settings';
import { mailConfirmationToken } from './mail-service';
import { OutlookAuth } from './outlook-auth';
import { OutlookGraph } from './outlook-graph';
import { ImapAuth } from './imap-auth';
import { ImapMailbox } from './imap-mailbox';
import { mailFilterSchema, imapConfigSchema, defaultMailFilter, type MailMethod, type MailState, type MailMessage, type MailSummary, type MailFilter, type ImapConfigInput } from '../shared/mail';
import type { MailBinding, MailWorkspaceAPI, LocalMail, LocalMailQuery, MailAnalysis, MailImportReport, MailPreset, LocalMailFolder } from '../shared/mail-workspace';

const idSchema=z.string().min(1).max(2048);
const registryEntry=z.strictObject({id:z.union([z.string().uuid(),z.enum(['legacy-imap','legacy-microsoft'])]),method:z.enum(['imap','microsoft'])});
const registrySchema=z.array(registryEntry).max(100).refine(items=>new Set(items.map(v=>v.id)).size===items.length);
type Entry=z.infer<typeof registryEntry>;
interface Connection { auth:Pick<OutlookAuth,'getState'|'connect'|'disconnect'|'saveFilter'> & {configure:(input:any)=>Promise<MailState>}; reader:Pick<OutlookGraph,'listFolders'|'read'|'getMessage'> & {cancelPending?:()=>void} }
export type MailConnectionFactory=(entry:Entry,directory:string)=>Connection;
/** Each binding owns its credentials; selecting a row never changes other bindings. */
export class MailAccountManager {
 private file:string;
 private connections=new Map<string,Connection>();
 private entries:Entry[];
 constructor(private directory:string,vault:KeyVault,openBrowser:(url:string)=>Promise<void>,private factory?:MailConnectionFactory){
  mkdirSync(directory,{recursive:true});this.file=join(directory,'mail-accounts.json');
  if(!factory)this.factory=(entry,dir)=>{if(entry.method==='microsoft'){const auth=new OutlookAuth(dir,vault,openBrowser);return {auth,reader:new OutlookGraph(auth)};}const auth=new ImapAuth(dir,vault,credentials=>new ImapMailbox(async()=>credentials).testConnection());return {auth,reader:new ImapMailbox(()=>auth.getCredentials())};};
  if(existsSync(this.file)){try{this.entries=registrySchema.parse(JSON.parse(readFileSync(this.file,'utf8')));}catch{throw new Error('多邮箱配置损坏，请先备份 data 并检查 mail-accounts.json。');}}
  else {this.entries=[];if(existsSync(join(directory,'imap-auth.bin')))this.entries.push({id:'legacy-imap',method:'imap'});if(existsSync(join(directory,'outlook-auth.bin')))this.entries.push({id:'legacy-microsoft',method:'microsoft'});this.write();}
  for(const entry of this.entries)if(entry.id.startsWith('legacy-')&&entry.id!=='legacy-'+entry.method)throw new Error('旧邮箱配置标识不匹配。');
 }
 private write(){writeFileSync(this.file+'.tmp',JSON.stringify(this.entries),{mode:0o600});renameSync(this.file+'.tmp',this.file);}
 private entry(id:string){const entry=this.entries.find(v=>v.id===idSchema.parse(id));if(!entry)throw new Error('找不到此邮箱绑定。');return entry;}
 connection(id:string){const entry=this.entry(id);let connection=this.connections.get(id);if(!connection){const dir=id.startsWith('legacy-')?this.directory:join(this.directory,'mail-accounts',entry.id);mkdirSync(dir,{recursive:true});connection=this.factory!(entry,dir);this.connections.set(id,connection);}return connection;}
 async getAccount(id:string):Promise<MailBinding>{const entry=this.entry(id);return {...entry,state:{...await this.connection(id).auth.getState(),method:entry.method}};}
 async getAccounts(){return Promise.all(this.entries.map(entry=>this.getAccount(entry.id)));}
 async addAccount(method:MailMethod){const valid=z.enum(['imap','microsoft']).parse(method);if(this.entries.length>=100)throw new Error('邮箱绑定已达100个，请使用已有绑定。');const entry={id:randomUUID(),method:valid};this.entries.push(entry);try{this.write();}catch(error){this.entries.pop();throw error;}return this.getAccount(entry.id);}
 /** 只删除绑定与登录凭据；本地邮件、分析、对话和预设保留。 */
 removeAccount(id:string){const entry=this.entry(id);this.connections.delete(entry.id);this.entries=this.entries.filter(value=>value.id!==entry.id);this.write();try{if(entry.id.startsWith('legacy-'))rmSync(join(this.directory,entry.method==='imap'?'imap-auth.bin':'outlook-auth.bin'),{force:true});else rmSync(join(this.directory,'mail-accounts',entry.id),{recursive:true,force:true});}catch{/* 残留凭据文件不影响已移除的绑定 */}}
 async configureImap(id:string,input:ImapConfigInput){if(this.entry(id).method!=='imap')throw new Error('此绑定不使用 IMAP。');await this.connection(id).auth.configure(imapConfigSchema.parse(input));return this.getAccount(id);}
 async configureMicrosoft(id:string,clientId:string){if(this.entry(id).method!=='microsoft')throw new Error('此绑定不使用 Microsoft 授权。');await this.connection(id).auth.configure(z.string().trim().uuid().parse(clientId));return this.getAccount(id);}
 async connect(id:string){await this.connection(id).auth.connect();return this.getAccount(id);}
 async disconnect(id:string){await this.connection(id).auth.disconnect();return this.getAccount(id);}
 async saveFilter(id:string,filter:MailFilter){await this.connection(id).auth.saveFilter(mailFilterSchema.parse(filter));return this.getAccount(id);}
 async listFolders(id:string,parent?:string){const before=await this.identity(id);const result=await this.connection(id).reader.listFolders(parent===undefined?undefined:idSchema.parse(parent));await this.checkIdentity(id,before.identity);return result;}
 async identity(id:string){const binding=await this.getAccount(id);if(!binding.state.account)throw new Error('请先连接此邮箱。');return {identity:binding.state.account.id,email:binding.state.account.email,filter:binding.state.filter};}
 async checkIdentity(id:string,identity:string){if((await this.identity(id)).identity!==identity)throw new Error('邮箱身份已变化，请重新导入。');}
}
const localKey=(binding:string,identity:string,remote:string)=>createHash('sha256').update(JSON.stringify([binding,identity,remote])).digest('hex');
const querySchema=z.strictObject({bindingIds:z.array(idSchema).max(100).optional(),folderId:z.string().uuid().nullable().optional(),status:z.enum(['unanalyzed','analyzed']).optional(),search:z.string().max(1000).optional(),offset:z.number().int().min(0).optional(),limit:z.number().int().min(1).max(1000).optional()});
/** Mail text and analysis history are independent of the todo database and survive restarts. */
export class MailWorkspaceStore {
 private db:DatabaseSync;
 private file:string;
 constructor(directory:string){mkdirSync(directory,{recursive:true});this.file=join(directory,'mail.sqlite');this.db=new DatabaseSync(this.file);this.db.exec(`PRAGMA foreign_keys=ON; PRAGMA busy_timeout=5000; PRAGMA secure_delete=ON;
 CREATE TABLE IF NOT EXISTS local_mail(local_id TEXT PRIMARY KEY,binding_id TEXT NOT NULL,identity TEXT NOT NULL,remote_id TEXT NOT NULL,payload TEXT NOT NULL,fingerprint TEXT NOT NULL,analysis_id TEXT,analyzed_at TEXT,saved_at TEXT,UNIQUE(binding_id,identity,remote_id));
 CREATE TABLE IF NOT EXISTS mail_analyses(id TEXT PRIMARY KEY,payload TEXT NOT NULL,saved INTEGER NOT NULL DEFAULT 0);
 CREATE INDEX IF NOT EXISTS local_mail_binding ON local_mail(binding_id);`);
  this.db.exec('BEGIN IMMEDIATE');try{
   if(!(this.db.prepare('PRAGMA table_info(local_mail)').all() as {name:string}[]).some(row=>row.name==='folder_id'))this.db.exec('ALTER TABLE local_mail ADD COLUMN folder_id TEXT');
   this.db.exec(`CREATE TABLE IF NOT EXISTS mail_receipts(local_id TEXT PRIMARY KEY,binding_id TEXT NOT NULL,identity TEXT NOT NULL,remote_id TEXT NOT NULL,imported_at TEXT NOT NULL,analysis_id TEXT,analyzed_at TEXT,deleted INTEGER NOT NULL DEFAULT 0);
    CREATE TABLE IF NOT EXISTS mail_known_senders(binding_id TEXT NOT NULL,sender TEXT NOT NULL,PRIMARY KEY(binding_id,sender));
    CREATE TABLE IF NOT EXISTS mail_presets(id TEXT PRIMARY KEY,binding_id TEXT NOT NULL,name TEXT NOT NULL,filter TEXT NOT NULL,UNIQUE(binding_id,name));
    CREATE TABLE IF NOT EXISTS mail_preset_meta(binding_id TEXT PRIMARY KEY,seeded_at TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS mail_local_folders(id TEXT PRIMARY KEY,name TEXT NOT NULL UNIQUE);
    INSERT OR IGNORE INTO mail_receipts SELECT local_id,binding_id,identity,remote_id,json_extract(payload,'$.cachedAt'),analysis_id,analyzed_at,0 FROM local_mail;
    INSERT OR IGNORE INTO mail_known_senders SELECT binding_id,json_extract(payload,'$.sender') FROM local_mail WHERE json_extract(payload,'$.sender')<>'';`);
   this.db.exec('COMMIT');
  }catch(error){this.db.exec('ROLLBACK');this.db.close();throw error;}
 }
 close(){this.db.close();}
 has(binding:string,identity:string,remote:string){return !!this.db.prepare('SELECT 1 FROM mail_receipts WHERE local_id=?').get(localKey(binding,identity,remote));}
 isDeleted(binding:string,identity:string,remote:string){return !!this.db.prepare('SELECT 1 FROM mail_receipts WHERE local_id=? AND deleted=1').get(localKey(binding,identity,remote));}
 private mailFingerprint(item:Pick<MailMessage,'subject'|'sender'|'receivedAt'|'body'>){return createHash('sha256').update(JSON.stringify([item.subject,item.sender,item.receivedAt,item.body])).digest('hex');}
 private checkDatabaseSize(){if(statSync(this.file).size>512*1024*1024)throw new Error('本地邮件库已超过512MB，本次未保存更多邮件。');}
 private assertPayloadSize(payload:LocalMail){if(Buffer.byteLength(JSON.stringify(payload))>16*1024*1024)throw new Error('邮件正文超过本地保存上限（约16MB），请打开原邮箱查看。');}
 private receiptDeleted(localId:string){return !!this.db.prepare('SELECT 1 FROM mail_receipts WHERE local_id=? AND deleted=1').get(localId);}
 /** 先保存摘要，让邮件立即出现在列表；正文状态保持 pending，不冒充完整导入。 */
 putSummary(binding:string,identity:string,email:string,item:MailSummary,extra:Partial<Pick<MailMessage,'attachments'|'hasHtml'|'bodyNote'|'preview'>>={}){
  this.checkDatabaseSize();
  const localId=localKey(binding,identity,item.id);
  if(this.receiptDeleted(localId))return {created:false,deleted:true,localId};
  const existing=this.db.prepare('SELECT payload FROM local_mail WHERE local_id=?').get(localId);
  const now=new Date().toISOString();
  if(existing){
   const previous=JSON.parse(String(existing.payload)) as LocalMail;
   const next:LocalMail={...previous,...item,preview:item.preview||extra.preview||previous.preview||'',attachments:extra.attachments??previous.attachments??[],hasHtml:extra.hasHtml??previous.hasHtml??false,bodyStatus:previous.bodyStatus??(previous.body?'complete':'pending')};
   this.assertPayloadSize(next);
   this.db.prepare('UPDATE local_mail SET payload=? WHERE local_id=?').run(JSON.stringify(next),localId);
   return {created:false,deleted:false,localId};
  }
  const payload:LocalMail={...item,preview:item.preview||extra.preview||'',body:'',bodyStatus:'pending',bodyBytes:0,attachments:extra.attachments??[],hasHtml:extra.hasHtml??false,bodyNote:extra.bodyNote,localId,bindingId:binding,mailboxIdentity:identity,accountEmail:email,cachedAt:now,analyzedAt:null,analysisId:null,status:'unanalyzed'};
  this.assertPayloadSize(payload);
  this.db.exec('BEGIN IMMEDIATE');try{
   this.db.prepare('INSERT INTO local_mail(local_id,binding_id,identity,remote_id,payload,fingerprint) VALUES(?,?,?,?,?,?)').run(localId,binding,identity,item.id,JSON.stringify(payload),this.mailFingerprint(payload));
   this.db.prepare('INSERT OR IGNORE INTO mail_receipts(local_id,binding_id,identity,remote_id,imported_at) VALUES(?,?,?,?,?)').run(localId,binding,identity,item.id,now);
   if(item.sender)this.db.prepare('INSERT OR IGNORE INTO mail_known_senders VALUES(?,?)').run(binding,item.sender);
   this.db.exec('COMMIT');
  }catch(error){this.db.exec('ROLLBACK');throw error;}
  return {created:true,deleted:false,localId};
 }
 /** 保存正文（可完整、可部分、可 unavailable）；只有正文真正取得才更新 bodyStatus。 */
 putBody(localId:string,item:MailMessage){
  this.checkDatabaseSize();
  const row=this.db.prepare('SELECT payload,fingerprint FROM local_mail WHERE local_id=?').get(localId);
  if(!row)throw new Error('本地未保存此邮件摘要，请先导入。');
  if(this.receiptDeleted(localId))return {updated:false,added:false,localId};
  const previous=JSON.parse(String(row.payload)) as LocalMail;
  const payload:LocalMail={...previous,...item,localId:previous.localId,bindingId:previous.bindingId,mailboxIdentity:previous.mailboxIdentity,accountEmail:previous.accountEmail,cachedAt:previous.cachedAt,bodyStatus:item.bodyStatus??'complete',attachments:item.attachments??previous.attachments??[]};
  this.assertPayloadSize(payload);
  const fingerprint=this.mailFingerprint(payload);
  this.db.exec('BEGIN IMMEDIATE');try{
   this.db.prepare(`UPDATE local_mail SET payload=?,fingerprint=?,analysis_id=CASE WHEN fingerprint=? THEN analysis_id ELSE NULL END,analyzed_at=CASE WHEN fingerprint=? THEN analyzed_at ELSE NULL END WHERE local_id=?`).run(JSON.stringify(payload),fingerprint,fingerprint,fingerprint,localId);
   this.db.prepare(`INSERT INTO mail_receipts(local_id,binding_id,identity,remote_id,imported_at,analysis_id,analyzed_at) SELECT local_id,binding_id,identity,remote_id,json_extract(payload,'$.cachedAt'),analysis_id,analyzed_at FROM local_mail WHERE local_id=? ON CONFLICT(local_id) DO UPDATE SET analysis_id=excluded.analysis_id,analyzed_at=excluded.analyzed_at`).run(localId);
   if(item.sender)this.db.prepare('INSERT OR IGNORE INTO mail_known_senders VALUES(?,?)').run(previous.bindingId,item.sender);
   this.db.exec('COMMIT');
  }catch(error){this.db.exec('ROLLBACK');throw error;}
  return {updated:String(row.fingerprint)!==fingerprint,added:false,localId};
 }
 put(binding:string,identity:string,email:string,item:MailMessage){
  this.checkDatabaseSize();
  const localId=localKey(binding,identity,item.id),old=this.db.prepare('SELECT fingerprint FROM local_mail WHERE local_id=?').get(localId);
  if(this.receiptDeleted(localId))return {added:false,localId};
  const payload:LocalMail={...item,bodyStatus:item.bodyStatus??'complete',attachments:item.attachments??[],localId,bindingId:binding,mailboxIdentity:identity,accountEmail:email,cachedAt:new Date().toISOString(),analyzedAt:null,analysisId:null,status:'unanalyzed'};
  this.assertPayloadSize(payload);
  const fingerprint=this.mailFingerprint(payload);
  this.db.exec('BEGIN IMMEDIATE');try{
  this.db.prepare(`INSERT INTO local_mail(local_id,binding_id,identity,remote_id,payload,fingerprint) VALUES(?,?,?,?,?,?) ON CONFLICT(local_id) DO UPDATE SET payload=excluded.payload,fingerprint=excluded.fingerprint,analysis_id=CASE WHEN fingerprint=excluded.fingerprint THEN analysis_id ELSE NULL END,analyzed_at=CASE WHEN fingerprint=excluded.fingerprint THEN analyzed_at ELSE NULL END`).run(localId,binding,identity,item.id,JSON.stringify(payload),fingerprint);
  this.db.prepare(`INSERT INTO mail_receipts(local_id,binding_id,identity,remote_id,imported_at,analysis_id,analyzed_at) SELECT local_id,binding_id,identity,remote_id,json_extract(payload,'$.cachedAt'),analysis_id,analyzed_at FROM local_mail WHERE local_id=? ON CONFLICT(local_id) DO UPDATE SET analysis_id=excluded.analysis_id,analyzed_at=excluded.analyzed_at`).run(localId);
  if(item.sender)this.db.prepare('INSERT OR IGNORE INTO mail_known_senders VALUES(?,?)').run(binding,item.sender);
  this.db.exec('COMMIT');}catch(error){this.db.exec('ROLLBACK');throw error;}
  return {added:!old,localId};
 }
 /** 用于导入去重：摘要存在但正文待读取/不完整时仍允许补读。 */
 bodyState(binding:string,identity:string,remote:string):'missing'|'deleted'|'pending'|'partial'|'complete'|'unavailable'{
  const localId=localKey(binding,identity,remote);
  if(this.receiptDeleted(localId))return 'deleted';
  const row=this.db.prepare('SELECT payload FROM local_mail WHERE local_id=?').get(localId);
  if(!row)return 'missing';
  const payload=JSON.parse(String(row.payload)) as LocalMail;
  return payload.bodyStatus??(payload.body?'complete':'pending');
 }
 private decode(row:any):LocalMail {const item=JSON.parse(row.payload) as LocalMail;return {...item,analysisId:row.analysis_id,analyzedAt:row.analyzed_at,folderId:row.folder_id??null,status:row.analyzed_at?'analyzed':'unanalyzed',bodyStatus:item.bodyStatus??(item.body?'complete':'pending'),attachments:item.attachments??[]};}
 getLocal(id:string):LocalMail{const row=this.db.prepare('SELECT m.*,a.saved FROM local_mail m LEFT JOIN mail_analyses a ON a.id=m.analysis_id WHERE m.local_id=?').get(idSchema.parse(id));if(!row)throw new Error('本地未保存此邮件，请先导入。');return this.decode(row);}
 listLocal(input:LocalMailQuery){const query=querySchema.parse(input),clauses:string[]=[],args:(string|number)[]=[];
  if(query.bindingIds){if(!query.bindingIds.length)return {messages:[],total:0};clauses.push('m.binding_id IN ('+query.bindingIds.map(()=>'?').join(',')+')');args.push(...query.bindingIds);}
  if(query.status==='unanalyzed')clauses.push('m.analyzed_at IS NULL');else if(query.status==='analyzed')clauses.push('m.analyzed_at IS NOT NULL');
  if(query.folderId===null)clauses.push('m.folder_id IS NULL');else if(query.folderId){clauses.push('m.folder_id=?');args.push(query.folderId);}
  if(query.search?.trim()){clauses.push(`(instr(lower(json_extract(m.payload,'$.subject')),lower(?))>0 OR instr(lower(json_extract(m.payload,'$.sender')),lower(?))>0)`);args.push(query.search.trim(),query.search.trim());}
  const from=' FROM local_mail m LEFT JOIN mail_analyses a ON a.id=m.analysis_id'+(clauses.length?' WHERE '+clauses.join(' AND '):'');
  const total=Number(this.db.prepare('SELECT count(*) AS count'+from).get(...args)!.count);
  const rows=this.db.prepare('SELECT m.*,a.saved'+from+` ORDER BY json_extract(m.payload,'$.receivedAt') DESC,m.local_id LIMIT ? OFFSET ?`).all(...args,query.limit??200,query.offset??0);
  return {messages:rows.map(row=>({...this.decode(row),body:''})),total};
 }
 listAnalyses():MailAnalysis[]{return this.db.prepare('SELECT payload,saved FROM mail_analyses ORDER BY rowid DESC').all().map(row=>({...JSON.parse(String(row.payload)),saved:!!row.saved}));}
 listPresets(bindingId:string):MailPreset[]{
  const binding=idSchema.parse(bindingId);
  // 每个邮箱首次使用时写入一条可编辑的“默认预设”，之后即使改名或删除也不再自动重建。
  if(!this.db.prepare('SELECT 1 FROM mail_preset_meta WHERE binding_id=?').get(binding)){
   this.db.exec('BEGIN IMMEDIATE');
   try{
    this.db.prepare('INSERT OR IGNORE INTO mail_presets VALUES(?,?,?,?)').run(randomUUID(),binding,'默认预设',JSON.stringify(defaultMailFilter));
    this.db.prepare('INSERT OR IGNORE INTO mail_preset_meta VALUES(?,?)').run(binding,new Date().toISOString());
    this.db.exec('COMMIT');
   }catch(error){this.db.exec('ROLLBACK');throw error;}
  }
  return this.db.prepare('SELECT * FROM mail_presets WHERE binding_id=? ORDER BY name').all(binding).map(row=>({id:String(row.id),bindingId:String(row.binding_id),name:String(row.name),filter:mailFilterSchema.parse(JSON.parse(String(row.filter)))}));
 }
 savePreset(bindingId:string,input:{id?:string;name:string;filter:MailFilter}):MailPreset{
  const parsed=z.strictObject({id:z.string().uuid().optional(),name:z.string().trim().min(1).max(100),filter:mailFilterSchema}).parse(input),id=parsed.id??randomUUID();idSchema.parse(bindingId);
  if(parsed.id&&!this.db.prepare('SELECT 1 FROM mail_presets WHERE id=? AND binding_id=?').get(id,bindingId))throw new Error('找不到此邮箱的预设。');
  try{this.db.prepare('INSERT INTO mail_presets VALUES(?,?,?,?) ON CONFLICT(id) DO UPDATE SET name=excluded.name,filter=excluded.filter').run(id,bindingId,parsed.name,JSON.stringify(parsed.filter));}catch{throw new Error('预设名称已存在或无法保存。');}
  return {id,bindingId,name:parsed.name,filter:parsed.filter};
 }
 deletePreset(id:string){this.db.prepare('DELETE FROM mail_presets WHERE id=?').run(z.string().uuid().parse(id));}
 knownSenders(bindingId:string):string[]{return this.db.prepare('SELECT sender FROM mail_known_senders WHERE binding_id=? ORDER BY sender COLLATE NOCASE').all(idSchema.parse(bindingId)).map(row=>String(row.sender));}
 listFoldersLocal():LocalMailFolder[]{return this.db.prepare('SELECT id,name FROM mail_local_folders ORDER BY name').all().map(row=>({id:String(row.id),name:String(row.name)}));}
 createLocalFolder(name:string):LocalMailFolder{const id=randomUUID(),value=z.string().trim().min(1).max(100).parse(name);try{this.db.prepare('INSERT INTO mail_local_folders VALUES(?,?)').run(id,value);}catch{throw new Error('文件夹名称已存在或无法保存。');}return {id,name:value};}
 renameLocalFolder(id:string,name:string):LocalMailFolder{z.string().uuid().parse(id);const value=z.string().trim().min(1).max(100).parse(name);try{if(!this.db.prepare('UPDATE mail_local_folders SET name=? WHERE id=?').run(value,id).changes)throw new Error();}catch{throw new Error('找不到文件夹或名称已存在。');}return {id,name:value};}
 deleteLocalFolder(id:string){z.string().uuid().parse(id);this.db.exec('BEGIN IMMEDIATE');try{this.db.prepare('UPDATE local_mail SET folder_id=NULL WHERE folder_id=?').run(id);this.db.prepare('DELETE FROM mail_local_folders WHERE id=?').run(id);this.db.exec('COMMIT');}catch(error){this.db.exec('ROLLBACK');throw error;}}
 private validateIds(ids:string[]){return z.array(idSchema).min(1).max(1000).refine(values=>new Set(values).size===values.length).parse(ids);}
 moveLocal(ids:string[],folderId:string|null){this.validateIds(ids);if(folderId!==null&&!this.db.prepare('SELECT 1 FROM mail_local_folders WHERE id=?').get(z.string().uuid().parse(folderId)))throw new Error('找不到此本地文件夹。');this.db.exec('BEGIN IMMEDIATE');try{for(const id of ids)this.getLocal(id);for(const id of ids)this.db.prepare('UPDATE local_mail SET folder_id=? WHERE local_id=?').run(folderId,id);this.db.exec('COMMIT');return {count:ids.length};}catch(error){this.db.exec('ROLLBACK');throw error;}}
 deleteLocal(ids:string[]){this.validateIds(ids);let count=0;this.db.exec('BEGIN IMMEDIATE');try{for(const id of ids){const row=this.db.prepare('SELECT local_id FROM local_mail WHERE local_id=?').get(id);if(!row)continue;this.db.prepare('UPDATE mail_receipts SET deleted=1 WHERE local_id=?').run(id);count+=Number(this.db.prepare('DELETE FROM local_mail WHERE local_id=?').run(id).changes);}this.db.exec('COMMIT');return {count};}catch(error){this.db.exec('ROLLBACK');throw error;}}
 getAnalysis(id:string):MailAnalysis{const row=this.db.prepare('SELECT payload,saved FROM mail_analyses WHERE id=?').get(idSchema.parse(id));if(!row)throw new Error('找不到此分析记录。');return {...JSON.parse(String(row.payload)),saved:!!row.saved};}
 saveAnalysis(analysis:MailAnalysis){this.saveAnalyses([analysis]);}
 saveAnalyses(analyses:MailAnalysis[]){
  if(!Array.isArray(analyses)||analyses.length>2000)throw new Error('分析批次数量无效。');
  this.db.exec('BEGIN IMMEDIATE');try{for(const analysis of analyses){
   if(analysis.messageIds.length>200||new Set(analysis.messageIds).size!==analysis.messageIds.length)throw new Error('分析邮件数量无效。');
   const existing=this.db.prepare('SELECT payload FROM mail_analyses WHERE id=?').get(idSchema.parse(analysis.id));
   if(existing){if(String(existing.payload)!==JSON.stringify(analysis))throw new Error('此分析标识已用于其他结果。');continue;}
   for(const id of analysis.messageIds)this.getLocal(id);
   this.db.prepare('INSERT INTO mail_analyses(id,payload,saved) VALUES(?,?,?)').run(analysis.id,JSON.stringify(analysis),analysis.saved?1:0);
   // 只分析部分正文时保留分析记录，但不把邮件标成已分析。
   if(analysis.complete!==false)for(const id of analysis.messageIds){this.db.prepare('UPDATE local_mail SET analysis_id=?,analyzed_at=? WHERE local_id=?').run(analysis.id,analysis.createdAt,id);this.db.prepare('UPDATE mail_receipts SET analysis_id=?,analyzed_at=? WHERE local_id=?').run(analysis.id,analysis.createdAt,id);}
  }this.db.exec('COMMIT');}catch(error){this.db.exec('ROLLBACK');throw error;}
 }
 /** 记录同一分析中已分批添加的草稿，确保重试和追加都不会重复创建待办。 */
 markAnalysisProgress(id:string,addedTempIds:string[],addedTodos:Record<string,string>){
  const analysis=this.getAnalysis(id);
  const mergedIds=[...new Set([...(analysis.addedTempIds??[]),...addedTempIds])];
  const mergedTodos={...(analysis.addedTodos??{}),...addedTodos};
  const saved=analysis.drafts.every(draft=>mergedIds.includes(draft.tempId));
  const next:MailAnalysis={...analysis,addedTempIds:mergedIds,addedTodos:mergedTodos,saved};
  this.db.exec('BEGIN IMMEDIATE');try{
   this.db.prepare('UPDATE mail_analyses SET payload=?,saved=? WHERE id=?').run(JSON.stringify(next),saved?1:0,id);
   if(saved)for(const mailId of analysis.messageIds)this.db.prepare('UPDATE local_mail SET saved_at=COALESCE(saved_at,?) WHERE local_id=?').run(new Date().toISOString(),mailId);
   this.db.exec('COMMIT');
  }catch(error){this.db.exec('ROLLBACK');throw error;}
  return next;
 }
 /** 全文全部分段完成后才把邮件标为已分析。 */
 completeAnalysis(id:string){
  const analysis=this.getAnalysis(id);
  const next:MailAnalysis={...analysis,complete:true};
  this.db.exec('BEGIN IMMEDIATE');try{
   this.db.prepare('UPDATE mail_analyses SET payload=? WHERE id=?').run(JSON.stringify(next),id);
   for(const mailId of analysis.messageIds){this.db.prepare('UPDATE local_mail SET analysis_id=?,analyzed_at=? WHERE local_id=?').run(id,analysis.createdAt,mailId);this.db.prepare('UPDATE mail_receipts SET analysis_id=?,analyzed_at=? WHERE local_id=?').run(id,analysis.createdAt,mailId);}
   this.db.exec('COMMIT');
  }catch(error){this.db.exec('ROLLBACK');throw error;}
 }
 markLocalSaved(id:string){this.getLocal(id);const at=new Date().toISOString();this.db.prepare('UPDATE local_mail SET saved_at=COALESCE(saved_at,?),analyzed_at=COALESCE(analyzed_at,?) WHERE local_id=?').run(at,at,id);this.db.prepare('UPDATE mail_receipts SET analyzed_at=COALESCE(analyzed_at,?) WHERE local_id=?').run(at,id);}
 markAnalysisSaved(id:string){const analysis=this.getAnalysis(id);this.db.exec('BEGIN IMMEDIATE');try{this.db.prepare('UPDATE mail_analyses SET saved=1 WHERE id=?').run(id);for(const mailId of analysis.messageIds)this.db.prepare('UPDATE local_mail SET saved_at=COALESCE(saved_at,?) WHERE local_id=?').run(new Date().toISOString(),mailId);this.db.exec('COMMIT');}catch(error){this.db.exec('ROLLBACK');throw error;}}
}
export type MailWorkspaceBase=Omit<MailWorkspaceAPI,'analyze'|'getAnalysis'|'saveAnalysis'>;
export function createMailWorkspaceBase(manager:MailAccountManager,store:MailWorkspaceStore,openExternal:(url:string)=>Promise<void>,hasDraftConfirmation?:(token:string)=>boolean):MailWorkspaceBase {
 const result=async<T>(work:()=>T|Promise<T>)=>{try{return {ok:true as const,data:await work()};}catch(error){return {ok:false as const,error:error instanceof z.ZodError?'邮箱参数无效，请检查输入。':error instanceof Error?error.message.replace(/(?:https?|webcal):\/\/[^\s]+/gi,'［地址］').replace(/Bearer\s+[^\s]+/gi,'［凭据］'):'邮件操作失败。'};}};
 const pending=new Map<string,ReturnType<MailWorkspaceBase['importMail']>>();
 return {
  getAccounts:()=>result(()=>manager.getAccounts()),getAccount:id=>result(()=>manager.getAccount(id)),addAccount:method=>result(()=>manager.addAccount(method)),removeAccount:id=>result(()=>manager.removeAccount(id)),
  configureImap:(id,input)=>result(()=>manager.configureImap(id,input)),configureMicrosoft:(id,clientId)=>result(()=>manager.configureMicrosoft(id,clientId)),connect:id=>result(()=>manager.connect(id)),disconnect:id=>result(()=>manager.disconnect(id)),saveFilter:(id,filter)=>result(()=>manager.saveFilter(id,filter)),listFolders:(id,parent)=>result(()=>manager.listFolders(id,parent)),
  listPresets:bindingId=>result(async()=>{await manager.getAccount(bindingId);return store.listPresets(bindingId);}),savePreset:(bindingId,input)=>result(async()=>{await manager.getAccount(bindingId);return store.savePreset(bindingId,input);}),deletePreset:id=>result(()=>store.deletePreset(id)),knownSenders:bindingId=>result(()=>store.knownSenders(bindingId)),
  listFoldersLocal:()=>result(()=>store.listFoldersLocal()),createLocalFolder:name=>result(()=>store.createLocalFolder(name)),renameLocalFolder:(id,name)=>result(()=>store.renameLocalFolder(id,name)),deleteLocalFolder:id=>result(()=>store.deleteLocalFolder(id)),moveLocal:(ids,folderId)=>result(()=>store.moveLocal(ids,folderId)),deleteLocal:ids=>result(()=>store.deleteLocal(ids)),
  listLocal:query=>result(()=>store.listLocal(query)),getLocal:id=>result(()=>store.getLocal(id)),
  openOriginal:id=>result(async()=>{const item=store.getLocal(id);let url:URL;try{url=new URL(item.webUrl);}catch{throw new Error('此邮件没有可用的网页原文链接。');}if(url.protocol!=='https:'||url.username||url.password||!['outlook.office.com','outlook.office365.com','outlook.live.com'].includes(url.hostname))throw new Error('原邮件链接无效。');await openExternal(url.href);}),
  importMail:input=>{const key=JSON.stringify(input);const active=pending.get(key);if(active)return active;const operation=result(async()=>{
   const valid=z.strictObject({bindingIds:z.array(idSchema).min(1).max(100).refine(ids=>new Set(ids).size===ids.length),filter:mailFilterSchema.optional(),onlyNew:z.boolean()}).parse(input);
   const report:MailImportReport={added:0,skipped:0,failed:0,limited:false,errors:[]};
   for(const bindingId of valid.bindingIds){let label='邮箱';try{const binding=await manager.getAccount(bindingId);label=binding.state.account?.email||binding.state.imap?.email||bindingId;const before=await manager.identity(bindingId),reader=manager.connection(bindingId).reader;const found=await reader.read(valid.filter??before.filter);await manager.checkIdentity(bindingId,before.identity);report.limited ||= found.limited;
    for(const summary of found.messages){if((valid.onlyNew||store.isDeleted(bindingId,before.identity,summary.id))&&store.has(bindingId,before.identity,summary.id)){report.skipped++;continue;}try{const full=await reader.getMessage(summary.id);await manager.checkIdentity(bindingId,before.identity);const saved=store.put(bindingId,before.identity,before.email,full);if(saved.added&&bindingId.startsWith('legacy-')&&hasDraftConfirmation?.(mailConfirmationToken(before.identity,full.id)))store.markLocalSaved(saved.localId);if(saved.added)report.added++;else report.skipped++;}catch{report.failed++;report.errors.push(label+'：一封邮件未能保存，可稍后重试或在原邮箱查看。');}}
   }catch{report.failed++;report.errors.push(label+'：导入未完成，请检查连接与筛选条件。');}}
   report.errors=[...new Set(report.errors)];return report;
  }).finally(()=>pending.delete(key));pending.set(key,operation);return operation;},
 };
}
