// Actual Windows EXE smoke: isolated profile, packaged React/preload/Python, restart.
// Run with Node 24 in CI. Uses the existing app's host-only test profile override.
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const net = require('node:net');
const assert = require('node:assert/strict');
const {spawn} = require('node:child_process');
const root = path.resolve(__dirname, '..');
function argument(name, fallback) { const i = process.argv.indexOf(name); return i < 0 ? fallback : process.argv[i + 1]; }
const appDir = path.resolve(argument('--app-dir', process.env.TM_WINDOWS_APP || path.join(root,'release-windows/win-unpacked')));
const output = path.resolve(argument('--output', path.join(root,'handover/windows-release/app')));
const exe = path.join(appDir,'Task Manager.exe');
if (process.platform !== 'win32') throw new Error('This acceptance must run on Windows with the real packaged EXE.');
assert.ok(fs.existsSync(exe), 'Packaged Task Manager.exe missing');
fs.mkdirSync(output,{recursive:true});
const profile = fs.mkdtempSync(path.join(os.tmpdir(),'tm-win-ui-'));
const dataDir = path.join(profile,'学生 data with spaces');
const report = {startedAt:new Date().toISOString(),platform:process.platform,architecture:process.arch,executable:exe,checks:[],externalAccounts:0,externalMessagesSent:0,scope:'Real packaged EXE, renderer/preload IPC, local SQLite and restart. No live model/mail provider.'};
const delay = ms => new Promise(resolve => setTimeout(resolve,ms));
let current, socket;
function check(name, condition) { report.checks.push({name,passed:Boolean(condition)}); assert.ok(condition,name); }
function freePort() { return new Promise((resolve,reject)=>{const s=net.createServer();s.once('error',reject);s.listen(0,'127.0.0.1',()=>{const port=s.address().port;s.close(()=>resolve(port));});}); }
async function connect(url) {
  const ws = new WebSocket(url);
  await new Promise((resolve,reject)=>{ws.addEventListener('open',resolve,{once:true});ws.addEventListener('error',reject,{once:true});});
  let id=0;const pending=new Map();
  ws.addEventListener('message',event=>{const value=JSON.parse(String(event.data));const item=pending.get(value.id);if(!item)return;pending.delete(value.id);clearTimeout(item.timer);value.error?item.reject(Error(value.error.message)):item.resolve(value.result);});
  ws.addEventListener('close',()=>{for(const item of pending.values()){clearTimeout(item.timer);item.reject(Error('DevTools target closed'));}pending.clear();});
  return {close:()=>ws.close(),call:(method,params={})=>new Promise((resolve,reject)=>{const current=++id;const timer=setTimeout(()=>{pending.delete(current);reject(Error('CDP timeout: '+method));},30000);pending.set(current,{resolve,reject,timer});ws.send(JSON.stringify({id:current,method,params}));})};
}
async function evaluate(expression) {
  const result = await socket.call('Runtime.evaluate',{expression,awaitPromise:true,returnByValue:true});
  if(result.exceptionDetails)throw Error(result.exceptionDetails.exception?.description||result.exceptionDetails.text);
  return result.result.value;
}
async function launch(iteration) {
  const port=await freePort();const env={...process.env,TM_TEST_DATA_DIR:dataDir,PYTHONUTF8:'1'};delete env.ELECTRON_RUN_AS_NODE;
  current=spawn(exe,[`--remote-debugging-port=${port}`,'--remote-debugging-address=127.0.0.1'],{env,stdio:['ignore','pipe','pipe'],windowsHide:false});
  const log=fs.createWriteStream(path.join(output,`startup-${iteration}.log`));current.stdout.pipe(log);current.stderr.pipe(log);current.once('close',()=>log.end());
  current.spawnError=null;current.once('error',e=>{current.spawnError=e;});
  let target;const until=Date.now()+90000;
  while(Date.now()<until){
    if(current.spawnError)throw current.spawnError;
    if(current.exitCode!==null)throw Error('App exited before UI became ready: '+current.exitCode);
    try{const response=await fetch(`http://127.0.0.1:${port}/json/list`);const items=await response.json();target=items.find(t=>t.type==='page'&&t.url.startsWith('file:')&&!t.url.includes('widget=1'));if(target)break;}catch{}
    await delay(250);
  }
  assert.ok(target,'Packaged app did not expose its renderer within 90 seconds');
  socket=await connect(target.webSocketDebuggerUrl);
  let state;
  while(Date.now()<until){
    try {
      state=await evaluate(`({heading:document.querySelector('.ag-welcome h1')?.textContent||'',body:document.body?.innerText.slice(0,4000)||'',hasIPC:!!window.agent?.getSecretary})`);
    } catch(error) {
      // The file target can appear before navigation has a default context.
      // Retry only that startup gap; actual preload/IPC exceptions still fail.
      if(!/execution context was destroyed|cannot find (?:default )?(?:execution )?context|inspected target navigated/i.test(error.message))throw error;
    }
    if(state?.hasIPC&&state.heading==='今天有什么需要处理？')break;
    await delay(250);
  }
  check(`secretary_home_visible_${iteration}`,state?.heading==='今天有什么需要处理？'&&state.hasIPC);
  const shot=await socket.call('Page.captureScreenshot',{format:'png',captureBeyondViewport:false});
  fs.writeFileSync(path.join(output,`secretary-home-${iteration}.png`),Buffer.from(shot.data,'base64'));
  return state;
}
async function stop() {
  if(!current)return;
  if(current.exitCode===null){
    // Graceful product quit closes its owned Python worker and SQLite handles.
    try{await evaluate('window.widget.quit()');}catch{}
    const until=Date.now()+12000;while(current.exitCode===null&&Date.now()<until)await delay(100);
    if(current.exitCode===null){
      await new Promise(resolve=>{const kill=spawn('taskkill.exe',['/PID',String(current.pid),'/T','/F'],{windowsHide:true});kill.once('exit',resolve);kill.once('error',resolve);});
      await delay(500);
    }
  }
  socket?.close();socket=null;current=null;
}
(async()=>{
  try{
    await launch(1);
    const home=await evaluate('window.agent.getSecretary()');check('fixed_secretary_ipc',home.ok&&home.data.kind==='secretary');
    const settings=await evaluate('window.desktop.getSettings()');
    check('isolated_empty_profile',settings.ok&&!settings.data.hasApiKey&&path.resolve(settings.data.dataDirectory).toLowerCase()===path.resolve(dataDir).toLowerCase());
    const accounts=await evaluate('window.mailWorkspace.getAccounts()');check('no_mailbox_in_distribution',accounts.ok&&accounts.data.length===0);
    const pending=await evaluate('window.agent.listPending()');check('fresh_pending_empty',pending.ok&&pending.data.length===0);
    const created=await evaluate(`window.desktop.createTodo({title:'Windows packaged restart check'})`);check('packaged_python_write',created.ok&&!!created.data.value.id);
    const taskId=created.data.value.id;
    const listed=await evaluate('window.desktop.listTodos()');check('packaged_python_read',listed.ok&&listed.data.some(t=>t.id===taskId));
    // Exercise the real sidebar route after the backend write.
    const clicked=await evaluate(`(()=>{const b=[...document.querySelectorAll('button')].find(x=>x.textContent.trim()==='日历与待办');if(!b)return false;b.click();return true;})()`);
    check('calendar_navigation_available',clicked);
    let calendar='';for(let n=0;n<50;n++){calendar=await evaluate('document.body.innerText');if(calendar.includes('Windows packaged restart check'))break;await delay(150);}
    check('saved_task_rendered_in_calendar',calendar.includes('Windows packaged restart check'));
    const calendarShot=await socket.call('Page.captureScreenshot',{format:'png',captureBeyondViewport:false});fs.writeFileSync(path.join(output,'calendar.png'),Buffer.from(calendarShot.data,'base64'));
    await stop();
    await launch(2);
    const reopenedHome=await evaluate('window.agent.getSecretary()');check('same_secretary_after_process_restart',reopenedHome.ok&&reopenedHome.data.id===home.data.id);
    const reopenedTasks=await evaluate('window.desktop.listTodos()');check('same_task_after_process_restart',reopenedTasks.ok&&reopenedTasks.data.length===1&&reopenedTasks.data[0].id===taskId);
    report.passed=true;
  }catch(error){report.passed=false;report.error=error.stack||String(error);process.exitCode=1;}
  finally{
    await stop();report.finishedAt=new Date().toISOString();fs.writeFileSync(path.join(output,'app.json'),JSON.stringify(report,null,2)+'\n');
    // Delete only this process's fresh synthetic profile, never product data.
    try{fs.rmSync(profile,{recursive:true,force:true});}catch(error){report.cleanupError=String(error);fs.writeFileSync(path.join(output,'app.json'),JSON.stringify(report,null,2)+'\n');}
  }
  console.log(JSON.stringify({passed:report.passed,checks:report.checks.length,report:path.join(output,'app.json')}));
})();
