import { readFileSync,writeFileSync,renameSync,existsSync } from 'node:fs';
import { join } from 'node:path';
import { createHash } from 'node:crypto';
import { domainToASCII } from 'node:url';
import { isIP } from 'node:net';
import { z } from 'zod';
import { imapConfigSchema,mailFilterSchema,defaultMailFilter,type ImapConfigInput,type MailAccount,type MailFilter,type MailState } from '../shared/mail';
import type { KeyVault } from './settings';

const storedSchema=z.strictObject({config:imapConfigSchema.nullable(),connected:z.boolean(),filters:z.record(z.string(),mailFilterSchema)}).refine(value=>!value.connected||!!value.config?.password,'连接配置缺失');
type Stored=z.infer<typeof storedSchema>;
export interface ImapCredentials { config:ImapConfigInput; account:MailAccount }
function normalize(input:ImapConfigInput):ImapConfigInput {
 const value=imapConfigSchema.parse(input),raw=value.host.replace(/\.$/,'');
 const host=isIP(raw)?raw.toLowerCase():domainToASCII(raw).toLowerCase();
 if(!host||(!isIP(host)&&!host.split('.').every(label=>/^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$/i.test(label))))throw new Error('请输入有效的 IMAP 服务器主机名或IP地址。');
 return {...value,host,username:value.username||value.email};
}
function account(config:ImapConfigInput):MailAccount {return {id:'imap:'+createHash('sha256').update(JSON.stringify([config.host,config.port,config.security,config.username])).digest('hex'),email:config.email,name:config.email};}
function publicState(value:Stored):MailState {
 const config=value.config,identity=config?account(config):null;
 return {method:'imap',clientId:'',account:value.connected?identity:null,
  filter:structuredClone(identity?value.filters[identity.id]??defaultMailFilter:defaultMailFilter),
  ...(config?{imap:{email:config.email,username:config.username,host:config.host,port:config.port,security:config.security,hasSecret:!!config.password}}:{})};
}
export class ImapAuth {
 private file:string;
 private queue:Promise<unknown>=Promise.resolve();
 private epoch=0;
 private connecting:Promise<MailState>|null=null;
 constructor(directory:string,private vault:KeyVault,private testConnection:(credentials:ImapCredentials)=>Promise<void>){this.file=join(directory,'imap-auth.bin');}
 private serial<T>(action:()=>Promise<T>):Promise<T>{const next=this.queue.then(action);this.queue=next.catch(()=>{});return next;}
 private async read():Promise<Stored>{
  if(!existsSync(this.file))return {config:null,connected:false,filters:{}};
  try{return storedSchema.parse(JSON.parse((await this.vault.decrypt(readFileSync(this.file))).result));}
  catch{throw new Error('IMAP 本机配置无法解密或已损坏，请先备份 data 并检查 imap-auth.bin。');}
 }
 private async write(value:Stored){
  if(!await this.vault.available())throw new Error('系统加密不可用，未保存 IMAP 登录信息。');
  const encoded=await this.vault.encrypt(JSON.stringify(storedSchema.parse(value)));
  writeFileSync(this.file+'.tmp',encoded,{mode:0o600});renameSync(this.file+'.tmp',this.file);
 }
 getState():Promise<MailState>{return this.serial(async()=>publicState(await this.read()));}
 configure(input:ImapConfigInput):Promise<MailState>{
  const config=normalize(input);this.epoch++;this.connecting=null;
  return this.serial(async()=>{
   const state=await this.read(),old=state.config;
   const same=old&&old.host===config.host&&old.port===config.port&&old.security===config.security&&old.email===config.email&&old.username===config.username;
   if(!config.password){if(!same||!old.password)throw new Error('请填写此连接的授权码或密码，修改服务器或账户后需要重新填写。');config.password=old.password;}
   state.config=config;state.connected=false;await this.write(state);return publicState(state);
  });
 }
 connect():Promise<MailState>{
  if(this.connecting)return this.connecting;
  const generation=this.epoch;
  const pending=(async()=>{
   const snapshot=await this.serial(()=>this.read());
   if(!snapshot.config?.password)throw new Error('请先保存 IMAP 服务器、账户及授权码或密码。');
   const credentials={config:structuredClone(snapshot.config),account:account(snapshot.config)};
   try{await this.testConnection(credentials);}catch{throw new Error('IMAP 连接失败，请检查服务器、端口、加密方式及授权码；邮箱须允许此认证方式。');}
   return this.serial(async()=>{
    if(generation!==this.epoch)throw new Error('IMAP 连接设置已变化，请重新连接。');
    const state=await this.read();if(JSON.stringify(state.config)!==JSON.stringify(snapshot.config))throw new Error('IMAP 连接设置已变化，请重新连接。');
    state.connected=true;await this.write(state);if(generation!==this.epoch)throw new Error('IMAP 连接已取消。');return publicState(state);
   });
  })();
  this.connecting=pending;void pending.finally(()=>{if(this.connecting===pending)this.connecting=null;}).catch(()=>{});return pending;
 }
 disconnect():Promise<MailState>{
  this.epoch++;this.connecting=null;
  return this.serial(async()=>{const state=await this.read();state.connected=false;if(state.config)state.config.password='';await this.write(state);return publicState(state);});
 }
 saveFilter(input:MailFilter):Promise<MailState>{
  const filter=mailFilterSchema.parse(input);
  return this.serial(async()=>{const state=await this.read();if(!state.connected||!state.config)throw new Error('请先连接 IMAP 邮箱。');state.filters[account(state.config).id]=filter;await this.write(state);return publicState(state);});
 }
 getCredentials():Promise<ImapCredentials>{return this.serial(async()=>{const state=await this.read();if(!state.connected||!state.config?.password)throw new Error('请先连接 IMAP 邮箱。');return {config:structuredClone(state.config),account:account(state.config)};});}
}
