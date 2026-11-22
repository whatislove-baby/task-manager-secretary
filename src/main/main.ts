import { attachPythonDesktop } from './python-desktop';
import { MailAutomationHost } from './mail-automation-host';
// 桌面数据与窗口入口。
import { app, BrowserWindow, ipcMain, dialog, safeStorage, screen, shell, Notification } from 'electron';
import { join, resolve } from 'node:path';
import { pathToFileURL } from 'node:url';
import { loadWindowPage } from './window-page';
import { logWindowEvent, setWindowLogDirectory } from './window-log';
import { resolveDataDirectory, prepareDataDirectory } from './storage';
import { createAppTray } from './tray';
import { ApplicationPreferencesStore } from './preferences';
import { mutationMethods, rendererAllowed, widgetBounds, moveWidgetBounds, widgetWindowStyle } from './widget-policy';
export function configureDesktopData(): string {
  // Explicit host-only test profile; never exposed through renderer IPC.
  const directory=process.env.TM_TEST_DATA_DIR
    ? resolve(process.env.TM_TEST_DATA_DIR)
    : process.platform==='darwin' && app.isPackaged
    ? join(app.getPath('appData'),'Task Manager Mac','data')
    : resolveDataDirectory({ isPackaged:app.isPackaged, executablePath:process.execPath, projectDirectory:resolve(__dirname,'../..'), portableDirectory:process.env.PORTABLE_EXECUTABLE_DIR });
  prepareDataDirectory(directory);
  // macOS packaged apps keep writable data outside the signed/read-only .app.
  app.setPath('userData',directory); app.setPath('sessionData',join(directory,'browser-cache')); app.setAppLogsPath(join(directory,'logs'));
  return directory;
}
// Development-only compatibility oracle. Production packages do not include this module.
export function attachDesktopBackend(...args: Parameters<typeof import('./legacy-desktop').attachDesktopBackend>) {
  return (require('./legacy-desktop') as typeof import('./legacy-desktop')).attachDesktopBackend(...args);
}

export function startDesktop(): void {
  let mainWindow: BrowserWindow | null = null;
  let widgetWindow: BrowserWindow | null = null;
  let widgetExpanded = false;
  let quitting = false;
  let preferences: ApplicationPreferencesStore;
  let tray: ReturnType<typeof createAppTray> | null = null;
  let backend: { dispose(): void | Promise<void>; broadcast(): void } | null = null;
  let dataDirectory: string;
  const rendererFile = resolve(__dirname, '../../dist-renderer/index.html');
  const ownUrl = pathToFileURL(rendererFile).href;
  const windows = () => [mainWindow, widgetWindow].filter((window): window is BrowserWindow => !!window && !window.isDestroyed());
  try { dataDirectory = configureDesktopData(); }
  catch (error) { dialog.showErrorBox('数据目录无法使用', (error as Error).message); app.quit(); throw error; }
  setWindowLogDirectory(join(dataDirectory, 'logs'));
  logWindowEvent('app:start', { electron: process.versions.electron, packaged: app.isPackaged, directory: dataDirectory });
  if (!app.requestSingleInstanceLock()) { logWindowEvent('app:single-instance-exit'); app.quit(); }
  else {
    function secureWindow(window: BrowserWindow, widget: boolean) {
      logWindowEvent('window:create', { name: widget ? 'widget' : 'main', bounds: window.getBounds(), visible: false });
      window.webContents.setWindowOpenHandler(() => ({ action: 'deny' }));
      const allowed = ownUrl + (widget ? '?widget=1' : '');
      window.webContents.on('will-navigate', (event, url) => { if (url !== allowed) event.preventDefault(); });
      window.webContents.on('will-prevent-unload', event => {
        const choice = dialog.showMessageBoxSync(window, { type: 'question', title: '尚有未保存内容', message: '放弃当前修改并关闭？', buttons: ['继续编辑', '放弃并关闭'], defaultId: 0, cancelId: 0 });
        if (choice === 1) event.preventDefault();
        else quitting = false;
      });
      void loadWindowPage(window, rendererFile, widget, () => {
        dialog.showErrorBox('页面加载失败', '请重新构建应用；现有待办数据未被删除。');
      });
    }
    function createMain() {
      if (mainWindow && !mainWindow.isDestroyed()) { if (mainWindow.isMinimized()) mainWindow.restore(); mainWindow.show(); mainWindow.focus(); return; }
      mainWindow = new BrowserWindow({ width: 1320, height: 900, minWidth: 760, minHeight: 600, title: 'Task Manager', backgroundColor: '#ffffff', autoHideMenuBar: true, show: false,
        webPreferences: { preload: join(__dirname, 'preload.js'), contextIsolation: true, nodeIntegration: false, sandbox: true } });
      const window = mainWindow;
      window.on('close', event => {
        if (quitting) { logWindowEvent('window:close', { name: 'main', quitting: true }); return; }
        event.preventDefault();
        const closeBehavior = preferences.get().closeBehavior;
        logWindowEvent('window:close', { name: 'main', action: 'prevented', closeBehavior });
        if (closeBehavior === 'tray') window.hide();
        else app.quit();
      });
      window.on('closed', () => { mainWindow = null; });
      secureWindow(window, false);
    }
    function createWidget() {
      if (widgetWindow && !widgetWindow.isDestroyed()) { widgetWindow.show(); return; }
      widgetExpanded = false;
      const work = screen.getPrimaryDisplay().workArea;
      const bounds = widgetBounds({ x: work.x + work.width - 400, y: work.y + 48, width: 380, height: 154 }, work, false);
      widgetWindow = new BrowserWindow({ ...bounds, title: '乌鸦待办', ...widgetWindowStyle, minWidth: bounds.width, maxWidth: bounds.width, minHeight: bounds.height, maxHeight: bounds.height, show: false,
        webPreferences: { preload: join(__dirname, 'preload.js'), contextIsolation: true, nodeIntegration: false, sandbox: true } });
      const window = widgetWindow;
      window.setIgnoreMouseEvents(true,{forward:true});
      window.setAlwaysOnTop(true, 'floating');
      window.setVisibleOnAllWorkspaces(true, { visibleOnFullScreen: true });
      window.on('closed', () => { if (widgetWindow === window) widgetWindow = null; tray?.refresh(); backend?.broadcast(); });
      secureWindow(window, true);
      tray?.refresh(); backend?.broadcast();
    }
    app.on('second-instance', () => { logWindowEvent('app:second-instance'); createMain(); });
    app.whenReady().then(() => {
      logWindowEvent('app:ready', { gpu: app.getGPUFeatureStatus().gpu_compositing, electron: process.versions.electron });
      preferences = new ApplicationPreferencesStore(dataDirectory,{
        supported: process.platform === 'win32' && app.isPackaged,
        set: enabled => app.setLoginItemSettings({openAtLogin:enabled,path:process.execPath,args:[]}),
      });
      preferences.get();
      logWindowEvent('app:preferences', { ...preferences.get() });
      for (const name of ['get','save'] as const) ipcMain.handle('preferences:'+name,(event,value) => {
        const source=windows().find(window=>event.sender===window.webContents);
        if(!source||event.senderFrame!==source.webContents.mainFrame||!rendererAllowed(event.senderFrame.url,ownUrl))return {ok:false,error:'拒绝未知页面请求。'};
        try { const data=name==='get'?preferences.get():preferences.save(value);if(name==='save')backend?.broadcast();return {ok:true,data}; }
        catch(error){return {ok:false,error:error instanceof Error?error.message:'应用设置保存失败。'};}
      });
      backend = (app.isPackaged || process.env.TM_BACKEND !== 'typescript') ? attachPythonDesktop(ipcMain, windows, dataDirectory, rendererFile, {
        automation: new MailAutomationHost(dataDirectory, app.isPackaged
          ? {python:join(process.resourcesPath,'python',process.platform==='darwin'?'bin/python3':'python.exe'),backendDirectory:join(process.resourcesPath,'backend')}
          : {python:process.env.TM_PYTHON ?? resolve(__dirname,'../../.venv',process.platform==='win32'?'Scripts/python.exe':'bin/python'),backendDirectory:resolve(__dirname,'../../backend')}),
        notifyJob: (job,acknowledge) => {if (Notification.isSupported()) {const notice=new Notification({title:'Task Manager',body:job.label+'处理结束，返回应用查看。'});notice.on('close',acknowledge);notice.on('click',()=>{acknowledge();const target=windows()[0];if(target&&!target.isDestroyed()){if(target.isMinimized())target.restore();target.show();target.focus();}});notice.show();}},
        runtime: app.isPackaged ? {python:join(process.resourcesPath,'python',process.platform==='darwin'?'bin/python3':'python.exe'),backendDirectory:join(process.resourcesPath,'backend')} : undefined,
        chooseBackup: async () => {const value=await dialog.showOpenDialog({properties:['openFile'],filters:[{name:'JSON备份',extensions:['json']}]});return value.canceled ? null : value.filePaths[0];},
        saveBackup: async () => {const value=await dialog.showSaveDialog({defaultPath:join(dataDirectory,'todo-backup.json'),filters:[{name:'JSON备份',extensions:['json']}]});return value.canceled ? null : value.filePath ?? null;},
        chooseFiles: async () => {const value=await dialog.showOpenDialog({properties:['openFile','multiSelections'],filters:[{name:'文档、表格、图片或 ZIP',extensions:['pdf','docx','csv','xlsx','txt','md','png','jpg','jpeg','webp','zip']}]});return value.canceled?null:value.filePaths;},
        saveFile: async name => {const value=await dialog.showSaveDialog({defaultPath:name});return value.canceled?null:value.filePath??null;},
        chooseText: async () => {const value=await dialog.showOpenDialog({properties:['openFile'],filters:[{name:'文字或邮件',extensions:['txt','eml']}]});return value.canceled ? null : value.filePaths[0];},
        openExternal: url => shell.openExternal(url),
        decryptLegacy: async cipher => (await safeStorage.decryptStringAsync(cipher)).result,
      }) : attachDesktopBackend(windows, dataDirectory, rendererFile);
      const handlers = {
        getState: () => ({ enabled: !!widgetWindow && !widgetWindow.isDestroyed() }),
        setEnabled: (enabled: unknown) => { if (typeof enabled !== 'boolean') throw new Error('无效悬浮窗状态'); if (enabled) createWidget(); else widgetWindow?.close(); },
        setExpanded: (expanded: unknown) => {
          if (typeof expanded !== 'boolean') throw new Error('无效悬浮窗尺寸');
          if (!widgetWindow || widgetWindow.isDestroyed()) return;
          const bounds = widgetWindow.getBounds();
          const work = screen.getDisplayNearestPoint({ x: bounds.x, y: bounds.y }).workArea;
          widgetExpanded = expanded;
          const target = widgetBounds(bounds, work, expanded);
          widgetWindow.setMinimumSize(0, 0); widgetWindow.setMaximumSize(0, 0);
          widgetWindow.setBounds(target, false);
          widgetWindow.setMinimumSize(target.width, target.height); widgetWindow.setMaximumSize(target.width, target.height);
        },
        setPointerPassthrough: (ignore:unknown) => { if(typeof ignore!=='boolean')throw new Error('无效点击穿透状态');widgetWindow?.setIgnoreMouseEvents(ignore,{forward:true}); },
        moveBy: (dx: unknown, dy: unknown) => {
          if (!widgetWindow || widgetWindow.isDestroyed()) return;
          const current = widgetWindow.getBounds();
          if (typeof dx !== 'number' || typeof dy !== 'number' || !Number.isFinite(dx) || !Number.isFinite(dy)) throw new Error('无效窗口位移');
          const work = screen.getDisplayNearestPoint({ x: Math.round(current.x + current.width / 2 + dx), y: Math.round(current.y + current.height / 2 + dy) }).workArea;
          // 不复用getBounds里的width/height：Windows原生边框回报偏差可能累计。
          const canonical = widgetBounds(current, work, widgetExpanded);
          const position = moveWidgetBounds(canonical, work, dx, dy);
          widgetWindow.setBounds(position, false);
        },
        close: () => { widgetWindow?.close(); },
        showMain: createMain,
        minimizeToTray: () => { mainWindow?.hide(); },
        quit: () => { app.quit(); },
      };
      for (const [name, action] of Object.entries(handlers)) ipcMain.handle('widget:' + name, (event, ...args) => {
        const source = windows().find(window => event.sender === window.webContents);
        if (!source || event.senderFrame !== source.webContents.mainFrame || !rendererAllowed(event.senderFrame.url, ownUrl)) throw new Error('拒绝未知页面请求。');
        if ((name === 'setExpanded' || name === 'moveBy' || name === 'setPointerPassthrough') && source !== widgetWindow) throw new Error('仅悬浮窗可调整自身尺寸。');
        return (action as (...values: unknown[]) => unknown)(...args);
      });
      tray = createAppTray({
        iconPath: resolve(__dirname, '../../dist-renderer/art/raven-pencil.png'),
        showMain: createMain,
        toggleWidget: () => { if (widgetWindow && !widgetWindow.isDestroyed()) widgetWindow.close(); else createWidget(); },
        isWidgetEnabled: () => !!widgetWindow && !widgetWindow.isDestroyed(),
        quit: () => app.quit(),
      });
      createMain();
      if (preferences.get().widgetOnLaunch) createWidget();
    }).catch(error => { logWindowEvent('app:start-failed', { message: (error as Error).message }); dialog.showErrorBox('启动失败', (error as Error).message); app.quit(); });
    app.on('activate', () => { if (app.isReady()) createMain(); });
    app.on('before-quit', () => { logWindowEvent('app:before-quit'); quitting = true; });
    app.on('will-quit', (event) => { logWindowEvent('app:will-quit'); ipcMain.removeHandler('preferences:get'); ipcMain.removeHandler('preferences:save'); tray?.destroy(); tray = null; const closing=backend; backend=null; const disposal=closing?.dispose(); if (disposal && typeof disposal.then==='function') {event?.preventDefault();void disposal.finally(()=>app.quit());} });
    app.on('window-all-closed', () => { logWindowEvent('app:window-all-closed', { quitting }); if (quitting) app.quit(); });
  }
}

// Electron 用 default_app 加载主脚本（`electron .`）时 require.main 指向内置模块而不是本文件，
// 只看 require.main 会让开发启动静默空转；这里接受“没有父模块”的入口形态。
if (!module.parent || require.main === module) startDesktop();
