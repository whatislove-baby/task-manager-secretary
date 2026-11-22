import { readFileSync,writeFileSync,renameSync,existsSync } from 'node:fs';
import { join } from 'node:path';
import { z } from 'zod';
import type { MailMethod,MailState,MailFilter,ImapConfigInput } from '../shared/mail';
import type { OutlookAuth } from './outlook-auth';
import type { OutlookGraph } from './outlook-graph';
import type { ImapAuth } from './imap-auth';
import type { ImapMailbox } from './imap-mailbox';

type Outlook=Pick<OutlookAuth,'getState'|'configure'|'connect'|'disconnect'|'saveFilter'>;
type Imap=Pick<ImapAuth,'getState'|'configure'|'connect'|'disconnect'|'saveFilter'>;
type Reader=Pick<OutlookGraph,'listFolders'|'read'|'getMessage'>;
const methodSchema=z.enum(['imap','microsoft']);
const selectionSchema=z.strictObject({method:methodSchema});
/** Selects a binding mechanism, never an email provider. Existing OAuth registration remains usable. */
export class MailConnections {
 private file:string;
 private queue:Promise<unknown>=Promise.resolve();
 private epoch=0;
 constructor(directory:string,private outlook:Outlook,private graph:Reader,private imap:Imap,private mailbox:Pick<ImapMailbox,'listFolders'|'read'|'getMessage'>){this.file=join(directory,'mail-connection.json');}
 private serial<T>(action:()=>Promise<T>):Promise<T>{const next=this.queue.then(action);this.queue=next.catch(()=>{});return next;}
 private selected():MailMethod|null{
  if(!existsSync(this.file))return null;
  try{return selectionSchema.parse(JSON.parse(readFileSync(this.file,'utf8'))).method;}
  catch{throw new Error('邮箱绑定方式配置损坏，请先备份 data 并检查 mail-connection.json。');}
 }
 private async mode():Promise<MailMethod>{const saved=this.selected();if(saved)return saved;const old=await this.outlook.getState();return old.account||old.clientId?'microsoft':'imap';}
 private async snapshot(){await this.queue;return {method:await this.mode(),epoch:this.epoch};}
 private async unchanged(snapshot:{method:MailMethod;epoch:number}){if(snapshot.epoch!==this.epoch||(await this.mode())!==snapshot.method)throw new Error('邮箱绑定方式已变化，请重新操作。');}
 private auth(method:MailMethod){return method==='imap'?this.imap:this.outlook;}
 private reader(method:MailMethod){return method==='imap'?this.mailbox:this.graph;}
 async getState():Promise<MailState>{const state=await this.snapshot(),value=await this.auth(state.method).getState();await this.unchanged(state);return {...value,method:state.method};}
 selectMethod(value:MailMethod):Promise<MailState>{
  const method=methodSchema.parse(value);this.selected();const generation=++this.epoch;
  return this.serial(async()=>{if(generation!==this.epoch)throw new Error('邮箱绑定方式已变化，请重新操作。');writeFileSync(this.file+'.tmp',JSON.stringify({method}),{mode:0o600});renameSync(this.file+'.tmp',this.file);const state=await this.auth(method).getState();if(generation!==this.epoch)throw new Error('邮箱绑定方式已变化，请重新操作。');return {...state,method};});
 }
 async configure(clientId:string):Promise<MailState>{const snapshot=await this.snapshot();if(snapshot.method!=='microsoft')throw new Error('请先选择 Microsoft 365 授权登录方式。');const state=await this.outlook.configure(clientId);await this.unchanged(snapshot);return {...state,method:'microsoft'};}
 async configureImap(input:ImapConfigInput):Promise<MailState>{const snapshot=await this.snapshot();if(snapshot.method!=='imap')throw new Error('请先选择 IMAP 授权码或密码方式。');const state=await this.imap.configure(input);await this.unchanged(snapshot);return {...state,method:'imap'};}
 async connect():Promise<MailState>{const snapshot=await this.snapshot(),state=await this.auth(snapshot.method).connect();await this.unchanged(snapshot);return {...state,method:snapshot.method};}
 async disconnect():Promise<MailState>{const snapshot=await this.snapshot(),state=await this.auth(snapshot.method).disconnect();await this.unchanged(snapshot);return {...state,method:snapshot.method};}
 async saveFilter(filter:MailFilter):Promise<MailState>{const snapshot=await this.snapshot(),state=await this.auth(snapshot.method).saveFilter(filter);await this.unchanged(snapshot);return {...state,method:snapshot.method};}
 async listFolders(parentId?:string){const snapshot=await this.snapshot(),result=await this.reader(snapshot.method).listFolders(parentId);await this.unchanged(snapshot);return result;}
 async read(filter:MailFilter){const snapshot=await this.snapshot(),result=await this.reader(snapshot.method).read(filter);await this.unchanged(snapshot);return result;}
 async getMessage(id:string,options?:{maxBytes?:number}){const snapshot=await this.snapshot(),result=await this.reader(snapshot.method).getMessage(id,options);await this.unchanged(snapshot);return result;}
}
