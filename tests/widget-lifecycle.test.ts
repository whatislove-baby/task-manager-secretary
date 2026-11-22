import { attachPythonDesktop } from '../src/main/python-desktop';
import { MailAutomationHost } from '../src/main/mail-automation-host';
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { EventEmitter } from 'node:events';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { pathToFileURL } from 'node:url';
import { createRequire } from 'node:module';
import { runInNewContext } from 'node:vm';
import ts from 'typescript';
import * as policy from '../src/main/widget-policy';
import { loadWindowPage } from '../src/main/window-page';

/** Execute the production Electron entry with window/event adapters, without starting Electron. */
async function harness(deferLoads = false) {
  const handlers = new Map<string, (...args: any[]) => any>();
  const registrations: string[] = [];
  const windows: FakeWindow[] = [];
  const state = { repositories: 0, closes: 0, writes: 0, quits: 0, destroys: 0, allowDiscard: false, errors: [] as string[], windowLogs: [] as string[] };
  const app = new EventEmitter() as EventEmitter & Record<string, any>;
  let quitting = false;
  app.isPackaged = false; app.isReady = () => true;
  app.getGPUFeatureStatus = () => ({ gpu_compositing: 'test-only' });
  app.setPath = app.setAppLogsPath = () => {};
  app.requestSingleInstanceLock = () => true;
  app.whenReady = () => Promise.resolve();
  app.quit = () => {
    if (quitting) return;
    state.quits++; quitting = true; app.emit('before-quit');
    windows.filter(window => !window.dead).forEach(window => window.close());
    if (windows.every(window => window.dead)) app.emit('will-quit');
    quitting = false;
  };
  class FakeWindow extends EventEmitter {
    dead = false; dirty = false; shown = false;
    webContents = Object.assign(new EventEmitter(), {
      mainFrame: { url: '' },
      send: (_channel: string) => {},
      setWindowOpenHandler: (_action: unknown) => {},
    });
    bounds: any;
    constructor(public options: any) { super(); this.bounds = options; windows.push(this); }
    static getFocusedWindow() { return windows.find(window => !window.dead) ?? null; }
    rejectLoad?: (error: Error) => void;
    loadFile(file: string, options: any) { this.webContents.mainFrame.url = pathToFileURL(file).href + (options.query ? '?widget=1' : ''); return deferLoads ? new Promise<void>((_resolve, reject) => { this.rejectLoad = reject; }) : Promise.resolve(); }
    isDestroyed() { return this.dead; }
    isMinimized() { return false; }
    show() { this.shown = true; }
    hide() { this.shown = false; }
    focus() {} restore() {} setAlwaysOnTop() {} setVisibleOnAllWorkspaces() {}
    ignoresMouse=false; forwardsMouse=false;
    setIgnoreMouseEvents(ignore:boolean,options:{forward:boolean}) {this.ignoresMouse=ignore;this.forwardsMouse=options.forward;}
    getBounds() { return this.bounds; }
    setMinimumSize(_w: number, _h: number) {}
    setMaximumSize(_w: number, _h: number) {}
    setBounds(value: any) { this.bounds = value; }
    setPosition(x: number, y: number) { this.bounds = { ...this.bounds, x, y }; }
    close() {
      if (this.dead) return;
      const event={defaultPrevented:false,preventDefault(){this.defaultPrevented=true;}};this.emit('close',event);if(event.defaultPrevented)return;
      if (this.dirty) {
        let discarded = false;
        this.webContents.emit('will-prevent-unload', { preventDefault() { discarded = true; } });
        if (!discarded) return;
      }
      this.dead = true; this.emit('closed');
      if (windows.every(window => window.dead)) app.emit('window-all-closed');
    }
    destroy() { state.destroys++; this.dead = true; this.emit('closed'); }
  }
  class Repository {
    closed = false;
    constructor() { state.repositories++; }
    close() { this.closed = true; state.closes++; }
  }
  const nativeRequire = createRequire(resolve('package.json'));
  const module = { exports: {} };
  const requireAdapter = Object.assign((name: string) => {
    if (name === './python-desktop') return {attachPythonDesktop};
    if (name === './mail-automation-host') return {MailAutomationHost};
    if (name === './legacy-desktop') {
      const legacyModule={exports:{}};
      const code=ts.transpileModule(readFileSync(resolve('src/main/legacy-desktop.ts'),'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022,esModuleInterop:true}}).outputText;
      runInNewContext(code,{require:requireAdapter,module:legacyModule,exports:legacyModule.exports,__dirname:resolve('dist-backend/main'),fetch,process:{env:{},versions:{}},console});
      return legacyModule.exports;
    }
    if (name === 'electron') return {
      app, BrowserWindow: FakeWindow,
      ipcMain: { handle(channel: string, action: (...args: any[]) => any) { assert.ok(!handlers.has(channel), 'handler must register once: ' + channel); handlers.set(channel, action); registrations.push(channel); }, removeHandler(channel: string) { handlers.delete(channel); } },
      dialog: { showMessageBoxSync: () => state.allowDiscard ? 1 : 0, showErrorBox: (title: string, _message: string) => { state.errors.push(title); } }, safeStorage: {},
      screen: { getPrimaryDisplay: () => ({ workArea: {x:0,y:0,width:1920,height:1080} }), getDisplayNearestPoint: () => ({ workArea: {x:0,y:0,width:1920,height:1080} }) },
    };
    if (name === './mail-operations') return {MailOperations:class {api={listJobs:async()=>({ok:true,data:[]})};close(){}}};
    if (name === './mail-workspace') return {MailAccountManager:class {},MailWorkspaceStore:class {close(){}},createMailWorkspaceBase:()=>({})};
    if (name === './mail-analysis-service') return {createMailAnalysisService:()=>({getAccounts:async()=>({ok:true,data:[]})})};
    if (name === './preferences') return { ApplicationPreferencesStore: class { value={launchAtLogin:false,widgetOnLaunch:false,closeBehavior:'tray',startupSupported:false}; get(){return this.value;} save(value:any){this.value={...this.value,...value};return this.value;} } };
    if (name === './imap-auth') return { ImapAuth: class {} };
    if (name === './imap-mailbox') return { ImapMailbox: class {} };
    if (name === './mail-connections') return { MailConnections: class {} };
    if (name === './outlook-auth') return { OutlookAuth: class {} };
    if (name === './outlook-graph') return { OutlookGraph: class {} };
    if (name === './mail-service') return { createMailService: () => ({getState:async()=>({ok:true,data:{account:null}})}) };
    if (name === './canvas') return { CanvasFeedStore: class {} };
    if (name === './tray') return { createAppTray: () => ({ refresh() {}, destroy() {} }) };
    if (name === './repository') return { TaskRepository: Repository };
    if (name === './settings') return { SettingsStore: class {} };
    if (name === './storage') return { resolveDataDirectory: () => 'test-only-data', prepareDataDirectory: () => {} };
    if (name === './widget-policy') return policy;
    if (name === './window-page') return { loadWindowPage };
    if (name === './window-log') return { logWindowEvent: (event: string) => { state.windowLogs.push(event); }, setWindowLogDirectory: () => {}, windowLogPath: () => null };
    if (name === './service') return { createBackendService: (repository: Repository) => ({
      listTodos: async () => { assert.equal(repository.closed, false); return {ok:true,data:[{id:'fixture',completed:state.writes > 0}]}; },
      setCompleted: async () => { assert.equal(repository.closed, false); state.writes++; return {ok:true,data:{value:'fixture',warnings:[]}}; },
    }) };
    return nativeRequire(name);
  }, { main: module });
  const source = readFileSync(resolve('src/main/main.ts'), 'utf8');
  const compiled = ts.transpileModule(source, { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS, esModuleInterop: true } }).outputText;
  runInNewContext(compiled, { require: requireAdapter, module, exports: module.exports, __dirname: resolve('dist-backend/main'), fetch, process: { execPath: 'test-only.exe', env: {TM_BACKEND:'typescript'}, versions: { electron: 'test-only', node: 'test-only' } }, console });
  await new Promise<void>(done => setImmediate(done));
  function event(window: FakeWindow) { return { sender: window.webContents, senderFrame: window.webContents.mainFrame }; }
  function invoke(window: FakeWindow, channel: string, ...args: any[]) { const handler = handlers.get(channel); assert.ok(handler, channel); return handler(event(window), ...args); }
  return { app, state, windows, handlers, registrations, invoke };
}

test('关闭到托盘保留主窗与悬浮窗，共享服务直到明确退出', async () => {
  const h = await harness(); const main = h.windows[0];
  await h.invoke(main, 'widget:setEnabled', true); const widget = h.windows[1];
  const registrations = [...h.registrations];
  assert.equal(h.state.repositories, 1);
  main.close(); assert.equal(h.state.closes, 0); assert.equal(h.state.quits, 0);
  assert.equal((await h.invoke(widget, 'backend:listTodos')).ok, true);
  await h.invoke(widget, 'backend:setCompleted', 'fixture', true, 1);
  assert.equal((await h.invoke(widget, 'backend:listTodos')).data[0].completed, true);
  await h.invoke(widget, 'widget:showMain'); const reopened = h.windows[0];
  assert.equal(reopened.dead, false); assert.equal(h.state.repositories, 1);
  assert.deepEqual(h.registrations, registrations);
  reopened.close(); assert.equal(h.state.closes, 0);
  await h.invoke(widget, 'widget:close');
  assert.equal(h.state.quits, 0); assert.equal(h.state.closes, 0);
  await h.invoke(main,'widget:quit');
  assert.equal(h.state.quits, 1); assert.equal(h.state.closes, 1);
  assert.equal(h.handlers.has('backend:listTodos'), false);
});

test('取消未保存内容关闭及退出时保留窗口和仓储，不调用destroy', async () => {
  const h = await harness(); const main = h.windows[0]; main.dirty = true;
  await h.invoke(main, 'widget:setEnabled', true); const widget = h.windows[1];
  await h.invoke(main,'preferences:save',{launchAtLogin:false,widgetOnLaunch:false,closeBehavior:'quit'});
  main.close(); assert.equal(main.dead, false); assert.equal(h.state.closes, 0);
  await h.invoke(main, 'widget:quit');
  assert.equal(main.dead, false); assert.equal(h.state.closes, 0); assert.equal(h.state.destroys, 0);
  assert.equal((await h.invoke(main, 'backend:listTodos')).ok, true);
  h.state.allowDiscard = true;
  main.close(); assert.equal(h.state.closes, 1); assert.equal(h.state.destroys, 0);
});

test('不可信子frame和已经关闭窗口无法调用共享后端', async () => {
  const h = await harness(); const main = h.windows[0];
  await h.invoke(main, 'widget:setEnabled', true);
  const handler = h.handlers.get('backend:setCompleted')!;
  const forgedFrame = {url:main.webContents.mainFrame.url};
  assert.equal((await handler({sender:main.webContents,senderFrame:forgedFrame})).ok, false);
  main.destroy();
  assert.equal((await handler({sender:main.webContents,senderFrame:main.webContents.mainFrame})).ok, false);
  assert.equal(h.state.writes, 0);
});


test('只有悬浮窗可请求位移，图案拖动调用实际窗口边界处理', async () => {
  const h = await harness(); const main = h.windows[0];
  await h.invoke(main, 'widget:setEnabled', true); const widget = h.windows[1];
  assert.throws(() => h.invoke(main, 'widget:moveBy', 10, 10));
  assert.equal(widget.options.thickFrame, false);
  assert.equal(widget.options.roundedCorners, false);
  const before = { ...widget.bounds };
  await h.invoke(widget, 'widget:moveBy', -20, 15);
  assert.equal(widget.bounds.x, before.x - 20); assert.equal(widget.bounds.y, before.y + 15);
  assert.equal(widget.bounds.width, before.width); assert.equal(widget.bounds.height, before.height);
  assert.throws(() => h.invoke(widget, 'widget:moveBy', NaN, 0));
  for (const expanded of [false, true]) {
    await h.invoke(widget, 'widget:setExpanded', expanded);
    const size = { width: widget.bounds.width, height: widget.bounds.height };
    // 模拟原生getBounds报告不断膨胀，输出仍必须采用模式的固定尺寸。
    const actualGetBounds = widget.getBounds.bind(widget);
    widget.getBounds = () => ({ ...actualGetBounds(), width: actualGetBounds().width + 16, height: actualGetBounds().height + 8 });
    for (let i = 0; i < 100; i++) await h.invoke(widget, 'widget:moveBy', -2, 3);
    assert.equal(widget.bounds.width, size.width); assert.equal(widget.bounds.height, size.height);
    widget.getBounds = actualGetBounds;
  }
});


test('主窗口最小化保持正常状态，主动托盘隐藏保持服务存活，可重新打开且正常退出', async () => {
  const h = await harness(); const main = h.windows[0]; main.show();
  main.emit('minimize'); assert.equal(main.shown, true); assert.equal(main.dead, false);
  await h.invoke(main,'widget:showMain'); assert.equal(main.shown, true);
  await h.invoke(main,'widget:minimizeToTray'); assert.equal(main.shown, false);
  assert.equal((await h.invoke(main,'backend:listTodos')).ok,true);
  await h.invoke(main,'widget:showMain'); assert.equal(h.state.repositories,1);
  await h.invoke(main,'widget:quit'); assert.equal(h.state.closes,1);
});


test('邮件IPC仅允许已知窗口主frame，关闭最后窗口后移除邮件接口',async()=>{
 const h=await harness(),main=h.windows[0];
 assert.equal((await h.invoke(main,'mail:getState')).ok,true);
 const handler=h.handlers.get('mail:getState')!;
 assert.equal((await handler({sender:main.webContents,senderFrame:{url:main.webContents.mainFrame.url}})).ok,false);
 await h.invoke(main,'widget:quit');assert.equal(h.handlers.has('mail:getState'),false);
});


test('关闭到托盘不询问未保存内容，切换退出模式后关闭会退出全部窗口',async()=>{
 const h=await harness(),main=h.windows[0];main.show();main.dirty=true;
 main.close();assert.equal(main.dead,false);assert.equal(main.shown,false);assert.equal(h.state.quits,0);
 main.dirty=false;await h.invoke(main,'widget:setEnabled',true);
 const result=await h.invoke(main,'preferences:save',{launchAtLogin:false,widgetOnLaunch:false,closeBehavior:'quit'});assert.equal(result.ok,true);
 main.close();assert.ok(h.windows.every(window=>window.dead));assert.equal(h.state.closes,1);
});


test('悬浮窗初始透传，只有悬浮窗可按实际图案命中切换，始终转发移动供重新进入',async()=>{
 const h=await harness(),main=h.windows[0];await h.invoke(main,'widget:setEnabled',true);const widget=h.windows[1];
 assert.equal(widget.ignoresMouse,true);assert.equal(widget.forwardsMouse,true);
 assert.throws(()=>h.invoke(main,'widget:setPointerPassthrough',false));
 await h.invoke(widget,'widget:setPointerPassthrough',false);assert.equal(widget.ignoresMouse,false);
 await h.invoke(widget,'widget:setPointerPassthrough',true);assert.equal(widget.ignoresMouse,true);assert.equal(widget.forwardsMouse,true);
 assert.throws(()=>h.invoke(widget,'widget:setPointerPassthrough','false'));
});


test('连续开关时旧悬浮窗加载取消不弹错误，也不销毁或显示新窗口', async () => {
  const h = await harness(true), main = h.windows[0];
  for (let i = 0; i < 12; i++) {
    await h.invoke(main, 'widget:setEnabled', true);
    const old = h.windows.at(-1)!;
    await h.invoke(main, 'widget:setEnabled', false);
    await h.invoke(main, 'widget:setEnabled', true);
    const current = h.windows.at(-1)!;
    old.rejectLoad!(Object.assign(new Error('ERR_ABORTED'), { code: 'ERR_ABORTED', errno: -3 }));
    old.emit('ready-to-show');
    await new Promise<void>(done => setImmediate(done));
    assert.equal(old.shown, false);
    assert.equal(current.dead, false);
    assert.equal((await h.invoke(main, 'widget:getState')).enabled, true);
    await h.invoke(main, 'widget:setEnabled', false);
    current.rejectLoad!(new Error('ERR_FAILED'));
    await new Promise<void>(done => setImmediate(done));
  }
  assert.deepEqual(h.state.errors, []);
  assert.equal(h.state.destroys, 0);
  assert.equal((await h.invoke(main, 'backend:listTodos')).ok, true);
});

test('仍存活的窗口加载失败仍报告一次，销毁后可重新开启悬浮窗', async () => {
  const h = await harness(true), main = h.windows[0];
  await h.invoke(main, 'widget:setEnabled', true);
  const failed = h.windows[1];
  failed.rejectLoad!(new Error('ERR_FILE_NOT_FOUND'));
  await new Promise<void>(done => setImmediate(done));
  assert.deepEqual(h.state.errors, ['页面加载失败']);
  assert.equal(failed.dead, true);
  assert.equal(h.state.destroys, 1);
  assert.equal((await h.invoke(main, 'widget:getState')).enabled, false);
  await h.invoke(main, 'widget:setEnabled', true);
  assert.equal(h.windows[2].dead, false);
  assert.equal((await h.invoke(main, 'backend:listTodos')).ok, true);
});

test('退出应用取消待加载页面时不弹页面失败提示', async () => {
  const h = await harness(true), main = h.windows[0];
  await h.invoke(main, 'widget:setEnabled', true);
  await h.invoke(main, 'widget:quit');
  for (const window of h.windows) window.rejectLoad!(new Error('ERR_ABORTED'));
  await new Promise<void>(done => setImmediate(done));
  assert.deepEqual(h.state.errors, []);
  assert.equal(h.state.destroys, 0);
  assert.equal(h.state.closes, 1);
});


test('原生关闭先取消加载后触发closed时不误报也不强制销毁', async () => {
  const h = await harness(true), main = h.windows[0];
  await h.invoke(main, 'widget:setEnabled', true);
  const widget = h.windows[1];
  // 实测Electron顺序：close → loadFile拒绝（isDestroyed=false）→ closed。
  widget.emit('close', { defaultPrevented: false, preventDefault() { this.defaultPrevented = true; } });
  widget.rejectLoad!(Object.assign(new Error('ERR_FAILED'), { code: 'ERR_FAILED', errno: -2 }));
  await new Promise<void>(done => setImmediate(done));
  assert.deepEqual(h.state.errors, []);
  assert.equal(h.state.destroys, 0);
  widget.emit('ready-to-show');
  assert.equal(widget.shown, false);
  widget.dead = true;
  widget.emit('closed');
  assert.equal((await h.invoke(main, 'widget:getState')).enabled, false);
});

test('主窗关闭被阻止并隐藏到托盘后真实加载错误仍报告', async () => {
  const h = await harness(true), main = h.windows[0];
  main.close();
  main.rejectLoad!(Object.assign(new Error('ERR_FAILED'), { code: 'ERR_FAILED', errno: -2 }));
  await new Promise<void>(done => setImmediate(done));
  assert.deepEqual(h.state.errors, ['页面加载失败']);
  assert.equal(h.state.destroys, 1);
});

test('较晚的close监听器阻止关闭后仍报告加载故障', async () => {
  const h = await harness(true), main = h.windows[0];
  await h.invoke(main, 'widget:setEnabled', true);
  const widget = h.windows[1];
  widget.on('close', event => event.preventDefault());
  widget.close();
  widget.rejectLoad!(new Error('ERR_FILE_NOT_FOUND'));
  await new Promise<void>(done => setImmediate(done));
  assert.deepEqual(h.state.errors, ['页面加载失败']);
  assert.equal(h.state.destroys, 1);
});
