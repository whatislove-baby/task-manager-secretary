// 原生可见性回归：独立空数据，不截图、不移动鼠标；测试窗完成后自动关闭。
const {app,BrowserWindow,ipcMain}=require('electron');
const fs=require('node:fs'),path=require('node:path'),os=require('node:os'),assert=require('node:assert/strict');
const {loadWindowPage}=require('../dist-backend/main/window-page');
const {widgetWindowStyle}=require('../dist-backend/main/widget-policy');
const {TaskRepository}=require('../dist-backend/main/repository');
const root=path.resolve(__dirname,'..'),dir=fs.mkdtempSync(path.join(os.tmpdir(),'task-manager-widget-smoke-'));
console.log('SMOKE_TEMP_DIRECTORY='+dir);
app.setPath('userData',dir);app.setPath('sessionData',path.join(dir,'session'));app.setAppLogsPath(path.join(dir,'logs'));
app.on('window-all-closed',()=>{});
const deadline=(promise,ms,label)=>{let timer;return Promise.race([promise,new Promise((_,reject)=>{timer=setTimeout(()=>reject(new Error(label)),ms);})]).finally(()=>clearTimeout(timer));};
// 页面加载完成与首次绘制/显示不是同一刻：按截止时间轮询，避免把正常延迟当成失败。
const waitVisible=async(window,ms)=>{const end=Date.now()+ms;while(Date.now()<end){if(window.isVisible())return true;await new Promise(resolve=>setTimeout(resolve,25));}return false;};
let repository;let reports=0;
app.whenReady().then(async()=>{
 const fixture=path.join(dir,'probe.html');fs.writeFileSync(fixture,'<!doctype html><p>loaded</p>');
 // 正对照：同一运行时显示空白页能载入，排除文件缺失或运行时无法工作的情况。
 const control=new BrowserWindow({show:true,width:240,height:100,title:'Task Manager 窗口检查',webPreferences:{sandbox:true,contextIsolation:true,nodeIntegration:false}});
 await deadline(control.loadFile(fixture),5000,'可见窗口正对照未加载');
 assert.equal(await control.webContents.executeJavaScript('document.body.textContent'),'loaded');
 console.log('VISIBLE_CONTROL=PASS (kept alive)');
 repository=new TaskRepository(path.join(dir,'tasks.sqlite'));
 repository.create({title:'独立窗口检查',due:{mode:'none'}});
 ipcMain.handle('backend:listTodos',()=>({ok:true,data:repository.list()}));
 ipcMain.handle('backend:getDayOrder',()=>({ok:true,data:[]}));
 ipcMain.handle('widget:getState',()=>({enabled:true}));
 ipcMain.handle('widget:setPointerPassthrough',()=>{});
 const page=path.join(root,'dist-renderer','index.html');
 for(let i=0;i<3;i++){
  const w=new BrowserWindow({...widgetWindowStyle,width:380,height:154,x:80,y:80,show:false,webPreferences:{preload:path.join(root,'dist-backend','main','preload.js'),sandbox:true,contextIsolation:true,nodeIntegration:false}});
  w.setIgnoreMouseEvents(true,{forward:true});w.setAlwaysOnTop(true,'floating');
  await deadline(loadWindowPage(w,page,true,()=>reports++),5000,'悬浮窗页面未在5秒内完成加载');
  assert.equal(await waitVisible(w,5000),true,'悬浮窗页面加载后5秒内未显示');
  const rendered=await w.webContents.executeJavaScript(`new Promise((resolve,reject)=>{const end=Date.now()+4000;function check(){const branch=document.querySelector('.crow-branch'),bird=document.querySelector('.crow-illustration'),tag=document.querySelector('.crow-name-tag');if(branch?.complete&&branch.naturalWidth&&bird?.complete&&bird.naturalWidth&&tag?.textContent==='独立窗口检查'){resolve(true);}else if(Date.now()>end){reject(new Error('乌鸦图案或任务标题未渲染'));}else setTimeout(check,30);}check();})`);
  assert.equal(rendered,true);
  const closed=new Promise(resolve=>w.once('closed',resolve));w.close();await deadline(closed,3000,'悬浮窗未关闭');
 }
 for(let i=0;i<5;i++){
  const w=new BrowserWindow({...widgetWindowStyle,width:380,height:154,show:false,webPreferences:{sandbox:true,contextIsolation:true,nodeIntegration:false}});
  const pending=loadWindowPage(w,fixture,true,()=>reports++);
  const closed=new Promise(resolve=>w.once('closed',resolve));w.close();
  await deadline(Promise.all([pending,closed]),3000,'快速关闭未完成');
 }
 assert.equal(reports,0,'关闭窗口不应报告页面失败');
 console.log('WIDGET_LOAD_NATIVE_SMOKE=PASS: visible baseline; 3 real crow renderer open/close cycles with artwork and task title; 5 immediate closes; no error reports');
 control.destroy();repository.close();repository=null;app.exit(0);
}).catch(error=>{console.error('WIDGET_LOAD_NATIVE_SMOKE=FAIL: '+error.message);repository?.close();app.exit(1);});
setTimeout(()=>app.exit(2),35000).unref();
