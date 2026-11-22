import { contextBridge, ipcRenderer } from 'electron';
import type { WidgetAPI } from '../shared/widget';
import type { MailAPI } from '../shared/mail';
import type { DesktopAPI } from '../shared/contracts';
const invoke=(name:keyof DesktopAPI,...args:unknown[])=>ipcRenderer.invoke('backend:'+name,...args);
const api:DesktopAPI={
  getCanvasSource:id=>invoke('getCanvasSource',id), openCanvasOriginal:id=>invoke('openCanvasOriginal',id),
  getCanvasConfig:()=>invoke('getCanvasConfig'), saveCanvasFeed:url=>invoke('saveCanvasFeed',url),
  clearCanvasFeed:()=>invoke('clearCanvasFeed'), syncCanvas:()=>invoke('syncCanvas'),
  getDayOrder:day=>invoke('getDayOrder',day), setDayOrder:(day,ids)=>invoke('setDayOrder',day,ids),
  listTodos:include=>invoke('listTodos',include), effectiveDue:id=>invoke('effectiveDue',id),
  editTodo:(id,patch,completed,rev)=>invoke('editTodo',id,patch,completed,rev),
  createTodo:value=>invoke('createTodo',value), updateTodo:(id,patch,rev)=>invoke('updateTodo',id,patch,rev),
  moveTodo:(id,parent,rev)=>invoke('moveTodo',id,parent,rev), setCompleted:(id,value,rev)=>invoke('setCompleted',id,value,rev),
  trashTodo:(id,rev)=>invoke('trashTodo',id,rev), restoreBatch:id=>invoke('restoreBatch',id),
  saveDrafts:(drafts,token)=>invoke('saveDrafts',drafts,token),
  getCompletionConfig:()=>invoke('getCompletionConfig'),setCompletionConfig:value=>invoke('setCompletionConfig',value),
  getSettings:()=>invoke('getSettings'), saveSettings:value=>invoke('saveSettings',value),clearKey:()=>invoke('clearKey'),
  importText:()=>invoke('importText'),extract:text=>invoke('extract',text),exportBackup:()=>invoke('exportBackup'),importBackup:()=>invoke('importBackup'),
};
contextBridge.exposeInMainWorld('desktop',Object.freeze(api));

const widget: WidgetAPI = {
  getState: () => ipcRenderer.invoke('widget:getState'),
  setEnabled: enabled => ipcRenderer.invoke('widget:setEnabled', enabled),
  setExpanded: expanded => ipcRenderer.invoke('widget:setExpanded', expanded),
  setPointerPassthrough: ignore => ipcRenderer.invoke('widget:setPointerPassthrough',ignore),
  moveBy: (dx, dy) => ipcRenderer.invoke('widget:moveBy', dx, dy),
  minimizeToTray: () => ipcRenderer.invoke('widget:minimizeToTray'),
  close: () => ipcRenderer.invoke('widget:close'),
  showMain: () => ipcRenderer.invoke('widget:showMain'),
  quit: () => ipcRenderer.invoke('widget:quit'),
  onChanged: callback => {
    const listener = () => callback();
    ipcRenderer.on('desktop:changed', listener);
    return () => { ipcRenderer.removeListener('desktop:changed', listener); };
  },
};
contextBridge.exposeInMainWorld('widget', Object.freeze(widget));

const mailInvoke=(name:keyof MailAPI,...args:unknown[])=>ipcRenderer.invoke('mail:'+name,...args);
const mail:MailAPI={
  getState:()=>mailInvoke('getState'),selectMethod:method=>mailInvoke('selectMethod',method),configureImap:input=>mailInvoke('configureImap',input),configure:id=>mailInvoke('configure',id),connect:()=>mailInvoke('connect'),disconnect:()=>mailInvoke('disconnect'),
  listFolders:parent=>mailInvoke('listFolders',parent),saveFilter:filter=>mailInvoke('saveFilter',filter),read:filter=>mailInvoke('read',filter),
  getMessage:id=>mailInvoke('getMessage',id),openMessage:id=>mailInvoke('openMessage',id),extract:(id,target)=>mailInvoke('extract',id,target),
};
contextBridge.exposeInMainWorld('mail',Object.freeze(mail));

import type { PreferencesAPI } from '../shared/preferences';
const preferences:PreferencesAPI={get:()=>ipcRenderer.invoke('preferences:get'),save:value=>ipcRenderer.invoke('preferences:save',value)};
contextBridge.exposeInMainWorld('preferences',Object.freeze(preferences));

const workspaceInvoke=(name:keyof import('../shared/mail-workspace').MailWorkspaceAPI,...args:unknown[])=>ipcRenderer.invoke('mail-workspace:'+name,...args);
const workspace:import('../shared/mail-workspace').MailWorkspaceAPI={
 listPresets:id=>workspaceInvoke('listPresets',id),savePreset:(id,input)=>workspaceInvoke('savePreset',id,input),deletePreset:id=>workspaceInvoke('deletePreset',id),knownSenders:id=>workspaceInvoke('knownSenders',id),
 listFoldersLocal:()=>workspaceInvoke('listFoldersLocal'),createLocalFolder:name=>workspaceInvoke('createLocalFolder',name),renameLocalFolder:(id,name)=>workspaceInvoke('renameLocalFolder',id,name),deleteLocalFolder:id=>workspaceInvoke('deleteLocalFolder',id),moveLocal:(ids,folder)=>workspaceInvoke('moveLocal',ids,folder),deleteLocal:ids=>workspaceInvoke('deleteLocal',ids),
 getAccounts:()=>workspaceInvoke('getAccounts'),addAccount:method=>workspaceInvoke('addAccount',method),removeAccount:id=>workspaceInvoke('removeAccount',id),getAccount:id=>workspaceInvoke('getAccount',id),
 configureImap:(id,input)=>workspaceInvoke('configureImap',id,input),configureMicrosoft:(id,clientId)=>workspaceInvoke('configureMicrosoft',id,clientId),
 connect:id=>workspaceInvoke('connect',id),disconnect:id=>workspaceInvoke('disconnect',id),listFolders:(id,parent)=>workspaceInvoke('listFolders',id,parent),
 saveFilter:(id,filter)=>workspaceInvoke('saveFilter',id,filter),importMail:input=>workspaceInvoke('importMail',input),
 listLocal:query=>workspaceInvoke('listLocal',query),getLocal:id=>workspaceInvoke('getLocal',id),openOriginal:id=>workspaceInvoke('openOriginal',id),
 analyze:input=>workspaceInvoke('analyze',input),getAnalysis:id=>workspaceInvoke('getAnalysis',id),saveAnalysis:(id,drafts)=>workspaceInvoke('saveAnalysis',id,drafts),
};
contextBridge.exposeInMainWorld('mailWorkspace',Object.freeze(workspace));

const operationsInvoke=(name:keyof import('../shared/mail-operations').MailOperationsAPI,...args:unknown[])=>ipcRenderer.invoke('mail-operations:'+name,...args);
const operations:import('../shared/mail-operations').MailOperationsAPI={
 startInboxSync:()=>operationsInvoke('startInboxSync'),
 startFileTask:input=>operationsInvoke('startFileTask',input),
 acknowledgeJob:id=>operationsInvoke('acknowledgeJob',id),
 listJobs:()=>operationsInvoke('listJobs'),getJob:id=>operationsInvoke('getJob',id),startImport:input=>operationsInvoke('startImport',input),startAnalysis:input=>operationsInvoke('startAnalysis',input),
 pauseJob:id=>operationsInvoke('pauseJob',id),resumeJob:id=>operationsInvoke('resumeJob',id),cancelJob:id=>operationsInvoke('cancelJob',id),
 retryImportFailures:input=>operationsInvoke('retryImportFailures',input),retryBodies:input=>operationsInvoke('retryBodies',input),
 listConversations:()=>operationsInvoke('listConversations'),getConversation:id=>operationsInvoke('getConversation',id),
 continueConversation:(id,text,target)=>operationsInvoke('continueConversation',id,text,target),deleteConversation:id=>operationsInvoke('deleteConversation',id),startConversation:(text,target)=>operationsInvoke('startConversation',text,target),
};
contextBridge.exposeInMainWorld('mailOperations',Object.freeze(operations));

const composeInvoke=(name:keyof import('../shared/mail-compose').MailComposeAPI,...args:unknown[])=>ipcRenderer.invoke('mail-compose:'+name,...args);
const compose:import('../shared/mail-compose').MailComposeAPI={
 getConfig:id=>composeInvoke('getConfig',id),saveConfig:(id,input)=>composeInvoke('saveConfig',id,input),
 generate:input=>composeInvoke('generate',input),prepare:input=>composeInvoke('prepare',input),
 send:id=>composeInvoke('send',id),listRecent:()=>composeInvoke('listRecent'),
};
contextBridge.exposeInMainWorld('mailCompose',Object.freeze(compose));

const automationInvoke=(name:keyof import('../shared/mail-automation').MailAutomationAPI,...args:unknown[])=>ipcRenderer.invoke('mail-automation:'+name,...args);
const automation:import('../shared/mail-automation').MailAutomationAPI={
 getConfig:()=>automationInvoke('getConfig'),saveConfig:value=>automationInvoke('saveConfig',value),
 getStatus:()=>automationInvoke('getStatus'),runNow:()=>automationInvoke('runNow'),
 listResults:query=>automationInvoke('listResults',query),correctCategory:(id,category)=>automationInvoke('correctCategory',id,category),
 getBackgroundStatus:()=>automationInvoke('getBackgroundStatus'),
};
contextBridge.exposeInMainWorld('mailAutomation',Object.freeze(automation));

const filesInvoke=(name:keyof import('../shared/mail-files').MailFilesAPI,...args:unknown[])=>ipcRenderer.invoke('mail-files:'+name,...args);
const files:import('../shared/mail-files').MailFilesAPI={
 importLocal:()=>filesInvoke('importLocal'), importAttachment:(localId,index)=>filesInvoke('importAttachment',localId,index),
 list:()=>filesInvoke('list'),get:id=>filesInvoke('get',id),read:id=>filesInvoke('read',id),export:id=>filesInvoke('export',id),
 inspect:(id,offset=0,limit=30)=>filesInvoke('inspect',id,offset,limit),preview:(id,page=1)=>filesInvoke('preview',id,page),validate:id=>filesInvoke('validate',id),
};
contextBridge.exposeInMainWorld('mailFiles',Object.freeze(files));

const agentInvoke=(name:keyof import('../shared/agent').AgentAPI,...args:unknown[])=>ipcRenderer.invoke('agent:'+name,...args);
const agent:import('../shared/agent').AgentAPI={
 getSecretary:()=>agentInvoke('getSecretary'),createChild:input=>agentInvoke('createChild',input),getBriefing:id=>agentInvoke('getBriefing',id),
 listConversations:()=>agentInvoke('listConversations'),getConversation:id=>agentInvoke('getConversation',id),
 startTurn:input=>agentInvoke('startTurn',input),getRun:id=>agentInvoke('getRun',id),
 resumeRun:input=>agentInvoke('resumeRun',input),pauseRun:id=>agentInvoke('pauseRun',id),cancelRun:id=>agentInvoke('cancelRun',id),
 deleteConversation:id=>agentInvoke('deleteConversation',id),getMailDraft:id=>agentInvoke('getMailDraft',id),listPending:()=>agentInvoke('listPending'),
 resolveMailDraft:input=>agentInvoke('resolveMailDraft',input),
};
contextBridge.exposeInMainWorld('agent',Object.freeze(agent));

const learning:import('../shared/agent').LearningAPI={
 list:query=>ipcRenderer.invoke('learning:list',query??{}),get:id=>ipcRenderer.invoke('learning:get',id),
 approve:input=>ipcRenderer.invoke('learning:approve',input),disable:input=>ipcRenderer.invoke('learning:disable',input),
};
const taskChanges:import('../shared/agent').TaskChangesAPI={
 list:()=>ipcRenderer.invoke('task-changes:list'),get:id=>ipcRenderer.invoke('task-changes:get',id),
 approve:input=>ipcRenderer.invoke('task-changes:approve',input),reject:input=>ipcRenderer.invoke('task-changes:reject',input),
};
contextBridge.exposeInMainWorld('learning',Object.freeze(learning));
contextBridge.exposeInMainWorld('taskChanges',Object.freeze(taskChanges));

const skills:import('../shared/skills').SkillsAPI={
 list:query=>ipcRenderer.invoke('skill:list',query??{}),get:id=>ipcRenderer.invoke('skill:get',id),
 importPackage:input=>ipcRenderer.invoke('skill:importPackage',input),
 approve:input=>ipcRenderer.invoke('skill:approve',input),disable:input=>ipcRenderer.invoke('skill:disable',input),
};
contextBridge.exposeInMainWorld('skills',Object.freeze(skills));
