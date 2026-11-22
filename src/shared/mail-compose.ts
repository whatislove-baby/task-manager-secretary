import type { Result, Settings } from './contracts';
import type { FileRecord } from './mail-files';
import type {LocalMail} from './mail-workspace';

export type ReplyMailSource=LocalMail & {fromAddress?:string;replyToAddresses?:string[];messageId?:string;identityWarnings?:string[]};
export interface ReplyContext {to:string;replyToAddresses:string[];sourceMessageId:string;inReplyTo:string;references:string[];threadAvailable:boolean;replyToLocalId:string;bindingId:string;accountEmail:string}

export interface SmtpConfig {
  bindingId: string;
  email: string;
  host: string;
  port: number;
  security: 'ssl' | 'starttls';
  configured: boolean;
  hasSecret: boolean;
}

export interface ComposeInput {
  originDraftId?: string;
  expectedRevision?: number;
  bindingId: string;
  to: string;
  subject: string;
  body: string;
  replyToLocalId?: string;
  attachmentIds?: string[];
}

/** Immutable preview and durable outcome. "sent" means SMTP acceptance, not delivery. */
export interface ComposeReceipt {
  originDraftId?: string;
  expectedRevision?: number;
  id: string;
  bindingId: string;
  from: string;
  to: string;
  subject: string;
  body: string;
  messageId: string;
  createdAt: string;
  status: 'prepared' | 'sending' | 'sent' | 'failed' | 'uncertain';
  detail: string;
  sentAt?: string;
  attachments?: FileRecord[];
  replyContext?: ReplyContext;
}

export interface MailComposeAPI {
  getConfig(bindingId: string): Promise<Result<SmtpConfig>>;
  saveConfig(bindingId: string, input: Pick<SmtpConfig, 'host' | 'port' | 'security'>): Promise<Result<SmtpConfig>>;
  generate(input: {
    instructions: string;
    subject: string;
    body: string;
    replyToLocalId?: string;
    fileIds?: string[];
    target: Pick<Settings, 'baseUrl' | 'model'>;
  }): Promise<Result<{subject: string; body: string}>>;
  prepare(input: ComposeInput): Promise<Result<ComposeReceipt>>;
  send(id: string): Promise<Result<ComposeReceipt>>;
  listRecent(): Promise<Result<ComposeReceipt[]>>;
}
