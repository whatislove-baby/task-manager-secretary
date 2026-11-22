import { z } from 'zod';
const calendarDate = z.string().regex(/^\d{4}-\d{2}-\d{2}$/).refine(v => { const d = new Date(v + 'T00:00:00Z'); return Number.isFinite(d.getTime()) && d.toISOString().slice(0,10) === v; }, '无效日期');
const minute = z.string().datetime({ offset: true }).refine(v => new Date(v).getUTCSeconds() === 0 && new Date(v).getUTCMilliseconds() === 0, '时间只精确到分钟');
const zone = z.string().min(1).refine(v => { try { new Intl.DateTimeFormat('en', { timeZone: v }); return true; } catch { return false; } }, '无效时区');
export const deadlineSchema = z.discriminatedUnion('kind', [z.strictObject({ kind: z.literal('date'), date: calendarDate }), z.strictObject({ kind: z.literal('minute'), at: minute.transform(v => new Date(v).toISOString()), timeZone: zone })]);
export const dueSchema = z.discriminatedUnion('mode', [z.strictObject({ mode: z.literal('none') }), z.strictObject({ mode: z.literal('inherit') }), z.strictObject({ mode: z.literal('value'), value: deadlineSchema })]);
export type Deadline = z.infer<typeof deadlineSchema>;
export type Due = z.infer<typeof dueSchema>;
export const createTodoSchema = z.strictObject({ title: z.string().trim().min(1, '标题不能为空').max(200), notes: z.string().max(10000).optional(), parentId: z.string().uuid().nullable().optional(), due: dueSchema.optional() });
export const todoPatchSchema = createTodoSchema.pick({ title: true, notes: true, due: true }).partial().refine(v => Object.keys(v).length > 0, '没有修改内容');
export const completionConfigSchema = z.strictObject({ cascadeComplete: z.boolean(), autoCompleteParent: z.boolean() });
export const defaultCompletionConfig = { cascadeComplete: true, autoCompleteParent: true };
export type CompletionConfig = z.infer<typeof completionConfigSchema>;
export type CreateTodoInput = z.infer<typeof createTodoSchema>;
export type TodoPatch = z.infer<typeof todoPatchSchema>;
export interface Todo {
  id: string; parentId: string | null; title: string; notes: string; completed: boolean; due: Due;
  /** 完成时刻；仅 completed 为真时有意义，用于“无截止任务只在完成当天显示”。 */
  completedAt?: string | null;
  createdAt: string; updatedAt: string; revision: number; deletedAt: string | null; trashBatchId: string | null;
}
export interface ResolvedDue { deadline: Deadline | null; inherited: boolean; sourceId: string | null; displayedDue: Deadline | null }
export interface DueWarning { todoId: string; parentId: string; code: 'AFTER_PARENT'; message: string }
export interface Mutation<T> { value: T; warnings: DueWarning[] }
export const draftSchema = createTodoSchema.omit({ parentId: true }).extend({
  tempId: z.string().min(1).max(100), parentTempId: z.string().min(1).max(100).nullable(),
  source: z.string().max(2000).default(''), sourceMailIds:z.array(z.string().min(1).max(2048)).max(20).optional(),
  /** 分批添加时由服务端预先生成的稳定待办标识；普通 AI 草稿不需要。 */
  todoId:z.string().uuid().optional(),
  /** 父草稿已在更早批次保存时，指向真实父待办；仅用于分批添加。 */
  parentTodoId:z.string().uuid().optional(),
});
export type TodoDraft = z.infer<typeof draftSchema>;
export const extractionSchema = z.strictObject({ drafts: z.array(draftSchema).max(100), notes: z.string().max(2000).default('') });
export type Extraction = z.infer<typeof extractionSchema>;
