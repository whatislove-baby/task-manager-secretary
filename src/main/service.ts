import { randomUUID } from 'node:crypto';
import { z } from 'zod';
import type { DesktopAPI, Result } from '../shared/contracts';
import { createTodoSchema, todoPatchSchema, completionConfigSchema } from '../shared/todo';
import { TaskRepository } from './repository';
import { SettingsStore } from './settings';
import { extractDrafts } from './ai';
import { parseImportedText } from './import';
import { CanvasFeedStore, fetchCanvasFeed } from './canvas';
import type { CanvasSyncReport } from '../shared/canvas';
import { localDay } from '../shared/planner';
export const MAX_BACKUP_BYTES = 64 * 1024 * 1024;
export interface FilePorts {
  openExternal?(url: string): Promise<void>;
  chooseText(): Promise<{ path: string; content: Buffer } | null>;
  writeBackup(text: string): Promise<boolean>;
  chooseBackup(): Promise<Buffer | null>;
}
function message(error: unknown): string {
  if (error instanceof z.ZodError) return error.issues[0]?.message || '输入格式不正确。';
  const detail = error instanceof Error ? error.message : '';
  if (/fetch|network/i.test(detail)) return '无法连接AI服务，请检查网络和地址。';
  if (/timeout|aborted/i.test(detail)) return 'AI请求超时，没有保存待办，请重试。';
  return detail || '操作失败。';
}
export function createBackendService(repository: TaskRepository, settings: SettingsStore, files: FilePorts, request: typeof fetch = fetch, canvas?: CanvasFeedStore, clock: () => Date = () => new Date()): DesktopAPI {
  let queue = Promise.resolve();
  let canvasSync: Promise<Result<CanvasSyncReport>> | null = null;
  const feedStore = () => { if (!canvas) throw new Error("Canvas订阅存储不可用。"); return canvas; };
  async function result<T>(action: () => Promise<T> | T): Promise<Result<T>> {
    try { return { ok:true, data:await action() }; } catch (error) { return { ok:false, error:message(error) }; }
  }
  function write<T>(action: () => Promise<T> | T): Promise<Result<T>> {
    const pending = queue.then(() => result(action)); queue = pending.then(() => undefined); return pending;
  }
  const id = (value: unknown) => z.string().uuid().parse(value);
  const revision = (value: unknown) => z.number().int().min(1).parse(value);
  return {
    getCanvasSource: value => result(() => repository.getCanvasSource(id(value))),
    openCanvasOriginal: value => result(async () => {
      const source = repository.getCanvasSource(id(value));
      if (!source?.url) throw new Error('这项待办没有 Canvas 原文链接。');
      let url: URL;
      try { url = new URL(source.url); } catch { throw new Error('Canvas 原文链接无效。'); }
      if (!['https:', 'http:'].includes(url.protocol) || url.username || url.password) throw new Error('Canvas 原文链接无效。');
      if (!files.openExternal) throw new Error('当前环境无法打开浏览器。');
      try { await files.openExternal(url.href); } catch { throw new Error('无法打开浏览器，请稍后重试。'); }
    }),
    getCanvasConfig: () => result(() => canvas ? canvas.get() : { configured: false, host: '' }),
    saveCanvasFeed: url => write(() => { const sources = repository.getCanvasSourceIds(); return feedStore().save(url, sources.length === 1 ? sources[0] : undefined); }),
    clearCanvasFeed: () => write(() => feedStore().clear()),
    syncCanvas: () => {
      if (canvasSync) return canvasSync;
      canvasSync = (async (): Promise<Result<CanvasSyncReport>> => {
        try {
          const before = await feedStore().forRequest();
          const feed = await fetchCanvasFeed(before.url, request, clock());
          return await write(async () => {
            const current = await feedStore().forRequest();
            if (current.url !== before.url || current.sourceId !== before.sourceId) throw new Error('同步期间订阅配置已变化，请重新同步。');
            const today = localDay(clock());
            const upcoming = feed.items.filter(item => item.due.mode === 'value' &&
              (item.due.value.kind === 'date' ? item.due.value.date : localDay(new Date(item.due.value.at))) >= today);
            const report = repository.syncCanvas(before.sourceId, upcoming);
            report.expired = feed.items.length - upcoming.length;
            report.issues = feed.issues;
            report.skipped += feed.issues.length + report.expired;
            return report;
          });
        } catch (error) { return { ok: false, error: error instanceof Error ? error.message : 'Canvas同步失败，请重试。' }; }
      })().finally(() => { canvasSync = null; });
      return canvasSync;
    },
    getDayOrder: day => result(() => repository.getDayOrder(day)),
    setDayOrder: (day,ids) => write(() => repository.setDayOrder(day,z.array(z.string().uuid()).max(100000).parse(ids))),
    listTodos: includeDeleted => result(() => repository.list(z.boolean().parse(includeDeleted ?? false))),
    effectiveDue: value => result(() => repository.effectiveDue(id(value))),
    createTodo: value => write(() => repository.create(createTodoSchema.parse(value))),
    editTodo: (value,patch,completed,rev) => write(() => repository.editTodo(id(value),todoPatchSchema.parse(patch),z.boolean().parse(completed),revision(rev))),
    updateTodo: (value,patch,rev) => write(() => repository.update(id(value),todoPatchSchema.parse(patch),revision(rev))),
    moveTodo: (value,parent,rev) => write(() => repository.move(id(value),parent === null ? null : id(parent),revision(rev))),
    setCompleted: (value,completed,rev) => write(() => repository.setCompleted(id(value),z.boolean().parse(completed),revision(rev))),
    trashTodo: (value,rev) => write(() => repository.trash(id(value),revision(rev))),
    restoreBatch: value => write(() => repository.restore(id(value))),
    saveDrafts: (drafts,token) => write(() => repository.saveDrafts(drafts,z.string().min(1).max(200).parse(token))),
    getCompletionConfig: () => result(() => repository.getConfig()),
    setCompletionConfig: config => write(() => repository.setConfig(completionConfigSchema.parse(config)).value),
    getSettings: () => result(() => settings.get()),
    saveSettings: value => write(() => settings.save(value)),
    clearKey: () => write(() => settings.clearKey()),
    extract: text => result(async () => ({ ...await extractDrafts(text,await settings.forRequest(),request), confirmationToken:randomUUID() })),
    importText: () => result(async () => { const file=await files.chooseText(); return file ? parseImportedText(file.path,file.content) : null; }),
    exportBackup: () => result(() => {
      const text = JSON.stringify(repository.exportBackup(),null,2);
      if (Buffer.byteLength(text,'utf8') > MAX_BACKUP_BYTES) throw new Error('备份超过64MB，请完整备份应用 data 文件夹。');
      return files.writeBackup(text);
    }),
    importBackup: () => write(async () => {
      const content=await files.chooseBackup(); if (!content) return false;
      if (content.length > MAX_BACKUP_BYTES) throw new Error('备份超过64MB，请先检查文件。');
      let value: unknown; try { value=JSON.parse(content.toString('utf8').replace(/^\uFEFF/,'')); } catch { throw new Error('备份不是有效JSON。'); }
      repository.importBackup(value); return true;
    }),
  };
}
