import { test } from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { CanvasFeedStore, normalizeCanvasUrl, parseCanvasItems, parseCanvasFeed, fetchCanvasItems } from '../src/main/canvas';
const now = new Date('2026-10-29T12:00:00Z');
const event = (lines:string[]) => ['BEGIN:VEVENT',...lines,'END:VEVENT'].join('\r\n');
const calendar = (...events:string[]) => ['BEGIN:VCALENDAR','VERSION:2.0',...events,'END:VCALENDAR'].join('\r\n');
const base = event(['UID:assignment-1','DTSTART:20261030T143000Z','SUMMARY:Essay','DESCRIPTION:<p>Read &amp; write</p><script>bad()</script>','URL:https://school.example/courses/1/assignments/2']);

test('Canvas ICS保留日期与分钟精度、解折行/转义/纯文本；日期修改UID key保持', () => {
  const items = parseCanvasItems(calendar(base,event(['UID:all-day','DTSTART;VALUE=DATE:20261031','SUMMARY:Long title ',' continued\\, okay'])),now);
  assert.equal(items.length,2);
  assert.deepEqual(items[0].due,{mode:'value',value:{kind:'minute',at:'2026-10-30T14:30:00.000Z',timeZone:'UTC'}});
  assert.equal(items[0].description,'Read & write');
  assert.equal(items[0].url,'https://school.example/courses/1/assignments/2');
  assert.deepEqual(items[1].due,{mode:'value',value:{kind:'date',date:'2026-10-31'}});
  assert.equal(items[1].title,'Long title continued, okay');
  assert.equal(parseCanvasItems(calendar(base.replace('20261030','20261105')),now)[0].key,items[0].key);
});

test('IANA TZID无VTIMEZONE仍准确跨DST；不存在时刻拒绝', () => {
  const source = calendar(event(['UID:tz','DTSTART;TZID=America/New_York:20261101T120000','SUMMARY:Time zone']));
  const due = parseCanvasItems(source,now)[0].due;
  assert.equal(due.mode === 'value' && due.value.kind === 'minute' && due.value.at,'2026-11-01T17:00:00.000Z');
  assert.match(parseCanvasFeed(source.replace('20261101T120000','20260308T023000'),now).issues[0].reason,/夏令时/);
});

test('重复日程展开EXDATE/单次改期/取消例外，key使用原实例日期；重复版本选最新', () => {
  const master = event(['UID:weekly','DTSTART:20261028T100000Z','RRULE:FREQ=DAILY;COUNT=5','EXDATE:20261029T100000Z','SUMMARY:Daily']);
  const override = event(['UID:weekly','RECURRENCE-ID:20261030T100000Z','DTSTART:20261030T120000Z','SUMMARY:Moved']);
  const cancelled = event(['UID:weekly','RECURRENCE-ID:20261031T100000Z','STATUS:CANCELLED']);
  const items = parseCanvasItems(calendar(master,override,cancelled),now);
  assert.equal(items.length,3);
  const moved=items.find(item=>item.title==='Moved')!;
  assert.ok(moved.key.includes('2026-10-30T10:00:00.000Z'));
  assert.equal(moved.due.mode==='value' && moved.due.value.kind==='minute' && moved.due.value.at,'2026-10-30T12:00:00.000Z');
  const updated=base.replace('SUMMARY:Essay','SEQUENCE:2\r\nSUMMARY:New essay');
  assert.equal(parseCanvasItems(calendar(base,updated),now)[0].title,'New essay');
  assert.match(parseCanvasFeed(calendar(base,base.replace('SUMMARY:Essay','SUMMARY:Conflict')),now).issues[0].reason,/冲突版本/);
});

test('空日历有效；取消事项跳过，畸形/无UID/无标题/无时间/无限高频明确拒绝', () => {
  assert.deepEqual(parseCanvasItems(calendar(),now),[]);
  assert.deepEqual(parseCanvasItems(calendar(event(['UID:x','STATUS:CANCELLED'])),now),[]);
  assert.throws(()=>parseCanvasFeed('<html>login</html>',now),/格式无效/);
  for(const source of [calendar(base.replace('UID:assignment-1\r\n','')),calendar(base.replace('SUMMARY:Essay','SUMMARY:')),calendar(base.replace('DTSTART:20261030T143000Z\r\n','')),calendar(base.replace('SUMMARY:Essay','RRULE:FREQ=SECONDLY\r\nSUMMARY:Essay'))]) assert.equal(parseCanvasFeed(source,now).issues.length,1);
});

test('网络读取HTTPS/禁重定向/限体积，错误不泄露私有订阅URL', async () => {
  let options: RequestInit | undefined;
  const url='https://school.example/feeds/private-token.ics';
  const request = (async (_url: unknown, init:RequestInit) => { options=init;return new Response(calendar(base)); }) as typeof fetch;
  assert.equal((await fetchCanvasItems(url,request,now)).length,1);
  assert.equal(options?.redirect,'error'); assert.ok(options?.signal);
  assert.equal(normalizeCanvasUrl('webcal://school.example/feed.ics'),'https://school.example/feed.ics');
  for(const value of ['http://school.example/feed.ics','https://name:password@school.example/feed','file:///etc/passwd']) assert.throws(()=>normalizeCanvasUrl(value));
  await assert.rejects(fetchCanvasItems(url,(async()=>{throw new Error('network '+url);}) as typeof fetch), error=> !String(error).includes('private-token'));
  await assert.rejects(fetchCanvasItems(url,(async()=>new Response('x',{headers:{'content-length':String(6*1024*1024)}})) as typeof fetch),/5MB/);
  await assert.rejects(fetchCanvasItems(url,(async()=>new Response('<html>login</html>')) as typeof fetch),/有效日历|格式无效/);
});

test('订阅URL仅加密落盘/不返回renderer，token轮换保留来源，换host新来源，坏配置不覆盖', async () => {
  const directory=mkdtempSync(join(tmpdir(),'canvas-feed-test-'));
  const vault={available:async()=>true,encrypt:async(value:string)=>Buffer.from(value.split('').reverse().join('')),decrypt:async(value:Buffer)=>({result:value.toString().split('').reverse().join(''),shouldReEncrypt:false})};
  try {
    const store=new CanvasFeedStore(directory,vault),url='https://school.example/feeds/top-secret.ics';
    assert.equal(store.get().configured,false);
    const cfg=await store.save(url);
    assert.deepEqual(cfg,{configured:true,host:'school.example'});
    assert.equal(readFileSync(join(directory,'canvas-feed.json'),'utf8').includes('top-secret'),false);
    const source=(await store.forRequest()).sourceId;
    await store.save('https://school.example/feeds/new-token.ics');assert.equal((await store.forRequest()).sourceId,source);
    const reopened=new CanvasFeedStore(directory,vault);assert.equal((await reopened.forRequest()).sourceId,source);
    await store.save('https://other.example/feed.ics');assert.notEqual((await store.forRequest()).sourceId,source);
    store.clear();await store.save(url,source);assert.equal((await store.forRequest()).sourceId,source);
    writeFileSync(join(directory,'canvas-feed.json'),'broken');await assert.rejects(store.save(url),/无法读取/);assert.equal(readFileSync(join(directory,'canvas-feed.json'),'utf8'),'broken');
  } finally { rmSync(directory,{recursive:true,force:true}); }
});


test('内嵌VTIMEZONE和跨日offset正确解析，重复展开有数量与计算上限', () => {
  const timezone=['BEGIN:VTIMEZONE','TZID:CustomSchool','BEGIN:STANDARD','DTSTART:19700101T000000','TZOFFSETFROM:+0230','TZOFFSETTO:+0230','END:STANDARD','END:VTIMEZONE'].join('\r\n');
  const source=calendar(timezone,event(['UID:custom','DTSTART;TZID=CustomSchool:20261030T003000','SUMMARY:School zone']));
  const due=parseCanvasItems(source,now)[0].due;
  assert.equal(due.mode==='value' && due.value.kind==='minute' && due.value.at,'2026-10-29T22:00:00.000Z');
  assert.match(parseCanvasFeed(calendar(event(['UID:ancient','DTSTART:19000101T100000Z','RRULE:FREQ=DAILY','SUMMARY:Ancient'])),now).issues[0].reason,/范围过大/);
});


test('完整长说明独立保留，危险链接和HTML不执行，单事项错误不阻止其他事项', () => {
  const description = 'Tokenizer project requirements. '.repeat(800);
  const source = calendar(base.replace('DESCRIPTION:<p>Read &amp; write</p><script>bad()</script>',`DESCRIPTION:${description}`),
    event(['UID:broken','SUMMARY:Broken','DESCRIPTION:private details']),
    event(['UID:unsafe','DTSTART;VALUE=DATE:20261031','SUMMARY:Safe text','DESCRIPTION:<style>hidden</style><p>Readable</p><script>alert(1)</script>','URL:javascript:alert(1)']));
  const result = parseCanvasFeed(source,now);
  assert.equal(result.items.length,2); assert.equal(result.issues.length,1);
  assert.equal(result.items[0].description,description.trim());
  assert.equal(result.items[1].description,'Readable'); assert.equal(result.items[1].url,'');
  assert.ok(!JSON.stringify(result.issues).includes('private details'));
  assert.equal(parseCanvasItems(calendar(base.replace('URL:https://school.example/courses/1/assignments/2','URL:https://user:password@school.example/item')),now)[0].url,'');
});

test('同UID重复组原子跳过，不留下部分实例；全局体积与格式错误仍拒绝', () => {
  const master = event(['UID:daily','DTSTART:20261028T100000Z','RRULE:FREQ=DAILY;COUNT=3','SUMMARY:Daily']);
  const invalid = event(['UID:daily','RECURRENCE-ID:20261029T100000Z','DTSTART:20261029T100000Z','SUMMARY:']);
  const result = parseCanvasFeed(calendar(master,invalid,base),now);
  assert.equal(result.items.length,1); assert.equal(result.items[0].title,'Essay'); assert.equal(result.issues.length,1);
  assert.throws(()=>parseCanvasFeed('x'.repeat(5*1024*1024+1)),/5MB/);
  assert.throws(()=>parseCanvasFeed('BEGIN:VCALENDAR\r\nVERSION:2.0\r\n'),/格式无效/);
  const many = Array.from({length:5001},(_,i)=>event([`UID:item-${i}`,'DTSTART;VALUE=DATE:20261030','SUMMARY:Item']));
  assert.throws(()=>parseCanvasFeed(calendar(...many)),/5000/);
});


test('长说明重复实例共享解析内容，但持久化展开总量超过40MiB时整次拒绝而非部分成功', () => {
  const recurring = (description:string) => event(['UID:long-daily','DTSTART:20261029T100000Z','RRULE:FREQ=DAILY;COUNT=365','SUMMARY:Long daily',`DESCRIPTION:${description}`]);
  const accepted = parseCanvasFeed(calendar(recurring('x'.repeat(100000))),now);
  assert.equal(accepted.items.length,365); assert.equal(accepted.issues.length,0);
  assert.equal(accepted.items[364].description.length,100000);
  assert.throws(()=>parseCanvasFeed(calendar(base,recurring('文'.repeat(100000))),now),/40MiB/);
});


test('与持久化契约边界一致：超长链接清空，JSON展开标识超限单组跳过', () => {
  const oversizedUrl = 'https://school.example/' + 'a'.repeat(8000);
  const longUrl = event(['UID:long-url','DTSTART;VALUE=DATE:20261030','SUMMARY:URL','URL:' + oversizedUrl]);
  const oversizedKey = event(['UID:' + '"'.repeat(600),'DTSTART;VALUE=DATE:20261030','SUMMARY:Escaped UID']);
  const result = parseCanvasFeed(calendar(base,longUrl,oversizedKey),now);
  assert.equal(result.items.length,2); assert.equal(result.issues.length,1);
  assert.equal(result.items[1].url,''); assert.match(result.issues[0].reason,/标识/);
  for (const item of result.items) { assert.ok(item.key.length <= 1000); assert.ok(item.url.length <= 8000); }
});

test('错误事项也计入5000项上限：无UID、组错误及混合成功均不可超限', () => {
  const noUid = event(['SUMMARY:Missing UID']);
  assert.throws(()=>parseCanvasFeed(calendar(...Array.from({length:5001},()=>noUid)),now),/5000/);
  const invalid = Array.from({length:5000},(_,i)=>event([`UID:bad-${i}`,'SUMMARY:Missing date']));
  assert.throws(()=>parseCanvasFeed(calendar(base,...invalid),now),/5000/);
  assert.equal(parseCanvasFeed(calendar(...invalid),now).issues.length,5000);
});
