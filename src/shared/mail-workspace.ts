import type { Result, Settings } from './contracts';
import type { TodoDraft } from './todo';
import type { MailMethod, MailState, MailFilter, MailFolder, MailMessage, ImapConfigInput, MailAttachment, MailBodyStatus, MailFailureStage } from './mail';

export interface MailBinding { id:string; method:MailMethod; state:MailState }
export type AnalysisStatus = 'unanalyzed' | 'analyzed';
export interface LocalMail extends MailMessage {
  fromAddress?:string; fromName?:string; replyToAddresses?:string[]; toAddresses?:string[]; ccAddresses?:string[];
  messageId?:string; inReplyTo?:string; references?:string[]; identitySource?:string; identityWarnings?:string[];
  folderId?:string|null; localId:string; bindingId:string; accountEmail:string; mailboxIdentity:string;
  cachedAt:string; analyzedAt:string|null; analysisId:string|null; status:AnalysisStatus;
  bodyStatus?:MailBodyStatus; bodyBytes?:number; attachments?:MailAttachment[];
}
/** 单封邮件导入失败明细；key 由邮箱绑定与稳定远端标识组成，用于重试定位。 */
export interface MailImportFailure {
  key:string; bindingId:string; mailboxEmail:string; remoteId:string; identity:string;
  subject:string; sender:string; receivedAt:string;
  stage:MailFailureStage; code:string; reason:string; retryable:boolean;
}
/** 无法取得邮件清单或邮箱身份不可用时的邮箱级错误，不伪造单封邮件。 */
export interface MailImportMailboxError { bindingId:string; mailboxEmail:string; stage:MailFailureStage; code:string; reason:string }
export interface MailImportReport {
  added:number; skipped:number; failed:number; limited:boolean; errors:string[];
  /** 新增成功且正文完整 / 仅摘要或部分正文的计数；旧任务可能没有。 */
  bodyRead?:number; bodyPending?:number;
  /** 本次仅取得摘要/部分正文的本地邮件，可直接补读。 */
  pendingLocalIds?:string[];
  failures?:MailImportFailure[]; mailboxErrors?:MailImportMailboxError[];
}
export interface MailAnalysis {
  id:string; messageIds:string[]; instructions:string; notes:string;
  drafts:TodoDraft[]; createdAt:string; saved:boolean; model:string; baseUrl:string;
  /** false 表示只分析了部分正文，邮件继续保持未分析。 */
  complete?:boolean;
  /** 同一个分析记录里已经分批添加过的草稿临时标识。 */
  addedTempIds?:string[];
  /** 已添加草稿对应的真实待办，用于后续保留父子层级。 */
  addedTodos?:Record<string,string>;
}
export interface LocalMailQuery { folderId?:string|null; bindingIds?:string[]; status?:AnalysisStatus; search?:string; offset?:number; limit?:number }
export interface LocalMailPage { messages:LocalMail[]; total:number }
export interface MailPreset { id:string;bindingId:string;name:string;filter:MailFilter }
export interface LocalMailFolder { id:string;name:string }
export interface MailWorkspaceAPI {
  listPresets(bindingId:string):Promise<Result<MailPreset[]>>;
  savePreset(bindingId:string,input:{id?:string;name:string;filter:MailFilter}):Promise<Result<MailPreset>>;
  deletePreset(id:string):Promise<Result<void>>;
  knownSenders(bindingId:string):Promise<Result<string[]>>;
  listFoldersLocal():Promise<Result<LocalMailFolder[]>>;
  createLocalFolder(name:string):Promise<Result<LocalMailFolder>>;
  renameLocalFolder(id:string,name:string):Promise<Result<LocalMailFolder>>;
  deleteLocalFolder(id:string):Promise<Result<void>>;
  moveLocal(ids:string[],folderId:string|null):Promise<Result<{count:number}>>;
  deleteLocal(ids:string[]):Promise<Result<{count:number}>>;
  getAccounts():Promise<Result<MailBinding[]>>;
  addAccount(method:MailMethod):Promise<Result<MailBinding>>;
  removeAccount(id:string):Promise<Result<void>>;
  getAccount(id:string):Promise<Result<MailBinding>>;
  configureImap(id:string,input:ImapConfigInput):Promise<Result<MailBinding>>;
  configureMicrosoft(id:string,clientId:string):Promise<Result<MailBinding>>;
  connect(id:string):Promise<Result<MailBinding>>;
  disconnect(id:string):Promise<Result<MailBinding>>;
  listFolders(id:string,parentId?:string):Promise<Result<MailFolder[]>>;
  saveFilter(id:string,filter:MailFilter):Promise<Result<MailBinding>>;
  importMail(input:{bindingIds:string[];filter?:MailFilter;onlyNew:boolean}):Promise<Result<MailImportReport>>;
  listLocal(query:LocalMailQuery):Promise<Result<LocalMailPage>>;
  getLocal(id:string):Promise<Result<LocalMail>>;
  openOriginal(id:string):Promise<Result<void>>;
  analyze(input:{messageIds:string[];instructions:string;target:Pick<Settings,'baseUrl'|'model'>}):Promise<Result<MailAnalysis>>;
  getAnalysis(id:string):Promise<Result<MailAnalysis>>;
  saveAnalysis(id:string,drafts:TodoDraft[]):Promise<Result<{count:number}>>;
}
