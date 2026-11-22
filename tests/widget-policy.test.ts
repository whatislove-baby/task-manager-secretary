import { test } from 'node:test';
import assert from 'node:assert/strict';
import { rendererAllowed, widgetBounds, mutationMethods, moveWidgetBounds } from '../src/main/widget-policy';

test('只有主页面与固定悬浮页URL可信，路径近似和任意参数被拒绝', () => {
  const base = 'file:///D:/app/index.html';
  assert.equal(rendererAllowed(base, base), true);
  assert.equal(rendererAllowed(base + '?widget=1', base), true);
  for (const url of [base + '?widget=2', base + '?widget=1&extra=1', base + '#widget=1', base + '/other', 'https://example.org/']) assert.equal(rendererAllowed(url, base), false);
});

test('悬浮窗展开收起约束在当前屏幕工作区，支持负坐标与小屏幕', () => {
  const work = {x:-1280,y:20,width:1280,height:700};
  assert.deepEqual(widgetBounds({x:-100,y:660,width:480,height:180}, work, true), {x:-380,y:160,width:380,height:560});
  assert.deepEqual(widgetBounds({x:-1400,y:0,width:480,height:650}, work, false), {x:-1280,y:20,width:380,height:154});
  assert.deepEqual(widgetBounds({x:900,y:900,width:480,height:180}, {x:0,y:0,width:320,height:300}, true), {x:0,y:0,width:320,height:300});
});

test('仅写操作广播跨窗口更新，读取和AI提取不产生修改事件', () => {
  for (const name of ['createTodo','editTodo','setCompleted','setDayOrder','trashTodo','restoreBatch','saveDrafts','importBackup'] as const) assert.equal(mutationMethods.has(name), true);
  for (const name of ['listTodos','getDayOrder','effectiveDue','getSettings','extract','exportBackup'] as const) assert.equal(mutationMethods.has(name), false);
});

test('图案拖动按屏幕位移移动且不改变尺寸，拒绝异常并约束屏幕', () => {
  const current = {x:100,y:80,width:380,height:154}, work = {x:0,y:0,width:1000,height:700};
  assert.deepEqual(moveWidgetBounds(current,work,25,-10),{...current,x:125,y:70});
  assert.deepEqual(moveWidgetBounds(current,work,999,999),{...current,x:620,y:546});
  for(const value of [NaN,Infinity,'5',10001]) assert.throws(()=>moveWidgetBounds(current,work,value,0));
});
