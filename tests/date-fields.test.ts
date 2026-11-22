import test from 'node:test';
import assert from 'node:assert/strict';
import {fromLocalMinute,toLocalMinute} from '../src/renderer/date-fields';
test('日期时间编辑按本机分钟往返，不擅自补日期精度',()=>{
 const prior=process.env.TZ;process.env.TZ='Asia/Shanghai';
 try{const due=fromLocalMinute('2026-11-05T14:30');assert.equal(due.at,'2026-11-05T06:30:00.000Z');assert.equal(toLocalMinute(due.at),'2026-11-05T14:30');assert.throws(()=>fromLocalMinute('2026-02-30T14:30'));assert.throws(()=>fromLocalMinute('2026-11-05'));}
 finally{if(prior===undefined)delete process.env.TZ;else process.env.TZ=prior;}
});
test('夏令时不存在或不唯一的本地分钟拒绝静默纠正',()=>{
 const prior=process.env.TZ;process.env.TZ='America/New_York';
 try{assert.throws(()=>fromLocalMinute('2026-03-08T02:30'),/不存在/);assert.throws(()=>fromLocalMinute('2026-11-01T01:30'),/歧义/);assert.equal(toLocalMinute(fromLocalMinute('2026-11-01T03:30').at),'2026-11-01T03:30');}
 finally{if(prior===undefined)delete process.env.TZ;else process.env.TZ=prior;}
});
