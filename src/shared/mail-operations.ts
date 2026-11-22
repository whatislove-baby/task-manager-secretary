import type { Result, Settings } from './contracts';
import type { MailFilter } from './mail';
import type { MailImportReport } from './mail-workspace';
import type { FileTaskInput } from './mail-files';
export type MailJobState='queued'|'running'|'pausing'|'paused'|'canceling'|'canceled'|'completed'|'failed';
export interface MailJob {notificationAcknowledged?:boolean;messageCount?:number;id:string;kind:'import'|'analysis'|'chat'|'files';state:MailJobState;label:string;done:number;total:number;createdAt:string;updatedAt:string;conversationId?:string;analysisIds?:string[];artifactIds?:string[];report?:MailImportReport;error?:string}
export interface ConversationSummary {id:string;title:string;createdAt:string;updatedAt:string;turnCount:number}
export interface ConversationTurn {id:string;role:'user'|'assistant';content:string;createdAt:string;analysisId?:string;jobId?:string;fileIds?:string[];artifactIds?:string[]}
export interface MailConversation extends ConversationSummary {messageIds:string[];turns:ConversationTurn[]}
export interface MailOperationsAPI {
 /** 收取所有已连接邮箱最近7天的收件箱，不使用旧的主题或发件人预设。 */
 startInboxSync():Promise<Result<MailJob>>;
 startFileTask(input:FileTaskInput):Promise<Result<MailJob>>;
 startConversation(text:string,target:Pick<Settings,'baseUrl'|'model'>):Promise<Result<MailJob>>;
 acknowledgeJob(id:string):Promise<Result<void>>;
 listJobs():Promise<Result<MailJob[]>>;
 getJob(id:string):Promise<Result<MailJob>>;
 startImport(input:{bindingIds:string[];filter?:MailFilter;onlyNew:boolean}):Promise<Result<MailJob>>;
 startAnalysis(input:{messageIds:string[];instructions:string;target:Pick<Settings,'baseUrl'|'model'>}):Promise<Result<MailJob>>;
 pauseJob(id:string):Promise<Result<MailJob>>;
 resumeJob(id:string):Promise<Result<MailJob>>;
 /** 取消本次任务；已有成功结果保留，取消后不能继续。 */
 cancelJob(id:string):Promise<Result<MailJob>>;
 /** 重试导入失败明细；keys 为空时重试全部可重试失败项。 */
 retryImportFailures(input:{jobId:string;keys?:string[]}):Promise<Result<MailJob>>;
 /** 补读指定本地邮件的正文，可暂停/取消，仍按失败明细记录。 */
 retryBodies(input:{localIds:string[]}):Promise<Result<MailJob>>;
 listConversations():Promise<Result<ConversationSummary[]>>;
 getConversation(id:string):Promise<Result<MailConversation>>;
 continueConversation(id:string,text:string,target:Pick<Settings,'baseUrl'|'model'>):Promise<Result<MailJob>>;
 deleteConversation(id:string):Promise<Result<void>>;
}
