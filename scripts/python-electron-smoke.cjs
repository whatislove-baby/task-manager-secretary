const {app, BrowserWindow, ipcMain} = require('electron');
const fs = require('node:fs'), os = require('node:os'), path = require('node:path'), assert = require('node:assert/strict');
const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'tm-python-electron-'));
const root = path.resolve(__dirname, '..');
app.setPath('userData', directory);
app.setPath('sessionData', path.join(directory, 'browser-cache'));
app.setAppLogsPath(path.join(directory, 'logs'));
app.on('window-all-closed', () => {});
let backend;
const windows = [];
const timeout = setTimeout(() => {console.error('PYTHON_DESKTOP_SMOKE_TIMEOUT');app.exit(2);}, 30000);
app.whenReady().then(async () => {
  try {
    const {attachPythonDesktop} = require('../dist-backend/main/python-desktop');
    const rendererFile = path.join(root,'dist-renderer/index.html');
    backend = attachPythonDesktop(ipcMain, () => windows, directory, rendererFile, process.env.TM_SMOKE_RUNTIME ? {runtime:{python:path.join(process.env.TM_SMOKE_RUNTIME,process.platform==='darwin'?'bin/python3':'python.exe'),backendDirectory:path.join(root,'backend')}} : {});
    ipcMain.handle('widget:getState', () => ({enabled:true}));
    ipcMain.handle('widget:setPointerPassthrough', () => {});
    ipcMain.handle('widget:setExpanded', () => {});
    for (const widget of [false, true]) {
      const window = new BrowserWindow({show:false,width:1000,height:700,webPreferences:{preload:path.join(root,'dist-backend/main/preload.js'),sandbox:true,contextIsolation:true,nodeIntegration:false}});
      windows.push(window);
      await window.loadFile(rendererFile, widget ? {query:{widget:'1'}} : {});
    }
    const created = await windows[0].webContents.executeJavaScript(`window.desktop.createTodo({title:'Python desktop smoke'})`);
    assert.equal(created.ok, true, created.error);
    const listed = await windows[1].webContents.executeJavaScript('window.desktop.listTodos()');
    assert.equal(listed.ok, true, listed.error);
    assert.equal(listed.data[0].title, 'Python desktop smoke');
    const updated = await windows[1].webContents.executeJavaScript(`window.desktop.setCompleted(${JSON.stringify(created.data.value.id)},true,${created.data.value.revision})`);
    assert.equal(updated.ok, true, updated.error);
    const fromMain = await windows[0].webContents.executeJavaScript('window.desktop.listTodos()');
    assert.equal(fromMain.data[0].completed, true);
    const unsupported = await windows[0].webContents.executeJavaScript('window.desktop.extract("smoke")');
    assert.equal(unsupported.ok, false);
    const composeMethods = await windows[0].webContents.executeJavaScript('Object.keys(window.mailCompose).sort()');
    assert.deepEqual(composeMethods, ['generate','getConfig','listRecent','prepare','saveConfig','send']);
    const outgoing = await windows[0].webContents.executeJavaScript('window.mailCompose.listRecent()');
    assert.equal(outgoing.ok, true, outgoing.error);
    assert.deepEqual(outgoing.data, []);
    const automationMethods = await windows[0].webContents.executeJavaScript('Object.keys(window.mailAutomation).sort()');
    assert.deepEqual(automationMethods, ['correctCategory','getBackgroundStatus','getConfig','getStatus','listResults','runNow','saveConfig']);
    const automationConfig = await windows[0].webContents.executeJavaScript('window.mailAutomation.getConfig()');
    assert.equal(automationConfig.ok, true, automationConfig.error);
    assert.equal(automationConfig.data.enabled, false);
    assert.deepEqual(automationConfig.data.times, ['09:00','18:00']);
    const automationResults = await windows[0].webContents.executeJavaScript('window.mailAutomation.listResults({})');
    assert.equal(automationResults.ok, true, automationResults.error);
    assert.equal(automationResults.data.total, 0);
    const noAutomationHost = await windows[0].webContents.executeJavaScript('window.mailAutomation.runNow()');
    assert.equal(noAutomationHost.ok, false, 'An unconfigured test environment must not launch a scheduler');
    const noMailKey = await windows[0].webContents.executeJavaScript(`window.mailCompose.generate({instructions:'合成测试，请拟一封确认邮件',subject:'',body:'',target:{baseUrl:'https://example.com/v1',model:'synthetic'}})`);
    assert.equal(noMailKey.ok, false);
    const binding=await windows[0].webContents.executeJavaScript("window.mailWorkspace.addAccount('imap')");
    assert.equal(binding.ok,true,binding.error);
    const presets=await windows[0].webContents.executeJavaScript(`window.mailWorkspace.listPresets(${JSON.stringify(binding.data.id)})`);
    assert.equal(presets.ok,true,presets.error);assert.equal(presets.data.length,1);
    const background=await windows[0].webContents.executeJavaScript("window.mailOperations.startImport({bindingIds:['missing-smoke-binding'],onlyNew:true})");
    assert.equal(background.ok,true,background.error);
    const result=await windows[0].webContents.executeJavaScript(`(async()=>{const end=Date.now()+15000;while(Date.now()<end){const r=await window.mailOperations.getJob(${JSON.stringify(background.data.id)});if(!r.ok||['completed','failed'].includes(r.data.state))return r;await new Promise(done=>setTimeout(done,100));}throw Error('Worker timeout');})()`);
    assert.equal(result.ok,true,result.error);assert.equal(result.data.state,'completed');assert.equal(result.data.report.failed,1);
    console.log('PYTHON_DESKTOP_SMOKE_PASS: actual React pages, preload IPC, Python writes, shared state across two windows, unconfigured AI rejected; composition, automation contract/defaults, presets and owned background worker verified');
    process.exitCode = 0;
  } catch (error) {console.error('PYTHON_DESKTOP_SMOKE_FAIL:', error.message);process.exitCode = 1;}
  finally {
    windows.forEach(window => {if (!window.isDestroyed()) window.destroy();});
    await backend?.dispose();
    clearTimeout(timeout);
    // Remove only the fresh temporary test directory, after both windows and Python close.
    if (path.dirname(directory) === os.tmpdir() && path.basename(directory).startsWith('tm-python-electron-')) {
      try {fs.rmSync(directory,{recursive:true,force:true});} catch {console.log('Test cache retained:',directory);}
    }
    app.exit(process.exitCode || 0);
  }
}).catch(error => {console.error(error.message);app.exit(1);});
