const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),os=require('node:os');
const {Module}=require('node:module');
const asar=require('@electron/asar');
const root=path.resolve(__dirname,'..'),app=path.join(root,'release-v2/win-unpacked'),resources=path.join(app,'resources'),archive=path.join(resources,'app.asar');
const files=asar.listPackage(archive).map(value=>value.split(path.sep).join('/'));
for(const name of ['repository','service','imap-mailbox','outlook-auth','mail-operations','legacy-desktop']) assert.equal(files.includes('/dist-backend/main/'+name+'.js'),false,'Old business backend shipped: '+name);
const moduleObject=new Module(path.join(root,'dist-backend/main/python-backend.js'),module);
moduleObject.filename=path.join(root,'dist-backend/main/python-backend.js');
moduleObject._compile(asar.extractFile(archive,path.join('dist-backend','main','python-backend.js')).toString('utf8'),moduleObject.filename);
const {PythonBackend}=moduleObject.exports;
const directory=fs.mkdtempSync(path.join(os.tmpdir(),'tm-pack-python-'));
const bridge=new PythonBackend(directory,{python:path.join(resources,'python/python.exe'),backendDirectory:path.join(resources,'backend')});
(async()=>{
  try{
    const health=await bridge.call('health');assert.equal(health.ok,true,health.error);
    assert.equal(path.resolve(health.data.environment.executable).toLowerCase(),path.join(resources,'python/python.exe').toLowerCase());
    const created=await bridge.call('createTodo',{title:'Packaged standalone Python'});assert.equal(created.ok,true,created.error);
    const mailbox=await bridge.call('mail-workspace:addAccount','imap');assert.equal(mailbox.ok,true,mailbox.error);
    const job=await bridge.call('mail-operations:startImport',{bindingIds:['missing-package-test'],onlyNew:true});assert.equal(job.ok,true,job.error);
    const end=Date.now()+15000;let latest;
    do{latest=await bridge.call('mail-operations:getJob',job.data.id);if(latest.ok&&['completed','failed'].includes(latest.data.state))break;await new Promise(done=>setTimeout(done,100));}while(Date.now()<end);
    assert.equal(latest.ok,true,latest.error);assert.equal(latest.data.state,'completed');assert.equal(latest.data.report.failed,1);
    console.log('PYTHON_PACKAGE_PASS: shipped bridge, embedded runtime, SQLite, mailbox API and owned worker; no TypeScript business modules in archive');
  }finally{await bridge.dispose();fs.rmSync(directory,{recursive:true,force:true});}
})().catch(error=>{console.error(error.message);process.exitCode=1;});
