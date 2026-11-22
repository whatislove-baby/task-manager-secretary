import { z } from 'zod';
import type { CanvasConfig, CanvasSyncReport, CanvasSource } from './canvas';
import type { Todo, CreateTodoInput, TodoPatch, CompletionConfig, ResolvedDue, Mutation, Extraction, TodoDraft } from './todo';
export const endpointSchema = z.string().url().refine(value => {
  const u = new URL(value);
  return u.protocol === 'https:' && !u.username && !u.password && !u.search && !u.hash;
}, '服务地址须为不含账号、查询参数的 HTTPS 地址');
export const settingsInputSchema = z.strictObject({ baseUrl: endpointSchema, model: z.string().trim().min(1).max(150), apiKey: z.string().trim().max(1000) });
export type SettingsInput = z.infer<typeof settingsInputSchema>;
export interface Settings { baseUrl: string; model: string; hasApiKey: boolean; dataDirectory: string }
export interface ImportedText { text: string; name: string }
export type Result<T> = { ok: true; data: T } | { ok: false; error: string };
export interface ExtractedDrafts extends Extraction { confirmationToken: string }
export interface DesktopAPI {
  getCanvasSource(todoId: string): Promise<Result<CanvasSource | null>>;
  openCanvasOriginal(todoId: string): Promise<Result<void>>;
  getCanvasConfig(): Promise<Result<CanvasConfig>>;
  saveCanvasFeed(url: string): Promise<Result<CanvasConfig>>;
  clearCanvasFeed(): Promise<Result<void>>;
  syncCanvas(): Promise<Result<CanvasSyncReport>>;
  getDayOrder(day: string): Promise<Result<string[]>>;
  setDayOrder(day: string, ids: string[]): Promise<Result<string[]>>;
  listTodos(includeDeleted?: boolean): Promise<Result<Todo[]>>;
  effectiveDue(id: string): Promise<Result<ResolvedDue>>;
  createTodo(input: CreateTodoInput): Promise<Result<Mutation<Todo>>>;
  editTodo(id: string, patch: TodoPatch, completed: boolean, revision: number): Promise<Result<Mutation<Todo>>>;
  updateTodo(id: string, patch: TodoPatch, revision: number): Promise<Result<Mutation<Todo>>>;
  moveTodo(id: string, parentId: string | null, revision: number): Promise<Result<Mutation<Todo>>>;
  setCompleted(id: string, completed: boolean, revision: number): Promise<Result<Mutation<Todo>>>;
  trashTodo(id: string, revision: number): Promise<Result<Mutation<string>>>;
  restoreBatch(batchId: string): Promise<Result<Mutation<Todo[]>>>;
  saveDrafts(drafts: TodoDraft[], token: string): Promise<Result<Mutation<number>>>;
  getCompletionConfig(): Promise<Result<CompletionConfig>>;
  setCompletionConfig(config: CompletionConfig): Promise<Result<CompletionConfig>>;
  getSettings(): Promise<Result<Settings>>;
  saveSettings(input: SettingsInput): Promise<Result<Settings>>;
  clearKey(): Promise<Result<void>>;
  importText(): Promise<Result<ImportedText | null>>;
  extract(text: string): Promise<Result<ExtractedDrafts>>;
  exportBackup(): Promise<Result<boolean>>;
  importBackup(): Promise<Result<boolean>>;
}
