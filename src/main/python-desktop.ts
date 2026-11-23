import type { BrowserWindow, IpcMain } from 'electron';
import { randomUUID } from 'node:crypto';
import { open, rename, unlink, writeFile } from 'node:fs/promises';
import { dirname, join } from 'node:path';
import { pathToFileURL } from 'node:url';
import { PythonBackend } from './python-backend';
import { pythonChannels } from './python-channels';
import { mutationMethods, rendererAllowed } from './widget-policy';
import type { Result, DesktopAPI } from '../shared/contracts';
import type { AgentConversation, AgentConversationExportInput } from '../shared/agent';
import { conversationFilename, conversationMarkdown } from '../shared/conversation-export';
import type { AutomationHost } from './mail-automation-host';

interface Backend {call(method: string, ...args: unknown[]): Promise<Result<unknown>>}

export interface PythonDesktopPorts {chooseFiles?():Promise<string[]|null>;saveFile?(name:string):Promise<string|null>;automation?:AutomationHost;runtime?:{python:string;backendDirectory:string}; notifyJob?(job:{id:string;kind:string;label:string},acknowledge:()=>void):void;chooseBackup?(): Promise<string | null>; saveBackup?(): Promise<string | null>; chooseText?(): Promise<string | null>; openExternal?(url:string):Promise<void>; decryptLegacy?(cipher:Buffer):Promise<string>}

function isConversationExportInput(value:unknown):value is AgentConversationExportInput {
  if (!value || typeof value!=='object' || Reflect.ownKeys(value).length!==2 ||
      !Object.hasOwn(value,'id') || !Object.hasOwn(value,'includeSources')) return false;
  const {id,includeSources}=value as Partial<AgentConversationExportInput>;
  return typeof id==='string' && id.length===36 && /^[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}$/i.test(id) && typeof includeSources==='boolean';
}

async function writeConversationExport(path:string,markdown:string):Promise<void> {
  const temporary=join(dirname(path),`.tm-conversation-${randomUUID()}.tmp`);
  const file=await open(temporary,'wx',0o600);
  try {
    try {
      await writeFile(file,markdown,'utf8');
      await file.sync();
    } finally {
      await file.close();
    }
    await rename(temporary,path);
  } catch(error) {
    await unlink(temporary).catch(()=>{});
    throw error;
  }
}

async function exportConversation(backend:Backend,args:unknown[],saveFile:PythonDesktopPorts['saveFile']):Promise<Result<boolean>> {
  const [input]=args;
  if (args.length!==1 || !isConversationExportInput(input)) return {ok:false,error:'无效的对话导出参数。'};
  if (!saveFile) return {ok:false,error:'当前环境无法打开保存窗口。'};
  try {
    const response=await backend.call('agent:getConversation',input.id);
    const conversation=response.ok?response.data as AgentConversation:undefined;
    if (conversation?.id!==input.id) return {ok:false,error:'无法读取这段对话，请重试。'};
    const markdown=conversationMarkdown(conversation,input.includeSources);
    if (!markdown) return {ok:false,error:'这段对话没有可导出的内容。'};
    const path=await saveFile(conversationFilename(conversation.title));
    if (!path) return {ok:true,data:false};
    await writeConversationExport(path,markdown);
    return {ok:true,data:true};
  } catch {
    return {ok:false,error:'保存对话失败，请重试。'};
  }
}

export async function migratePythonCredentials(backend:Backend, decrypt:(cipher:Buffer)=>Promise<string>):Promise<Result<unknown>> {
  const response=await backend.call('_legacySecrets');
  if (!response.ok) return response;
  for (const entry of response.data as {kind:string;encrypted:string}[]) {
    try {
      const plain=await decrypt(Buffer.from(entry.encrypted,'base64'));
      const result=await backend.call('_migrateLegacySecret',entry.kind,entry.encrypted,plain);
      if (!result.ok) return result;
    } catch {return {ok:false,error:'旧凭据无法迁移，请在当前设备重新保存或授权。'};}
  }
  return {ok:true,data:null};
}

export async function dispatchPython(backend: Backend, trusted: boolean, channel: string, args: unknown[], changed: () => void, ports: PythonDesktopPorts = {}): Promise<Result<unknown>> {
  if (!trusted) return {ok: false, error: '拒绝未知页面请求。'};
  if (!pythonChannels.includes(channel)) return {ok:false,error:'拒绝未知接口。'};
  const method = channel.startsWith('backend:') ? channel.slice('backend:'.length) : channel;
  if (method==='agent:exportConversation') return exportConversation(backend,args,ports.saveFile);
  let result: Result<unknown>;
  try {
    if (method === 'mail-files:importLocal') {
      if (args.length) return {ok:false,error:'文件路径只能由桌面对话框选择。'};
      if (!ports.chooseFiles) return {ok:false,error:'当前环境无法打开文件选择窗口。'};
      const paths=await ports.chooseFiles();
      if (!paths?.length) return {ok:true,data:null};
      if (paths.length>6) return {ok:false,error:'每次最多导入 6 份资料。'};
      const files:unknown[]=[];
      for (const path of paths) {
        const imported=await backend.call('mail-files:importLocalFile',path);
        if (!imported.ok) {if(files.length) changed(); return {ok:false,error:imported.error+(files.length?' 已成功导入的文件保留在文件列表中。':'')};}
        files.push(imported.data);
      }
      result={ok:true,data:files};
    } else if (method === 'mail-files:export') {
      if(args.length!==1 || typeof args[0]!=='string') return {ok:false,error:'请选择一个文件。'};
      if(!ports.saveFile) return {ok:false,error:'当前环境无法打开保存窗口。'};
      const record=await backend.call('mail-files:get',args[0]);
      if(!record.ok) return record;
      const path=await ports.saveFile((record.data as {name:string}).name);
      if(!path) return {ok:true,data:false};
      result=await backend.call('mail-files:exportFile',args[0],path);
    } else if (method === 'mail-automation:getBackgroundStatus') {
      if (args.length) return {ok:false,error:'后台状态查询不接受参数。'};
      return {ok:true,data:ports.automation ? await ports.automation.status() : {supported:false,installed:false,message:'此环境没有系统定时后台。'}};
    } else if (method === 'mail-automation:saveConfig') {
      const input = args[0] as Record<string,unknown> | undefined;
      if (input?.enabled && !ports.automation) return {ok:false,error:'此环境没有系统定时后台。'};
      result = await backend.call(method,...args);
      if (result.ok && ports.automation) {
        try {
          if ((result.data as {enabled:boolean}).enabled) await ports.automation.enable();
          else await ports.automation.disable();
        } catch {
          const rollback = await backend.call(method,{...input,enabled:false});
          try {await ports.automation.disable();} catch { /* The persisted switch still prevents new work when rollback succeeded. */ }
          return {ok:false,error:rollback.ok
            ? '系统后台未能应用设置，定时整理已停用；请重新打开设置检查后台状态。'
            : '系统后台未能应用设置，且停用未能确认；请重新打开设置检查开关与后台状态。'};
        }
      }
    } else if (method === 'mail-automation:runNow') {
      if (!ports.automation) return {ok:false,error:'当前环境无法启动独立整理进程。'};
      result = await backend.call(method,...args);
      if (result.ok) {
        try {await ports.automation.startOnce();}
        catch {return {ok:false,error:'整理请求已保存，但后台未能启动；请检查后台状态后重试。'};}
      }
    } else if (method === 'importBackup' || method === 'exportBackup' || method === 'importText') {
      if (args.length) return {ok:false,error:'文件路径只能由桌面对话框选择。'};
      const choose = method === 'importBackup' ? ports.chooseBackup : method === 'exportBackup' ? ports.saveBackup : ports.chooseText;
      if (!choose) return {ok:false,error:'当前环境无法打开文件选择窗口。'};
      const path = await choose();
      if (!path) return {ok:true,data:method === 'importText' ? null : false};
      result = await backend.call(method === 'importBackup' ? 'importBackupFile' : method === 'exportBackup' ? 'exportBackupFile' : 'importTextFile', path);
    } else if (method === 'openCanvasOriginal') {
      const source = await backend.call('getCanvasSource', ...args);
      if (!source.ok) return source;
      const url = new URL((source.data as {url:string} | null)?.url ?? '');
      if (!['http:','https:'].includes(url.protocol) || url.username || url.password || !ports.openExternal) return {ok:false,error:'无法打开Canvas原文链接。'};
      await ports.openExternal(url.href); return {ok:true,data:null};
    } else result = await backend.call(method, ...args);
  } catch {return {ok:false,error:'文件操作或打开外部链接失败，请重试。'};}
  if (result.ok && (mutationMethods.has(method as keyof DesktopAPI) || (channel.startsWith('agent:') || channel.startsWith('mail-workspace:') || channel.startsWith('mail-operations:') || channel.startsWith('mail-compose:') || channel.startsWith('mail-automation:') || channel.startsWith('mail-files:') || channel.startsWith('learning:') || channel.startsWith('skill:') || channel.startsWith('task-changes:')) && !/:(get|list|known|open)/.test(channel))) changed();
  return result;
}

export function attachPythonDesktop(ipc: IpcMain, getWindows: () => BrowserWindow[], directory: string, rendererFile: string, ports: PythonDesktopPorts = {}) {
  const bridge = new PythonBackend(directory,ports.runtime);
  const prepared=ports.decryptLegacy ? migratePythonCredentials(bridge, ports.decryptLegacy).catch(()=>({ok:false as const,error:'旧凭据迁移失败，请重新保存。'})) : Promise.resolve();
  if (ports.automation) void prepared.then(async () => {
    const config = await bridge.call('mail-automation:getConfig');
    if (config.ok && (config.data as {enabled:boolean}).enabled) await ports.automation!.enable();
  }).catch(() => { /* The status panel reports an unloaded background agent. */ });
  const allowed = pathToFileURL(rendererFile).href;
  const broadcast = () => getWindows().forEach(window => {if (!window.isDestroyed()) window.webContents.send('desktop:changed');});
  const notified=new Set<string>();
  let lastJobs='';
  for (const channel of pythonChannels) {
    ipc.handle(channel, async (event, ...args) => {
      const source = getWindows().find(window => !window.isDestroyed() && event.sender === window.webContents);
      const trusted = !!source && event.senderFrame === source.webContents.mainFrame && rendererAllowed(event.senderFrame.url, allowed);
      if (trusted) await prepared;
      const result=await dispatchPython(bridge, trusted, channel, args, broadcast, ports);
      if (trusted && channel==='mail-operations:listJobs' && result.ok) {
        const jobs=result.data as {id:string;kind:string;label:string;state:string;notificationAcknowledged?:boolean}[];
        const snapshot=JSON.stringify(jobs);
        if (lastJobs && lastJobs!==snapshot) broadcast();
        lastJobs=snapshot;
        for (const job of jobs) if (['completed','failed','canceled'].includes(job.state) && !job.notificationAcknowledged && !notified.has(job.id)) {
          notified.add(job.id);
          ports.notifyJob?.(job,()=>{void bridge.call('mail-operations:acknowledgeJob',job.id).then(()=>broadcast());});
        }
      }
      return result;
    });
  }
  return {broadcast, dispose: async () => {
    for (const channel of pythonChannels) ipc.removeHandler(channel);
    await bridge.dispose();
  }};
}
