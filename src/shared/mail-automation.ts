import type { Result } from './contracts';

export type MailAutomationCategory = 'action' | 'important' | 'normal' | 'spam' | 'review';
export interface MailAutomationConfigInput {
  enabled: boolean;
  bindingIds: string[];
  times: string[];
  timeZone: string;
  lookbackDays: number;
  maxMessagesPerRun: number;
  importantSenders: string[];
  mutedSenders: string[];
}
export interface MailAutomationConfig extends MailAutomationConfigInput { nextRunAt: string | null }
export type MailAutomationRunState = 'queued' | 'running' | 'completed' | 'failed';
export interface MailAutomationRun {
  id: string;
  trigger: 'schedule' | 'manual' | 'retry';
  state: MailAutomationRunState;
  startedAt: string | null;
  finishedAt: string | null;
  imported: number;
  classified: number;
  failed: number;
  skipped: number;
  error?: string;
}
export interface MailAutomationStatus {
  state: 'idle' | 'queued' | 'running' | 'failed';
  lastRun: MailAutomationRun | null;
  nextRunAt: string | null;
}
export interface MailAutomationBackgroundStatus {
  supported: boolean;
  installed: boolean;
  active?: boolean;
  message?: string;
}
export interface MailAutomationItem {
  localId: string;
  bindingId: string;
  subject: string;
  sender: string;
  receivedAt: string;
  category: MailAutomationCategory;
  summary: string;
  reason: string;
  analysisId: string | null;
  classifiedAt: string;
  corrected: boolean;
  error?: string;
}
export interface MailAutomationResultQuery { category?: MailAutomationCategory; offset?: number; limit?: number }
export interface MailAutomationResultPage {
  items: MailAutomationItem[];
  total: number;
  counts: Partial<Record<MailAutomationCategory, number>>;
}
export interface MailAutomationAPI {
  getConfig(): Promise<Result<MailAutomationConfig>>;
  saveConfig(input: MailAutomationConfigInput): Promise<Result<MailAutomationConfig>>;
  getStatus(): Promise<Result<MailAutomationStatus>>;
  runNow(): Promise<Result<{ queued: true }>>;
  listResults(query: MailAutomationResultQuery): Promise<Result<MailAutomationResultPage>>;
  correctCategory(localId: string, category: MailAutomationCategory): Promise<Result<MailAutomationItem>>;
  getBackgroundStatus?(): Promise<Result<MailAutomationBackgroundStatus>>;
}

export const defaultMailAutomationConfig: MailAutomationConfigInput = {
  enabled: false, bindingIds: [], times: ['09:00', '18:00'], timeZone: 'Asia/Hong_Kong',
  lookbackDays: 7, maxMessagesPerRun: 20, importantSenders: [], mutedSenders: [],
};
