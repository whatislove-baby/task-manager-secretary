const fs=require('fs');
const path=require('path');
const {spawn}=require('child_process');
const WebSocket=require('ws');
const project=path.resolve(__dirname,'..');
const exe=path.join(project,'release-v2','win-unpacked','Task Manager.exe');
const logPath=path.join(project,'packaged-verify.log');
const log=fs.createWriteStream(logPath);
const env={...process.env};delete env.ELECTRON_RUN_AS_NODE;
const child=spawn(exe,['--remote-debugging-port=9223'],{env,detached:false,stdio:['ignore','pipe','pipe']});
child.stdout.pipe(log);child.stderr.pipe(log);
const sleep=ms=>new Promise(r=>setTimeout(r,ms));
async function targets(){try{const response=await fetch('http://127.0.0.1:9223/json/list');if(!response.ok)return [];return await response.json();}catch{return [];}}
async function waitTargets(){for(let i=0;i<120;i++){const list=await targets();if(list.some(t=>t.type==='page'&&!t.url.includes('widget=1'))&&list.some(t=>t.type==='page'&&t.url.includes('widget=1')))return list;await sleep(250);}throw new Error('调试页面未就绪');}
function cdp(url){return new Promise((resolve,reject)=>{const socket=new WebSocket(url);socket.once('open',()=>resolve(socket));socket.once('error',reject);});}
let nextId=1;
function call(socket,method,params={}){return new Promise((resolve,reject)=>{const id=nextId++;const onMessage=data=>{const value=JSON.parse(String(data));if(value.id!==id)return;socket.off('message',onMessage);if(value.error)reject(new Error(value.error.message));else resolve(value.result);};socket.on('message',onMessage);socket.send(JSON.stringify({id,method,params}));});}
async function evaluate(socket,expression){const result=await call(socket,'Runtime.evaluate',{expression,awaitPromise:true,returnByValue:true});if(result.exceptionDetails)throw new Error(result.exceptionDetails.text);return result.result.value;}
(async()=>{
 try{
  const list=await waitTargets();
  const main=list.find(t=>t.type==='page'&&!t.url.includes('widget=1'));
  const widget=list.find(t=>t.type==='page'&&t.url.includes('widget=1'));
  const mainSocket=await cdp(main.webSocketDebuggerUrl),widgetSocket=await cdp(widget.webSocketDebuggerUrl);
  await call(mainSocket,'Page.enable');await call(widgetSocket,'Page.enable');
  let mainState;for(let i=0;i<80;i++){mainState=await evaluate(mainSocket,`({title:document.title,planner:!!document.querySelector('.planner-app'),nav:[...document.querySelectorAll('nav[aria-label="工具"] button')].map(b=>b.textContent.trim()),todos:document.querySelectorAll('.root-row').length,weekly:document.querySelectorAll('.day-cell').length,body:document.body.innerText.slice(0,120)})`);if(mainState.planner)break;await sleep(250);}
  if(!mainState.planner)throw new Error('主窗口未渲染: '+JSON.stringify(mainState));
  const shot=await call(mainSocket,'Page.captureScreenshot',{format:'png'});fs.writeFileSync(path.join(project,'docs','verification','v2-packaged-main.png'),Buffer.from(shot.data,'base64'));
  const widgetState=await evaluate(widgetSocket,`({widget:!!document.querySelector('.crow-widget'),birds:document.querySelectorAll('.crow-bird').length,expanded:!!document.querySelector('.crow-paper'),body:document.body.innerText.slice(0,80)})`);
  const shot2=await call(widgetSocket,'Page.captureScreenshot',{format:'png',captureBeyondViewport:false});fs.writeFileSync(path.join(project,'docs','verification','v2-packaged-widget.png'),Buffer.from(shot2.data,'base64'));
  const version=await (await fetch('http://127.0.0.1:9223/json/version')).json();
  console.log('PACKAGED_JSON='+JSON.stringify({mainState,widgetState,browser:version.Browser,dataDir:true}));
  const browserSocket=await cdp(version.webSocketDebuggerUrl);await call(browserSocket,'Browser.close');browserSocket.close();mainSocket.close();widgetSocket.close();
  await sleep(1200);
  console.log('PACKAGED_PASS');
  process.exit(0);
 }catch(error){
  console.error('PACKAGED_FAIL',error.stack||error.message);
  try{child.kill();}catch{}
  process.exit(1);
 }
})();
