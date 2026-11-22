import { test } from 'node:test';
import assert from 'node:assert/strict';
import { EventEmitter } from 'node:events';
import { loadWindowPage, PAGE_SHOW_FALLBACK_MS } from '../src/main/window-page';

type Loader = Parameters<typeof loadWindowPage>[0];
type Mode = 'pending' | 'resolve' | 'reject';

class FakeWebContents extends EventEmitter {}

class FakeWindow extends EventEmitter {
  readonly webContents = new FakeWebContents();
  destroyed = false;
  shown = 0;
  mode: Mode = 'pending';
  private settle?: (failed: boolean) => void;

  loadFile(): Promise<void> {
    if (this.mode === 'resolve') return Promise.resolve();
    if (this.mode === 'reject') return Promise.reject(new Error('ERR_FILE_NOT_FOUND'));
    return new Promise<void>((resolve, reject) => {
      this.settle = failed => { if (failed) reject(new Error('ERR_FAILED')); else resolve(); };
    });
  }
  settleLoad(failed = false) { this.settle?.(failed); }
  isDestroyed() { return this.destroyed; }
  show() { this.shown++; }
  destroy() { this.destroyed = true; this.emit('closed'); }
  requestClose(prevent = false) {
    const event = { defaultPrevented: prevent, preventDefault() { this.defaultPrevented = true; } };
    this.emit('close', event);
  }
  asLoader() { return this as unknown as Loader; }
}

function setup(mode: Mode = 'pending') {
  const window = new FakeWindow();
  window.mode = mode;
  const reports: string[] = [];
  const loading = loadWindowPage(window.asLoader(), 'renderer/index.html', false, () => reports.push('页面加载失败'));
  return { window, reports, loading };
}

const flush = () => new Promise<void>(resolve => setImmediate(resolve));

test('页面就绪后显示窗口，且只显示一次', async () => {
  const { window } = setup();
  window.emit('ready-to-show');
  assert.equal(window.shown, 1);
  window.webContents.emit('did-finish-load');
  assert.equal(window.shown, 1);
  window.settleLoad();
  await flush();
  assert.equal(window.shown, 1);
});

test('首次绘制事件缺失时，页面加载完成也显示窗口', async () => {
  const { window } = setup();
  window.webContents.emit('did-finish-load');
  assert.equal(window.shown, 1);
});

test('首次绘制与加载完成都迟迟不来时，兜底计时器仍会显示窗口', t => {
  t.mock.timers.enable({ apis: ['setTimeout'] });
  const { window } = setup();
  assert.equal(window.shown, 0);
  t.mock.timers.tick(PAGE_SHOW_FALLBACK_MS - 1);
  assert.equal(window.shown, 0);
  t.mock.timers.tick(1);
  assert.equal(window.shown, 1);
});

test('关闭已请求且未被拦截时，加载取消不误报也不销毁', async () => {
  const { window, reports } = setup();
  window.requestClose(false);
  window.emit('ready-to-show');
  window.settleLoad(true);
  await flush();
  assert.deepEqual(reports, []);
  assert.equal(window.destroyed, false);
  assert.equal(window.shown, 0);
});

test('关闭被拦截（隐藏到托盘）后页面就绪不再把窗口弹回来', async () => {
  const { window } = setup();
  window.requestClose(true);
  window.emit('ready-to-show');
  window.webContents.emit('did-finish-load');
  assert.equal(window.shown, 0);
});

test('关闭被拦截（隐藏到托盘）后真实加载故障仍报告一次并销毁', async () => {
  const { window, reports } = setup();
  window.requestClose(true);
  window.settleLoad(true);
  await flush();
  assert.deepEqual(reports, ['页面加载失败']);
  assert.equal(window.destroyed, true);
});

test('存活窗口真实加载失败只报告一次并销毁', async () => {
  const { window, reports } = setup();
  window.settleLoad(true);
  await flush();
  assert.deepEqual(reports, ['页面加载失败']);
  assert.equal(window.destroyed, true);
});

test('正常加载完成不报告失败', async () => {
  const { window, reports, loading } = setup('resolve');
  await loading;
  assert.deepEqual(reports, []);
  assert.equal(window.destroyed, false);
});

test('销毁后的窗口不再响应就绪与兜底', t => {
  t.mock.timers.enable({ apis: ['setTimeout'] });
  const { window } = setup();
  window.destroy();
  window.emit('ready-to-show');
  window.webContents.emit('did-finish-load');
  t.mock.timers.tick(PAGE_SHOW_FALLBACK_MS);
  assert.equal(window.shown, 0);
});
