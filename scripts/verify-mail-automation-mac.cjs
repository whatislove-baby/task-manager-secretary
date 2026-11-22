// Exercise an actual temporary launchd agent with the signed bundled Python.
// The fresh directory has automation disabled and no mail/model credentials.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const os = require('node:os');
const {spawnSync} = require('node:child_process');
const {Module} = require('node:module');
const asar = require('@electron/asar');
const {PythonBackend} = require('../dist-backend/main/python-backend');
const root = path.resolve(__dirname, '..');
const application = path.join(root, 'release-mac', process.arch === 'arm64' ? 'mac-arm64' : 'mac', 'Task Manager.app');
const resources = path.join(application, 'Contents', 'Resources');
const hostModule = new Module(path.join(root, 'dist-backend/main/mail-automation-host.js'), module);
hostModule.filename = path.join(root, 'dist-backend/main/mail-automation-host.js');
hostModule._compile(asar.extractFile(path.join(resources, 'app.asar'), 'dist-backend/main/mail-automation-host.js').toString('utf8'), hostModule.filename);
const {MailAutomationHost} = hostModule.exports;
const temporary = fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(), 'tm-launchd-check-')));
const directory = path.join(temporary, 'isolated data');
const runtime = {python:path.join(resources,'python','bin','python3'),backendDirectory:path.join(resources,'backend')};
const host = new MailAutomationHost(directory, runtime, {home:temporary});
let bridge;
(async()=>{
  try {
    fs.mkdirSync(directory);
    assert.equal((await host.status()).installed,false);
    await host.enable();
    const loaded=await host.status();
    assert.equal(loaded.installed,true);assert.equal(loaded.active,true);
    const end=Date.now()+15000;
    while(!fs.existsSync(path.join(directory,'mail-automation.sqlite'))&&Date.now()<end) await new Promise(done=>setTimeout(done,100));
    assert.ok(fs.existsSync(path.join(directory,'mail-automation.sqlite')),'launchd must create the automation DB without Electron');
    bridge=new PythonBackend(directory,runtime);
    const config=await bridge.call('mail-automation:getConfig');
    assert.equal(config.ok,true,config.error);assert.equal(config.data.enabled,false);assert.deepEqual(config.data.bindingIds,[]);
    const status=await bridge.call('mail-automation:getStatus');
    assert.equal(status.ok,true,status.error);assert.equal(status.data.lastRun,null);
    const items=await bridge.call('mail-automation:listResults',{});
    assert.equal(items.ok,true,items.error);assert.equal(items.data.total,0);
    await bridge.dispose();bridge=null;
    await host.disable();
    assert.equal((await host.status()).installed,false);
    assert.equal((await host.status()).active,false);
    const signed=spawnSync('/usr/bin/codesign',['--verify','--deep','--strict',application],{encoding:'utf8'});
    assert.equal(signed.status,0,signed.stderr);
    console.log('MAC_AUTOMATION_LAUNCHD_PASS: real temporary LaunchAgent starts signed packaged Python independently of Electron, honors disabled configuration, preserves signature, and unloads cleanly. No external accounts or model requests.');
  } finally {
    if(bridge)await bridge.dispose();
    await host.disable().catch(()=>{});
    fs.rmSync(temporary,{recursive:true,force:true});
  }
})().catch(error=>{console.error(error.message);process.exitCode=1;});
