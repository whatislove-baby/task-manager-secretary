// Electron原生能力测试：无窗口，不控制用户界面，不读取现有待办或密钥。
const { app, safeStorage, nativeImage, BrowserWindow } = require('electron');
const fs = require('node:fs');
const path = require('node:path');
const assert = require('node:assert/strict');
const project = path.resolve(__dirname, '..');
const directory = fs.mkdtempSync(path.join(project, '.runtime-smoke-'));
app.setPath('userData', directory);
app.setPath('sessionData', path.join(directory, 'session'));
app.setAppLogsPath(path.join(directory, 'logs'));
app.on('window-all-closed',()=>{});
app.whenReady().then(async () => {
  let repository; let mailStore; let operations; let probe;
  try {
    const {TaskRepository} = require('../dist-backend/main/repository');
    const file = path.join(directory, 'smoke.sqlite');
    repository = new TaskRepository(file);
    const first = repository.create({title:'runtime-fixture',due:{mode:'value',value:{kind:'date',date:'2026-11-05'}}}).value;
    repository.close(); repository = new TaskRepository(file);
    assert.equal(repository.list()[0].id,first.id);
    repository.setDayOrder('2026-11-05',[first.id]);
    assert.deepEqual(repository.getDayOrder('2026-11-05'),[first.id]);
    assert.equal(await safeStorage.isAsyncEncryptionAvailable(),true);
    const encoded = await safeStorage.encryptStringAsync('runtime-only-test-marker');
    const decoded = await safeStorage.decryptStringAsync(encoded);
    assert.equal(decoded.result,'runtime-only-test-marker');
    const {OutlookAuth}=require('../dist-backend/main/outlook-auth');
    const {OutlookGraph}=require('../dist-backend/main/outlook-graph');
    const vault={available:()=>safeStorage.isAsyncEncryptionAvailable(),encrypt:value=>safeStorage.encryptStringAsync(value),decrypt:value=>safeStorage.decryptStringAsync(value)};
    const auth=new OutlookAuth(directory,vault,async()=>{throw new Error('Smoke test must not open a browser');});
    const clientId='11111111-1111-4111-8111-111111111111';
    await auth.configure(clientId);
    const restored=new OutlookAuth(directory,vault,async()=>{});
    assert.equal((await restored.getState()).clientId,clientId);
    assert.equal(fs.readFileSync(path.join(directory,'outlook-auth.bin')).includes(Buffer.from(clientId)),false);
    await assert.rejects(()=>new OutlookGraph(restored).listFolders(),/连接/);
    const {ImapAuth}=require('../dist-backend/main/imap-auth');
    const {ImapMailbox}=require('../dist-backend/main/imap-mailbox');
    const {MailConnections}=require('../dist-backend/main/mail-connections');
    const imap=new ImapAuth(directory,vault,async()=>{throw new Error('Smoke test must not connect to email');});
    await imap.configure({email:'test@example.invalid',username:'',host:'imap.example.invalid',port:993,security:'tls',password:'runtime-imap-marker'});
    assert.equal(fs.readFileSync(path.join(directory,'imap-auth.bin')).includes(Buffer.from('runtime-imap-marker')),false);
    const restoredImap=new ImapAuth(directory,vault,async()=>{});
    assert.equal((await restoredImap.getState()).imap.hasSecret,true);
    const mailbox=new ImapMailbox(()=>restoredImap.getCredentials());
    const connections=new MailConnections(directory,restored,new OutlookGraph(restored),restoredImap,mailbox);
    assert.equal((await connections.selectMethod('imap')).method,'imap');
    assert.equal((await connections.getState()).account,null);
    await assert.rejects(()=>mailbox.listFolders(),/IMAP/);
    const {MailAccountManager,MailWorkspaceStore}=require('../dist-backend/main/mail-workspace');
    const {ApplicationPreferencesStore}=require('../dist-backend/main/preferences');
    const manager=new MailAccountManager(directory,vault,async()=>{throw new Error('No browser in smoke');});
    assert.equal((await manager.getAccounts()).length,2);
    const binding=await manager.addAccount('imap');
    await manager.configureImap(binding.id,{email:'second@example.invalid',username:'',host:'second.example.invalid',port:993,security:'tls',password:'another-smoke-marker'});
    assert.equal((await manager.getAccounts()).length,3);
    assert.equal(fs.readFileSync(path.join(directory,'mail-accounts',binding.id,'imap-auth.bin')).includes(Buffer.from('another-smoke-marker')),false);
    mailStore=new MailWorkspaceStore(directory);
    const cached=mailStore.put(binding.id,'fixture-identity','second@example.invalid',{id:'remote-1',subject:'Native fixture',sender:'test@example.invalid',receivedAt:'2026-10-30T00:00:00Z',preview:'',body:'Offline text',unread:true,webUrl:'',imported:false});
    mailStore.saveAnalysis({id:'11111111-1111-4111-8111-111111111112',messageIds:[cached.localId],instructions:'fixture',notes:'summary',drafts:[],createdAt:new Date().toISOString(),saved:false,model:'test',baseUrl:'https://model.example.invalid/v1'});
    mailStore.close();mailStore=new MailWorkspaceStore(directory);
    assert.equal(mailStore.getLocal(cached.localId).status,'analyzed');
    assert.equal(mailStore.getLocal(cached.localId).body,'Offline text');
    const prefs=new ApplicationPreferencesStore(directory,{supported:true,set:()=>{throw new Error('Smoke must not change Windows startup');}});
    prefs.save({launchAtLogin:false,widgetOnLaunch:true,closeBehavior:'tray'});
    assert.equal(new ApplicationPreferencesStore(directory,{supported:false,set:()=>{}}).get().widgetOnLaunch,true);
    const art=path.join(project,'dist-renderer','art');
    for(const name of ['branch-pencil.png','raven-pencil.png']){const bitmap=nativeImage.createFromPath(path.join(art,name)).toBitmap();let clear=0,solid=0;for(let i=3;i<bitmap.length;i+=4){if(bitmap[i]<24)clear++;else solid++;}assert.ok(clear>0&&solid>0,'Artwork must have real alpha');}
    const probeFile=path.join(directory,'art-probe.html');fs.writeFileSync(probeFile,'<!doctype html><html><body></body></html>');
    probe=new BrowserWindow({show:false,width:380,height:154,webPreferences:{sandbox:true,nodeIntegration:false,contextIsolation:true}});
    await probe.loadFile(probeFile);
    const artUrl=require('node:url').pathToFileURL(path.join(art,'raven-pencil.png')).href;
    const pixelResult=await probe.webContents.executeJavaScript(`new Promise((resolve,reject)=>{const img=new Image();img.onload=()=>{try{const c=document.createElement('canvas');c.width=img.naturalWidth;c.height=img.naturalHeight;const ctx=c.getContext('2d');ctx.drawImage(img,0,0);const data=ctx.getImageData(0,0,c.width,c.height).data;resolve(data.length>0);}catch(e){reject(e.message);}};img.onerror=()=>reject('image load');img.src=${JSON.stringify(artUrl)};})`);
    assert.equal(pixelResult,true);probe.destroy();probe=null;
    const {MailOperations}=require('../dist-backend/main/mail-operations');
    const {SettingsStore}=require('../dist-backend/main/settings');
    const modelSettings=new SettingsStore(directory,vault);await modelSettings.save({baseUrl:'https://model.example.invalid/v1',model:'fixture',apiKey:'unused-native-fixture'});
    const fakeRequest=async()=>new Response(JSON.stringify({choices:[{message:{content:JSON.stringify({drafts:[],notes:'Native conversation answer'})}}]}));
    operations=new MailOperations(directory,manager,mailStore,repository,modelSettings,()=>{},()=>{},fakeRequest);
    const initial=await operations.api.startConversation('Native fixture question',{baseUrl:'https://model.example.invalid/v1',model:'fixture'});assert.ok(initial.ok);
    let finished;for(let i=0;i<500;i++){const next=await operations.api.getJob(initial.data.id);assert.ok(next.ok);if(next.data.state==='completed'){finished=next.data;break;}if(next.data.state==='failed')throw new Error(next.data.error);await new Promise(resolve=>setTimeout(resolve,10));}
    assert.ok(finished);operations.close();operations=new MailOperations(directory,manager,mailStore,repository,modelSettings,()=>{},()=>{},fakeRequest);
    const history=await operations.api.getConversation(initial.data.conversationId);assert.ok(history.ok);assert.equal(history.data.turns.length,2);
    mailStore.deleteLocal([cached.localId]);assert.equal(mailStore.has(binding.id,'fixture-identity','remote-1'),true);assert.equal(mailStore.isDeleted(binding.id,'fixture-identity','remote-1'),true);assert.equal(mailStore.listLocal({}).total,0);
    console.log('RUNTIME_SMOKE_PASS: SQLite, safeStorage, MSAL, IMAP, multi-account encrypted config, offline mail, receipts, persistent background conversations, PNG alpha/canvas pixels and preferences');
    process.exitCode = 0;
  } catch(error) {
    console.error('RUNTIME_SMOKE_FAIL:',error.message); process.exitCode = 1;
  } finally {
    probe?.destroy(); operations?.close(); mailStore?.close(); repository?.close();
    // 只清理本脚本刚创建的独立测试目录；绝不清理应用data。
    if(path.dirname(directory)===project && path.basename(directory).startsWith('.runtime-smoke-')) {
      try { fs.rmSync(directory,{recursive:true,force:true}); } catch { console.log('临时测试目录仍占用：'+directory); }
    }
    app.exit(process.exitCode || 0);
  }
});
