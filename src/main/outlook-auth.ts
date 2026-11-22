import { PublicClientApplication, type Configuration, type AccountInfo, type AuthenticationResult, type InteractiveRequest, type SilentFlowRequest } from '@azure/msal-node';
import { readFileSync, writeFileSync, renameSync, existsSync } from 'node:fs';
import { join } from 'node:path';
import { z } from 'zod';
import type { KeyVault } from './settings';
import { defaultMailFilter, mailFilterSchema, type MailAccount, type MailFilter, type MailState } from '../shared/mail';

const clientSchema=z.string().trim().uuid('请输入有效的微软应用（客户端）ID');
const accountSchema=z.strictObject({id:z.string().min(1).max(2000),email:z.string().max(1000),name:z.string().max(1000)});
const storedSchema=z.strictObject({clientId:z.union([clientSchema,z.literal('')]),account:accountSchema.nullable(),filters:z.record(z.string(),mailFilterSchema),cache:z.string().max(8*1024*1024)});
type Stored=z.infer<typeof storedSchema>;
interface AuthClient {
 acquireTokenInteractive(request:InteractiveRequest):Promise<AuthenticationResult>;
 acquireTokenSilent(request:SilentFlowRequest):Promise<AuthenticationResult>;
 getTokenCache():{serialize():string;deserialize(value:string):void;getAllAccounts():Promise<AccountInfo[]>};
}
export interface OutlookAuthOptions { factory?:(config:Configuration)=>AuthClient; timeoutMs?:number }
const fresh=():Stored=>({clientId:'',account:null,filters:{},cache:''});
function publicState(value:Stored):MailState {return {clientId:value.clientId,account:value.account,filter:structuredClone(value.account ? value.filters[value.account.id]??defaultMailFilter : defaultMailFilter)};}
function accountOf(value:AccountInfo):MailAccount {return accountSchema.parse({id:value.homeAccountId,email:value.username,name:value.name??value.username});}
const scopes=['https://graph.microsoft.com/Mail.Read'];
function loginError(error:unknown):Error {
 const code=String((error as {errorCode?:unknown})?.errorCode??'');
 if (/consent|unauthorized_client|access_denied|invalid_client/.test(code)) return new Error('学校或微软未授权此应用。请核对应用ID、读取邮件权限及学校管理员审批要求。');
 if (/timeout/.test(code)) return new Error('Outlook 登录超时，请重新连接。');
 return new Error('Outlook 登录未完成，请重试；若页面要求管理员批准，请联系学校管理员。');
}

/** Tokens and all MSAL cache are encrypted locally; callers receive no credentials. */
export class OutlookAuth {
 private file:string;
 private queue:Promise<unknown>=Promise.resolve();
 private epoch=0;
 private connecting:Promise<MailState>|null=null;
 private cancel:(()=>Promise<void>)|null=null;
 constructor(directory:string,private vault:KeyVault,private openBrowser:(url:string)=>Promise<void>,private options:OutlookAuthOptions={}){this.file=join(directory,'outlook-auth.bin');}
 private async read():Promise<Stored>{
  if(!existsSync(this.file))return fresh();
  try {const decoded=await this.vault.decrypt(readFileSync(this.file));return storedSchema.parse(JSON.parse(decoded.result));}
  catch {throw new Error('Outlook 本机配置无法解密或已损坏，请备份数据目录后检查 outlook-auth.bin。');}
 }
 private async write(value:Stored){
  if(!await this.vault.available())throw new Error('系统加密不可用，未保存 Outlook 登录信息。');
  const encoded=await this.vault.encrypt(JSON.stringify(storedSchema.parse(value)));
  writeFileSync(this.file+'.tmp',encoded,{mode:0o600});renameSync(this.file+'.tmp',this.file);
 }
 private serial<T>(action:()=>Promise<T>):Promise<T>{const pending=this.queue.then(action);this.queue=pending.catch(()=>{});return pending;}
 getState():Promise<MailState>{return this.serial(async()=>publicState(await this.read()));}
 configure(clientId:string):Promise<MailState>{
  const parsed=clientSchema.parse(clientId);
  this.epoch++;const cancel=this.cancel;this.cancel=null;void cancel?.();
  return this.serial(async()=>{const state=await this.read();if(state.clientId!==parsed){state.clientId=parsed;state.account=null;state.cache='';}await this.write(state);return publicState(state);});
 }
 disconnect():Promise<MailState>{
  this.epoch++;const cancel=this.cancel;this.cancel=null;void cancel?.();
  return this.serial(async()=>{const state=await this.read();state.account=null;state.cache='';await this.write(state);return publicState(state);});
 }
 saveFilter(filter:MailFilter):Promise<MailState>{
  const valid=mailFilterSchema.parse(filter);
  return this.serial(async()=>{const state=await this.read();if(!state.account)throw new Error('请先连接学校 Outlook 邮箱。');state.filters[state.account.id]=valid;await this.write(state);return publicState(state);});
 }
 private client(state:Stored):AuthClient{
  if(!state.clientId)throw new Error('请先配置微软应用（客户端）ID，设置说明见邮箱连接面板。');
  const configuration:Configuration={auth:{clientId:state.clientId,authority:'https://login.microsoftonline.com/organizations'},system:{loggerOptions:{piiLoggingEnabled:false,loggerCallback:()=>{}},networkClient:{
   sendGetRequestAsync:async<T>(url:string,options?:{headers?:Record<string,string>})=>this.network<T>('GET',url,options),
   sendPostRequestAsync:async<T>(url:string,options?:{headers?:Record<string,string>;body?:string})=>this.network<T>('POST',url,options),
  }}};
  const app=this.options.factory?.(configuration)??new PublicClientApplication(configuration);
  if(state.cache)app.getTokenCache().deserialize(state.cache);
  return app;
 }
 private async network<T>(method:string,value:string,options?:{headers?:Record<string,string>;body?:string}){
  const url=new URL(value);if(url.protocol!=='https:'||url.hostname!=='login.microsoftonline.com'||url.username||url.password)throw new Error('拒绝未知的 Outlook 登录地址。');
  const response=await fetch(url,{method,headers:options?.headers,body:method==='POST'?options?.body:undefined,redirect:'error',signal:AbortSignal.timeout(20000)});
  const content=await response.text();if(content.length>2*1024*1024)throw new Error('Outlook 登录响应过大。');
  return {status:response.status,headers:Object.fromEntries(response.headers.entries()),body:JSON.parse(content) as T};
 }
 connect():Promise<MailState>{
  if(this.connecting)return this.connecting;
  const generation=this.epoch;
  const operation=(async()=>{
   const state=await this.serial(()=>this.read());
   if(!await this.vault.available())throw new Error('系统加密不可用，无法保存 Outlook 登录。');
   const app=this.client(state);let timer:ReturnType<typeof setTimeout>|undefined;let timedOut=false;
   try {
    const response=await app.acquireTokenInteractive({scopes,prompt:'select_account',successTemplate:'已连接 Outlook，可以关闭此页面并返回 Task Manager。',errorTemplate:'登录未完成，请返回 Task Manager 查看提示。',openBrowser:async value=>{
     if(generation!==this.epoch)throw new Error('cancelled');
     const url=new URL(value);
     if(url.protocol!=='https:'||url.hostname!=='login.microsoftonline.com'||url.username||url.password)throw new Error('unknown login url');
     const redirect=new URL(url.searchParams.get('redirect_uri')??'');
     if(redirect.protocol!=='http:'||!['localhost','127.0.0.1'].includes(redirect.hostname)||redirect.username||redirect.password)throw new Error('invalid loopback');
     const cancel=async()=>{try{await fetch(redirect,{method:'POST',headers:{'Content-Type':'application/x-www-form-urlencoded'},body:new URLSearchParams({error:'access_denied',state:url.searchParams.get('state')??''}),redirect:'error',signal:AbortSignal.timeout(2000)});}catch{/* A completed loopback may already be closed. */}};
     this.cancel=cancel;timer=setTimeout(()=>{timedOut=true;void cancel();},this.options.timeoutMs??180000);timer.unref?.();
     await this.openBrowser(url.href);
    }});
    if(!response.account)throw new Error('missing account');
    return await this.serial(async()=>{
     if(generation!==this.epoch)throw new Error('Outlook 连接已取消，请重新连接。');
     const current=await this.read();if(current.clientId!==state.clientId)throw new Error('Outlook 配置已变化，请重新连接。');
     current.account=accountOf(response.account!);current.cache=app.getTokenCache().serialize();await this.write(current);if(generation!==this.epoch)throw new Error('Outlook 连接已取消，请重新连接。');return publicState(current);
    });
   }catch(error){if(timedOut)throw new Error('Outlook 登录超时，请重新连接。');if(generation!==this.epoch)throw new Error('Outlook 连接已取消，请重新连接。');throw loginError(error);}
   finally{if(timer)clearTimeout(timer);if(generation===this.epoch)this.cancel=null;}
  })();
  this.connecting=operation;void operation.finally(()=>{if(this.connecting===operation)this.connecting=null;}).catch(()=>{});return operation;
 }
 async getToken():Promise<{token:string;account:MailAccount}>{
  const generation=this.epoch,state=await this.serial(()=>this.read());
  if(!state.account)throw new Error('请先连接学校 Outlook 邮箱。');
  const app=this.client(state);
  try {
   const account=(await app.getTokenCache().getAllAccounts()).find(account=>account.homeAccountId===state.account!.id);
   if(!account)throw new Error('missing account');
   const result=await app.acquireTokenSilent({scopes,account});
   if(!result.accessToken)throw new Error('missing token');
   await this.serial(async()=>{
    const current=await this.read();if(generation!==this.epoch||current.clientId!==state.clientId||current.account?.id!==state.account!.id)throw new Error('changed account');
    current.cache=app.getTokenCache().serialize();await this.write(current);
   });
   if(generation!==this.epoch)throw new Error('changed account');
   return {token:result.accessToken,account:structuredClone(state.account)};
  }catch{throw new Error('Outlook 授权已失效或暂时无法刷新，请重新连接邮箱。');}
 }
}
