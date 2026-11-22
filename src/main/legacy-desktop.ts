import { attachPythonDesktop } from './python-desktop';
// 桌面数据与窗口入口。
import { app, BrowserWindow, ipcMain, dialog, safeStorage, screen, shell, Notification } from 'electron';
import { join, resolve } from 'node:path';
import { pathToFileURL } from 'node:url';
import { readFileSync, statSync, writeFileSync, renameSync, mkdirSync } from 'node:fs';
import { TaskRepository } from './repository';
import { loadWindowPage } from './window-page';
import { logWindowEvent, setWindowLogDirectory } from './window-log';
import { SettingsStore } from './settings';
import { resolveDataDirectory, prepareDataDirectory } from './storage';
import { createBackendService, MAX_BACKUP_BYTES } from './service';
import { createAppTray } from './tray';
import { ApplicationPreferencesStore } from './preferences';
import { CanvasFeedStore } from './canvas';
import { OutlookAuth } from './outlook-auth';
import { OutlookGraph } from './outlook-graph';
import { ImapAuth } from './imap-auth';
import { ImapMailbox } from './imap-mailbox';
import { MailConnections } from './mail-connections';
import { createMailService } from './mail-service';
import { MailAccountManager, MailWorkspaceStore, createMailWorkspaceBase } from './mail-workspace';
import { createMailAnalysisService } from './mail-analysis-service';
import { MailOperations } from './mail-operations';
import type { MailAPI } from '../shared/mail';
import type { DesktopAPI } from '../shared/contracts';
import { mutationMethods, rendererAllowed, widgetBounds, moveWidgetBounds, widgetWindowStyle } from './widget-policy';
export function attachDesktopBackend(getWindows: () => BrowserWindow[], directory: string, rendererFile: string) {
  if (!app.isReady()) throw new Error('桌面适配器须在 app ready 后连接。');
  const repository=new TaskRepository(join(directory,'tasks.sqlite'));
  const vault = { available:()=>safeStorage.isAsyncEncryptionAvailable(), encrypt:(value:string)=>safeStorage.encryptStringAsync(value), decrypt:(value:Buffer)=>safeStorage.decryptStringAsync(value) };
  const settings=new SettingsStore(directory,vault);
  const canvas=new CanvasFeedStore(directory,vault);
  const parent = () => BrowserWindow.getFocusedWindow() ?? getWindows()[0];
  const broadcast = () => getWindows().forEach(window => { if (!window.isDestroyed()) window.webContents.send('desktop:changed'); });
  const api=createBackendService(repository,settings,{
    openExternal: url => shell.openExternal(url),
    chooseText:async () => {
      const result=await dialog.showOpenDialog(parent(),{properties:['openFile'],filters:[{name:'邮件或文字',extensions:['eml','txt']}]});
      if (result.canceled) return null;
      const path=result.filePaths[0]; if (statSync(path).size>2*1024*1024) throw new Error('文件超过2MB。');
      return {path,content:readFileSync(path)};
    },
    writeBackup:async text => {
      const backups=join(directory,'backups'); mkdirSync(backups,{recursive:true});
      const result=await dialog.showSaveDialog(parent(),{defaultPath:join(backups,'todo-backup-'+new Date().toISOString().replace(/[:.]/g,'-')+'.json'),filters:[{name:'备份',extensions:['json']}]});
      if (result.canceled || !result.filePath) return false;
      writeFileSync(result.filePath+'.tmp',text,'utf8'); renameSync(result.filePath+'.tmp',result.filePath); return true;
    },
    chooseBackup:async () => {
      const result=await dialog.showOpenDialog(parent(),{properties:['openFile'],filters:[{name:'待办备份',extensions:['json']}]});
      if (result.canceled) return null; const path=result.filePaths[0];
      if (statSync(path).size>MAX_BACKUP_BYTES) throw new Error('备份超过64MB。'); return readFileSync(path);
    },
  },fetch,canvas);
  const outlook=new OutlookAuth(directory,vault,url=>shell.openExternal(url));
  const imap=new ImapAuth(directory,vault,credentials=>new ImapMailbox(async()=>credentials).testConnection());
  const connections=new MailConnections(directory,outlook,new OutlookGraph(outlook),imap,new ImapMailbox(()=>imap.getCredentials()));
  const mail=createMailService(repository,settings,connections,connections,url=>shell.openExternal(url));
  const mailStore=new MailWorkspaceStore(directory);
  const mailManager=new MailAccountManager(directory,vault,url=>shell.openExternal(url));
  const workspace=createMailAnalysisService(createMailWorkspaceBase(mailManager,mailStore,url=>shell.openExternal(url),token=>repository.hasDraftConfirmation(token)),mailStore,repository,settings);
  const operations=new MailOperations(directory,mailManager,mailStore,repository,settings,broadcast,job=>{
    if(Notification?.isSupported()){const notice=new Notification({title:'Task Manager',body:job.kind==='import'?'邮件导入已完成，返回应用查看。':'AI处理已完成，返回应用查看对话与结果。'});notice.on('close',()=>{void operations.api.acknowledgeJob(job.id);});notice.on('click',()=>{void operations.api.acknowledgeJob(job.id);const target=getWindows()[0];if(target&&!target.isDestroyed()){if(target.isMinimized())target.restore();target.show();target.focus();}});notice.show();}
  });
  const allowed=pathToFileURL(rendererFile).href;
  const operationMethods=Object.keys(operations.api) as (keyof typeof operations.api)[];
  for(const name of operationMethods)ipcMain.handle('mail-operations:'+name,async(event,...args)=>{
    const source=getWindows().find(window=>!window.isDestroyed()&&event.sender===window.webContents);
    if(!source||event.senderFrame!==source.webContents.mainFrame||!rendererAllowed(event.senderFrame.url,allowed))return {ok:false,error:'拒绝未知页面请求。'};
    return (operations.api[name] as (...values:any[])=>Promise<{ok:boolean}>)(...args);
  });
  const workspaceMethods=Object.keys(workspace) as (keyof typeof workspace)[];
  for(const name of workspaceMethods)ipcMain.handle('mail-workspace:'+name,async(event,...args)=>{
    const source=getWindows().find(window=>!window.isDestroyed()&&event.sender===window.webContents);
    if(!source||event.senderFrame!==source.webContents.mainFrame||!rendererAllowed(event.senderFrame.url,allowed))return {ok:false,error:'拒绝未知页面请求。'};
    const result=await (workspace[name] as (...values:any[])=>Promise<{ok:boolean}>)(...args);
    if(result.ok&&['addAccount','removeAccount','configureImap','configureMicrosoft','connect','disconnect','saveFilter','importMail','analyze','saveAnalysis','savePreset','deletePreset','createLocalFolder','renameLocalFolder','deleteLocalFolder','moveLocal','deleteLocal'].includes(name))broadcast();
    return result;
  });
  const mailMethods=Object.keys(mail) as (keyof MailAPI)[];
  for(const name of mailMethods) ipcMain.handle('mail:'+name,async(event,...args)=>{
    const source=getWindows().find(window=>!window.isDestroyed()&&event.sender===window.webContents);
    if(!source||event.senderFrame!==source.webContents.mainFrame||!rendererAllowed(event.senderFrame.url,allowed))return {ok:false,error:'拒绝未知页面请求。'};
    const result=await (mail[name] as (...values:any[])=>Promise<{ok:boolean}>)(...args);
    if(result.ok&&['selectMethod','configureImap','configure','connect','disconnect','saveFilter'].includes(name))broadcast();
    return result;
  });
  const methods=Object.keys(api) as (keyof DesktopAPI)[];
  for (const name of methods) ipcMain.handle('backend:'+name,async (event,...args) => {
    const source = getWindows().find(window => !window.isDestroyed() && event.sender === window.webContents);
    if (!source || event.senderFrame !== source.webContents.mainFrame || !rendererAllowed(event.senderFrame.url, allowed)) return {ok:false,error:'拒绝未知页面请求。'};
    const result = await (api[name] as (...values:any[])=>Promise<{ok:boolean}>)(...args);
    if (result.ok && mutationMethods.has(name)) broadcast();
    return result;
  });
  const dispose=() => { operations.close();operationMethods.forEach(name=>ipcMain.removeHandler('mail-operations:'+name)); workspaceMethods.forEach(name=>ipcMain.removeHandler('mail-workspace:'+name)); mailStore.close(); mailMethods.forEach(name=>ipcMain.removeHandler('mail:'+name)); methods.forEach(name=>ipcMain.removeHandler('backend:'+name)); repository.close(); };
  return {api,dispose,broadcast};
}
