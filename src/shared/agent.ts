import type {Result} from './contracts';
import type {ComposeInput} from './mail-compose';
import type {Todo,TodoPatch} from './todo';

export type AgentRunStatus = 'queued'|'running'|'waiting'|'paused'|'completed'|'failed'|'canceled';
export interface AgentSource {kind:'mail'|'file'|'task'|'learning'|'skill';id:string;title:string;excerpt?:string}
export interface AgentArtifact {kind:'file'|'task_draft'|'mail_draft'|'learning_draft'|'skill_draft'|'task_change'|'conversation'|'briefing';id:string;title:string}
export interface AgentEvent {
  id:string;type:'status'|'tool'|'source'|'artifact'|'warning';label:string;
  createdAt:string;tool?:string;callId?:string;detail?:string;status?:'running'|'completed'|'failed';
  source?:AgentSource;artifact?:AgentArtifact;
}
export interface AgentClarification {id:string;question:string;options:string[]}
export interface AgentRun {
  id:string;conversationId:string;status:AgentRunStatus;createdAt:string;updatedAt:string;
  partialText:string;events:AgentEvent[];sources:AgentSource[];artifacts:AgentArtifact[];
  clarification?:AgentClarification;error?:string;model?:string;
  stopping?:boolean;
  usage?:{inputTokens:number;outputTokens:number};
}
export interface AgentTurn {
  id:string;role:'user'|'assistant';content:string;createdAt:string;runId?:string;
  mailIds?:string[];fileIds?:string[];sources?:AgentSource[];artifacts?:AgentArtifact[];
}
export interface AgentConversationSummary {id:string;title:string;createdAt:string;updatedAt:string;turnCount:number;kind?:'secretary'|'task'|'chat';parentConversationId?:string;goal?:string;status?:AgentRunStatus}
export interface AgentConversation extends AgentConversationSummary {turns:AgentTurn[];activeRun?:AgentRun;lastRun?:AgentRun;children?:AgentConversationSummary[];mailIds?:string[];fileIds?:string[]}
export interface AgentChildInput {parentConversationId:string;title:string;goal:string;mailIds?:string[];fileIds?:string[]}
export type BriefingCategory='action'|'important'|'normal'|'spam'|'review';
export interface AgentBriefing {id:string;createdAt?:string;counts:Record<BriefingCategory,number>;items:{mailId:string;subject:string;sender?:string;receivedAt?:string;category:BriefingCategory;reason?:string;summary?:string}[];warning?:string}
export interface AgentTurnInput {conversationId?:string;text:string;mailIds?:string[];fileIds?:string[];reasoning?:'auto'|'high'|'low'}
export interface AgentMailDraft extends ComposeInput {id:string;createdAt:string;revision?:number;lifecycleStatus?:string;supersededBy?:string|null}
export interface AgentPendingItem extends AgentArtifact {conversationId:string;runId:string;createdAt:string}
export interface AgentAPI {
  getSecretary():Promise<Result<AgentConversation>>;
  createChild(input:AgentChildInput):Promise<Result<AgentConversation>>;
  getBriefing(id:string):Promise<Result<AgentBriefing>>;
  listConversations():Promise<Result<AgentConversationSummary[]>>;
  getConversation(id:string):Promise<Result<AgentConversation>>;
  startTurn(input:AgentTurnInput):Promise<Result<AgentRun>>;
  getRun(id:string):Promise<Result<AgentRun>>;
  resumeRun(input:{id:string;answer?:string;questionId?:string}):Promise<Result<AgentRun>>;
  pauseRun(id:string):Promise<Result<AgentRun>>;
  cancelRun(id:string):Promise<Result<AgentRun>>;
  deleteConversation(id:string):Promise<Result<void>>;
  getMailDraft(id:string):Promise<Result<AgentMailDraft>>;
  listPending():Promise<Result<AgentPendingItem[]>>;
  resolveMailDraft(input:{id:string;receiptId?:string;dismiss?:boolean}):Promise<Result<void>>;
}

export interface LearningScope {kind:'customer'|'course'|'personal';label:string;emails:string[];contextKey:string}
export interface LearningEvidence {kind:'file'|'mail';id:string;name?:string;sha256?:string;fromAddress?:string;contentHash?:string}
export interface LearningTeaching {
 source:'conversation'|'direct-input';conversationId?:string;runId?:string;
 turns:{id:string;runId:string;createdAt:string;content:string;textSha256:string}[];
 omittedUserTurns:number;sourceHash:string;
}
export interface LearningRecipe {
 inputColumns:string[];optionalInputColumns?:string[];outputColumns:string[];mapping:Record<string,string>;constants:Record<string,string>;
 concat:{target:string;columns:string[];separator:string;padLast?:number}[];operations:Record<string,unknown>[];
 requiredValues:string[];numericColumns:string[];moneyColumns:string[];totals:string[];
 allowExtraColumns:boolean;preserveRowCount:boolean;outputFormat:'csv'|'xlsx';sheetName?:string;
 commercial?:{currency:string;taxBasis:'inclusive'|'exclusive'|'not_applicable';unit:string};
}
export interface LearningPreview {columns:string[];rows:string[][];rowCount:number;inputRowCount:number;totals:Record<string,string>;warnings:string[];inputId:string;inputSha256:string;outputSha256:string;previewTruncated?:boolean;expectedOutputMatched?:boolean}
export interface LearningMethod {
 kind:'learning'|'learning_draft';id:string;title:string;scopeId:string;scope:LearningScope;version:number;versionHash:string;parentId?:string|null;
 status:'draft'|'approved'|'disabled'|'superseded';createdAt:string;reviewedAt?:string|null;
 proposal:{title:string;kind:'note'|'table_workflow';scope:LearningScope;instruction:string;sources:LearningEvidence[];teaching?:LearningTeaching;recipe?:LearningRecipe;exampleFileId?:string;expectedOutputFileId?:string;parentId?:string};preview:LearningPreview|null;
}
export interface ReviewInput {id:string;expectedHash:string}
export interface LearningAPI {
 list(query?:{scopeId?:string;status?:LearningMethod['status'];limit?:number}):Promise<Result<LearningMethod[]>>;
 get(id:string):Promise<Result<LearningMethod>>;approve(input:ReviewInput):Promise<Result<LearningMethod>>;disable(input:ReviewInput):Promise<Result<LearningMethod>>;
}
export interface TaskChange {
 id:string;kind:'task_change';title:string;action:'update'|'cancel';taskId:string;expectedRevision:number;patch:TodoPatch;reason:string;
 evidence:{mailId:string;quote:string;subject:string;bindingId:string}[];hash:string;before:Todo;after?:Todo;status:'pending'|'approved'|'rejected'|'stale';createdAt:string;
}
export interface TaskChangesAPI {list():Promise<Result<TaskChange[]>>;get(id:string):Promise<Result<TaskChange>>;approve(input:ReviewInput):Promise<Result<TaskChange>>;reject(input:ReviewInput):Promise<Result<TaskChange>>}
