import ICAL from 'ical.js';
import { randomBytes } from 'node:crypto';
import { readFileSync, writeFileSync, renameSync, existsSync, unlinkSync } from 'node:fs';
import { join } from 'node:path';
import { z } from 'zod';
import type { KeyVault } from './settings';
import type { CanvasConfig, CanvasItem } from '../shared/canvas';
import { dueSchema, type Due } from '../shared/todo';

const MAX_BYTES = 5 * 1024 * 1024;
const MAX_ITEMS = 5000;
const MAX_EXPANDED_DESCRIPTION_BYTES = 40 * 1024 * 1024;
export function normalizeCanvasUrl(value: string): string {
  try {
    const url = new URL(value.trim().replace(/^webcal:/i, 'https:'));
    if (url.protocol !== 'https:' || url.username || url.password || url.hash || !url.hostname) throw new Error();
    return url.href;
  } catch { throw new Error('请输入有效的 HTTPS Canvas 日历订阅链接。'); }
}
const storedSchema = z.strictObject({ sourceId: z.string().regex(/^[a-f0-9]{64}$/), host: z.string().min(1), encryptedUrl: z.string().min(1) });
export class CanvasFeedStore {
  private file: string;
  constructor(directory: string, private vault: KeyVault) { this.file = join(directory, 'canvas-feed.json'); }
  private read() {
    if (!existsSync(this.file)) return null;
    try { return storedSchema.parse(JSON.parse(readFileSync(this.file, 'utf8'))); }
    catch { throw new Error('Canvas 配置无法读取，请先备份数据目录并检查配置文件。'); }
  }
  get(): CanvasConfig { const config = this.read(); return { configured: !!config, host: config?.host ?? '' }; }
  async save(value: string, restoreSourceId?: string): Promise<CanvasConfig> {
    const url = normalizeCanvasUrl(value), old = this.read(), host = new URL(url).host;
    if (restoreSourceId !== undefined && !/^[a-f0-9]{64}$/.test(restoreSourceId)) throw new Error('无效的订阅来源标识。');
    if (!await this.vault.available()) throw new Error('系统加密不可用，未保存 Canvas 链接。');
    const encryptedUrl = (await this.vault.encrypt(url)).toString('base64');
    // 加密过程中若配置已变化，拒绝覆盖较新的选择。
    if (JSON.stringify(this.read()) !== JSON.stringify(old)) throw new Error('Canvas 配置已变化，请重试。');
    const sourceId = old?.host === host ? old.sourceId : !old && restoreSourceId ? restoreSourceId : randomBytes(32).toString('hex');
    writeFileSync(this.file + '.tmp', JSON.stringify({ sourceId, host, encryptedUrl }), { mode: 0o600 });
    renameSync(this.file + '.tmp', this.file); return this.get();
  }
  clear() { this.read(); if (existsSync(this.file)) unlinkSync(this.file); }
  async forRequest(): Promise<{ url: string; sourceId: string }> {
    const config = this.read(); if (!config) throw new Error('请先保存 Canvas 日历订阅链接。');
    try {
      const decoded = await this.vault.decrypt(Buffer.from(config.encryptedUrl, 'base64'));
      const url = normalizeCanvasUrl(decoded.result);
      if (new URL(url).host !== config.host) throw new Error();
      return { url, sourceId: config.sourceId };
    } catch { throw new Error('Canvas 链接无法解密，请在此设备重新保存订阅链接。'); }
  }
}

type Component = InstanceType<typeof ICAL.Component>;
type Time = InstanceType<typeof ICAL.Time>;
function text(value: unknown): string {
  return String(value ?? '').replace(/<script\b[^>]*>[\s\S]*?<\/script>/gi, '').replace(/<style\b[^>]*>[\s\S]*?<\/style>/gi, '')
    .replace(/<(?:br\b[^>]*|\/p|\/div|\/li)>/gi, '\n').replace(/<[^>]*>/g, '')
    .replace(/&(#x[0-9a-f]+|#\d+|amp|lt|gt|quot|apos|nbsp);/gi, (_, entity: string) => {
      const map: Record<string,string> = { amp:'&',lt:'<',gt:'>',quot:'"',apos:"'",nbsp:' ' };
      if (entity[0] !== '#') return map[entity.toLowerCase()] ?? '';
      const n = entity[1].toLowerCase() === 'x' ? parseInt(entity.slice(2),16) : Number(entity.slice(1));
      return n > 0 && n <= 0x10ffff ? String.fromCodePoint(n) : '';
    }).trim();
}
function zoneName(component: Component, property = 'dtstart'): string {
  return String(component.getFirstProperty(property)?.getParameter('tzid') ?? '');
}
function localZone(): string { return Intl.DateTimeFormat().resolvedOptions().timeZone || 'UTC'; }
function instant(time: Time, tzid: string): Date {
  if (time.zone?.tzid && !['floating','local'].includes(time.zone.tzid)) return time.toJSDate();
  if (!tzid) return new Date(time.year,time.month-1,time.day,time.hour,time.minute,time.second);
  // IANA zones need not have VTIMEZONE; convert wall time with Intl, reject DST gaps.
  try {
    const fmt = new Intl.DateTimeFormat('en-CA', { timeZone:tzid, year:'numeric',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',second:'2-digit',hourCycle:'h23' });
    const wall = Date.UTC(time.year,time.month-1,time.day,time.hour,time.minute,time.second);
    let at = wall;
    for (let i=0;i<4;i++) {
      const parts = Object.fromEntries(fmt.formatToParts(at).filter(p=>p.type!=='literal').map(p=>[p.type,Number(p.value)]));
      const rendered = Date.UTC(parts.year,parts.month-1,parts.day,parts.hour,parts.minute,parts.second);
      const difference = wall-rendered; if (!difference) return new Date(at); at += difference;
    }
  } catch { throw new Error('订阅包含无法识别的时区，请检查 Canvas 时区设置。'); }
  throw new Error('订阅时间处于不存在的夏令时区间，未保存任何事项。');
}
function due(time: Time, tzid: string): Due {
  if (!time || !Number.isFinite(time.year)) throw new Error('日历事项缺少有效时间。');
  if (time.isDate) return dueSchema.parse({mode:'value',value:{kind:'date',date:time.toString().slice(0,10)}});
  const at = instant(time,tzid); at.setUTCSeconds(0,0);
  let timeZone = tzid || (time.zone?.tzid === 'UTC' ? 'UTC' : localZone());
  try { new Intl.DateTimeFormat('en', {timeZone}); } catch { timeZone = 'UTC'; }
  return dueSchema.parse({mode:'value',value:{kind:'minute',at:at.toISOString(),timeZone}});
}
function identity(time: Time, tzid: string): string { return time.isDate ? time.toString().slice(0,10) : instant(time,tzid).toISOString(); }
function componentRank(component: Component): [number,number] {
  const sequence = Number(component.getFirstPropertyValue('sequence') ?? 0);
  const modified = component.getFirstPropertyValue('last-modified') ?? component.getFirstPropertyValue('dtstamp');
  return [sequence, modified && typeof modified === 'object' && 'toJSDate' in modified ? Number((modified as Time).toJSDate()) : 0];
}
function cancelled(component: Component) { return String(component.getFirstPropertyValue('status') ?? '').toUpperCase() === 'CANCELLED'; }
export interface CanvasFeed { items: CanvasItem[]; issues: Array<{title: string; reason: string}> }
function originalUrl(value: unknown): string {
  try {
    const url = new URL(String(value ?? '').trim());
    return url.href.length <= 8000 && ['https:', 'http:'].includes(url.protocol) && !url.username && !url.password ? url.href : '';
  } catch { return ''; }
}
function issueTitle(component: Component): string {
  try { return text(component.getFirstPropertyValue('summary')).replace(/(?:https?|webcal):\/\/[^\s]+/gi, '［链接］').slice(0,200) || '未命名事项'; }
  catch { return '无法读取的事项'; }
}
function issueReason(error: unknown): string {
  const value = error instanceof Error ? error.message : '';
  if (/标题/.test(value)) return '标题为空或超过200个字符。';
  if (/唯一标识/.test(value)) return '缺少有效的事项标识。';
  if (/冲突版本/.test(value)) return '同一事项存在冲突版本，请检查 Canvas 后重试。';
  if (/批量修改/.test(value)) return '暂不支持批量修改后续重复事项。';
  if (/按小时或分钟/.test(value)) return '暂不支持按小时或分钟重复的事项。';
  if (/范围过大/.test(value)) return '重复事项的计算范围过大。';
  if (/时区|夏令时/.test(value)) return '事项的时区或夏令时时间无效。';
  if (/日期|时间/.test(value)) return '缺少有效的开始或截止日期。';
  return '事项格式无效，未导入；其他有效事项仍可同步。';
}
export function parseCanvasFeed(input: string, now = new Date()): CanvasFeed {
  if (Buffer.byteLength(input,'utf8') > MAX_BYTES) throw new FeedDownloadError('Canvas 日历超过5MB，未同步。');
  try {
    const root = new ICAL.Component(ICAL.parse(input.replace(/^\uFEFF/,'')));
    if (root.name !== 'vcalendar' || !/^BEGIN:VCALENDAR\s*$/im.test(input.replace(/^\uFEFF/,'')) || !/^END:VCALENDAR\s*$/im.test(input)) throw new Error('订阅返回的不是有效日历。');
    const groups = new Map<string, Component[]>();
    const issues: CanvasFeed['issues'] = [];
    const items = new Map<string,CanvasItem>();
    function addIssue(component: Component, error: unknown) {
      issues.push({title:issueTitle(component),reason:issueReason(error)});
      if (items.size + issues.length > MAX_ITEMS) throw new FeedDownloadError('Canvas 日历事项超过5000项，请缩小订阅范围。');
    }
    for (const component of root.getAllSubcomponents('vevent')) {
      try {
        const uid = String(component.getFirstPropertyValue('uid') ?? '').trim();
        if (!uid || uid.length > 850) throw new Error('日历事项缺少有效唯一标识。');
        const group = groups.get(uid) ?? []; group.push(component); groups.set(uid,group);
      } catch (error) { addIssue(component,error); }
    }
    // Repeated instances share their component's long description instead of sanitizing/copying it for every date.
    const content = new WeakMap<Component, Pick<CanvasItem, 'title' | 'description' | 'url'>>();
    const descriptionSizes = new WeakMap<Component, number>();
    let expandedDescriptionBytes = 0;
    const lower = new Date(now); lower.setDate(lower.getDate()-30); lower.setHours(0,0,0,0);
    const upper = new Date(now); upper.setDate(upper.getDate()+366); upper.setHours(23,59,59,999);
    for (const [uid, group] of groups) {
      const pending = new Map<string,CanvasItem>();
      const pendingSizes = new Map<string, number>();
      let pendingDescriptionBytes = 0;
      try {
        const components = new Map<string,Component>();
        for (const component of group) {
          const rid = component.getFirstPropertyValue('recurrence-id') as Time | null;
          if (component.getFirstProperty('recurrence-id')?.getParameter('range')) throw new Error('暂不支持批量修改后续重复事项的日历格式。');
          const key = rid ? identity(rid,zoneName(component,'recurrence-id')) : '';
          const previous = components.get(key);
          if (!previous) components.set(key,component);
          else {
            const a=componentRank(previous),b=componentRank(component);
            if (b[0]>a[0] || (b[0]===a[0] && b[1]>a[1])) components.set(key,component);
            else if (b[0]===a[0] && b[1]===a[1] && previous.toString()!==component.toString()) throw new Error('同一日历事项存在冲突版本，未同步。');
          }
        }
        function add(component: Component, start: Time, recurrenceId?: string) {
          if (cancelled(component)) return;
          let metadata = content.get(component);
          if (!metadata) {
            metadata = { title:text(component.getFirstPropertyValue('summary')), description:text(component.getFirstPropertyValue('description')), url:originalUrl(component.getFirstPropertyValue('url')) };
            content.set(component,metadata);
            descriptionSizes.set(component,Buffer.byteLength(metadata.description, 'utf8'));
          }
          const {title} = metadata;
          if (!title || title.length>200) throw new Error('Canvas 事项标题为空或超过200字，未同步。');
          const deadline = due(start,zoneName(component));
          const key = recurrenceId ? JSON.stringify([uid,recurrenceId]) : JSON.stringify([uid]);
          if (key.length > 1000) throw new Error('日历事项缺少有效唯一标识。');
          const descriptionBytes = descriptionSizes.get(component)!;
          pendingDescriptionBytes += descriptionBytes - (pendingSizes.get(key) ?? 0);
          pendingSizes.set(key,descriptionBytes);
          if (expandedDescriptionBytes + pendingDescriptionBytes > MAX_EXPANDED_DESCRIPTION_BYTES) throw new FeedDownloadError('Canvas 重复展开后的说明总量超过40MiB，请缩小订阅范围。');
          pending.set(key,{key,...metadata,due:deadline});
          if(items.size+pending.size+issues.length>MAX_ITEMS) throw new FeedDownloadError('Canvas 日历事项超过5000项，请缩小订阅范围。');
        }
        const master = components.get('');
        if (!master) {
          for (const [rid,component] of components) if(!cancelled(component)) add(component,component.getFirstPropertyValue('dtstart') as Time,rid);
        } else if (!cancelled(master)) {
          const start = master.getFirstPropertyValue('dtstart') as Time;
          if(!start) throw new Error('Canvas 事项缺少开始或截止日期。');
          const recurring = master.hasProperty('rrule') || master.hasProperty('rdate');
          if(!recurring) {
            if(components.size>1) throw new Error('重复事项缺少规则，未同步。');
            add(master,start);
          } else {
            const rule = master.getFirstPropertyValue('rrule') as {freq?:string} | null;
            if(rule && !['DAILY','WEEKLY','MONTHLY','YEARLY'].includes(rule.freq ?? '')) throw new Error('暂不支持按小时或分钟重复的日历事项。');
            const expansion = new ICAL.RecurExpansion({component:master,dtstart:start});
            const seen = new Set<string>(); let iterations=0; let occurrence: Time | null;
            while((occurrence=expansion.next())) {
              if(++iterations>20000) throw new Error('重复日历范围过大，未同步。');
              const rid = identity(occurrence,zoneName(master)); const override = components.get(rid);
              seen.add(rid);
              const component = override ?? master;
              const actual = override ? override.getFirstPropertyValue('dtstart') as Time : occurrence;
              const moment = instant(occurrence,zoneName(master));
              if(actual && !cancelled(component)) {
                const actualDate = instant(actual,zoneName(component));
                if(actualDate>=lower && actualDate<=upper) add(component,actual,rid);
              }
              if(moment>upper) break;
            }
            for(const [rid,component] of components) if(rid && !seen.has(rid) && !cancelled(component)) {
              const actual = component.getFirstPropertyValue('dtstart') as Time;
              if(actual && instant(actual,zoneName(component))>=lower && instant(actual,zoneName(component))<=upper) add(component,actual,rid);
            }
          }
        }
        for (const [key,item] of pending) items.set(key,item);
        expandedDescriptionBytes += pendingDescriptionBytes;
      } catch(error) {
        if (error instanceof FeedDownloadError) throw error;
        addIssue(group[0],error);
      }
    }
    return {items:[...items.values()],issues};
  } catch(error) {
    if(error instanceof FeedDownloadError) throw error;
    throw new Error('Canvas 日历格式无效，未保存任何事项。');
  }
}
export function parseCanvasItems(input: string, now = new Date()): CanvasItem[] { return parseCanvasFeed(input,now).items; }
class FeedDownloadError extends Error {}
export async function fetchCanvasFeed(value: string, request: typeof fetch = fetch, now = new Date()): Promise<CanvasFeed> {
  const url = normalizeCanvasUrl(value), controller = new AbortController();
  const timer = setTimeout(()=>controller.abort(),20000);
  let content = '';
  try {
    const response = await request(url,{method:'GET',redirect:'error',signal:controller.signal,headers:{Accept:'text/calendar'}});
    if(!response.ok) throw new FeedDownloadError(`Canvas 返回状态${response.status}，请检查订阅链接。`);
    if(Number(response.headers.get('content-length'))>MAX_BYTES) throw new FeedDownloadError('Canvas 日历超过5MB，未同步。');
    const reader=response.body?.getReader(); if(!reader) throw new FeedDownloadError('Canvas 返回空日历，请检查订阅链接。');
    const chunks: Uint8Array[]=[];let bytes=0;
    try { while(true) { const {value,done}=await reader.read();if(done)break;bytes+=value.byteLength;if(bytes>MAX_BYTES)throw new FeedDownloadError('Canvas 日历超过5MB，未同步。');chunks.push(value); } }
    finally { await reader.cancel().catch(()=>{}); reader.releaseLock(); }
    content = Buffer.concat(chunks).toString('utf8');
  } catch(error) {
    if(error instanceof FeedDownloadError) throw error;
    throw new Error(controller.signal.aborted ? 'Canvas 同步超时，请稍后重试。' : '无法读取 Canvas 订阅，请检查网络、订阅链接或是否需要重新生成链接。');
  } finally { clearTimeout(timer); }
  return parseCanvasFeed(content,now);
}

export async function fetchCanvasItems(value: string, request: typeof fetch = fetch, now = new Date()): Promise<CanvasItem[]> { return (await fetchCanvasFeed(value,request,now)).items; }
