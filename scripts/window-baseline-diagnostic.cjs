// 受控原生基线：所有窗口同一静态页面与配置，已通过的窗口保持存活。
const { app, BrowserWindow } = require('electron');
const fs = require('node:fs');
const path = require('node:path');
const os = require('node:os');
const { widgetWindowStyle } = require('../dist-backend/main/widget-policy');
const started = Date.now();
const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'task-manager-window-baseline-'));
const logFile = path.join(directory, 'events.log');
const record = (event, details = {}) => {
  const line = JSON.stringify({ ms: Date.now() - started, event, ...details });
  fs.appendFileSync(logFile, line + '\n');
  console.log(line);
};
app.setPath('userData', directory);
app.setPath('sessionData', path.join(directory, 'session'));
app.setAppLogsPath(path.join(directory, 'logs'));
app.on('window-all-closed', () => {});
const windows = [];
let exiting = false;
function finish(code) {
  if (exiting) return;
  exiting = true;
  for (const window of windows) if (!window.isDestroyed()) window.destroy();
  record('exit', { code });
  app.exit(code);
}
const deadline = async (promise, ms, label) => {
  let timer;
  try {
    return await Promise.race([promise, new Promise((_, reject) => {
      timer = setTimeout(() => reject(Object.assign(new Error(label), { code: 'PROBE_TIMEOUT' })), ms);
    })]);
  } finally { clearTimeout(timer); }
};
record('runtime', { executable: process.execPath, electron: process.versions.electron, directory, logFile });
setTimeout(() => { record('watchdog'); finish(2); }, 40000).unref();
app.whenReady().then(async () => {
  const fixture = path.join(directory, 'probe.html');
  fs.writeFileSync(fixture, '<!doctype html><title>Window baseline</title><body>baseline loaded</body>');
  for (const [step, visible] of [['visible-first', true], ['visible-second', true], ['hidden-third', false], ['widget-fourth', false]]) {
    record('create', { step, visible, retainedWindows: windows.length });
    const window = new BrowserWindow({ ...(step === 'widget-fourth' ? widgetWindowStyle : {}), show: visible, x: 80, y: 80, width: 380, height: 154,
      title: 'Task Manager 窗口诊断',
      webPreferences: { sandbox: true, contextIsolation: true, nodeIntegration: false } });
    windows.push(window);
    let domReady = false;
    window.webContents.on('dom-ready', () => { domReady = true; });
    for (const name of ['did-start-loading', 'dom-ready', 'did-finish-load', 'did-stop-loading']) {
      window.webContents.on(name, () => record(name, { step }));
    }
    window.webContents.on('did-start-navigation', (_event, url) => record('did-start-navigation', { step, url }));
    window.webContents.on('did-fail-load', (_event, code, description, url, mainFrame) => record('did-fail-load', { step, code, description, url, mainFrame }));
    window.webContents.on('render-process-gone', (_event, details) => record('render-process-gone', { step, ...details }));
    for (const name of ['ready-to-show', 'show', 'close', 'closed']) window.on(name, () => record(name, { step }));
    try {
      if (step === 'widget-fourth') {
        window.setIgnoreMouseEvents(true, { forward: true });
        window.showInactive();
        record('preload-showInactive', { step, visible: window.isVisible(), domReady });
      }
      const pending = (async () => {
        await window.loadFile(fixture);
        const text = await window.webContents.executeJavaScript('document.body.textContent');
        if (text !== 'baseline loaded') throw new Error('unexpected fixture body: ' + text);
        record('verified-body', { step, text, visible: window.isVisible() });
      })();
      if (step === 'hidden-third') {
        let verified = false;
        for (const [phase, ms] of [['hidden', 3000], ['showInactive', 5000], ['show', 5000]]) {
          if (phase !== 'hidden') {
            window[phase]();
            record('transition', { step, phase, domReady, visible: window.isVisible(), loading: window.webContents.isLoading(), url: window.webContents.getURL() });
          }
          try {
            await deadline(pending, ms, phase + ' timed out after ' + ms + 'ms');
            verified = true;
            record('transition-verified', { step, phase });
            break;
          } catch (error) {
            if (error.code !== 'PROBE_TIMEOUT') throw error;
            record('phase-timeout', { step, phase, domReady, visible: window.isVisible(), loading: window.webContents.isLoading(), url: window.webContents.getURL() });
          }
        }
        if (!verified) throw new Error('same hidden window failed after showInactive and show');
      } else {
        await deadline(pending, 8000, step + ' timed out after 8000ms');
      }
    } catch (error) {
      record('step-failed', { step, message: error.message, code: error.code,
        destroyed: window.isDestroyed(),
        ...(window.isDestroyed() ? {} : { loading: window.webContents.isLoading(), url: window.webContents.getURL(), pid: window.webContents.getOSProcessId() }),
        classification: visible ? 'visible baseline failed; no widget inference' : 'hidden load timed out or failed after two live visible controls' });
      finish(1);
      return;
    }
  }
  record('complete', { verifiedWindows: windows.length });
  finish(0);
}).catch(error => { record('fatal', { message: error.message }); finish(1); });
