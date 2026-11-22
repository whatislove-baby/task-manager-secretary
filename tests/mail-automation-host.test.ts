import test, {type TestContext} from 'node:test';
import assert from 'node:assert/strict';
import {access, mkdir, mkdtemp, readFile, rm, stat, writeFile} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {basename, join, resolve} from 'node:path';
import {JSDOM} from 'jsdom';
import {MailAutomationHost, type AutomationRuntime} from '../src/main/mail-automation-host';
import {dispatchPython} from '../src/main/python-desktop';
import type {Result} from '../src/shared/contracts';
import {defaultMailAutomationConfig, type MailAutomationConfigInput} from '../src/shared/mail-automation';

// Every launchctl/spawn boundary is replaced. Files belong only to mkdtemp.
class FakeLaunchd {
  calls:{file:string;args:string[]}[]=[];
  starts:{file:string;args:string[];cwd:string}[]=[];
  loaded=new Set<string>();
  failBootstrap=false;
  failStart=false;
  onStart?:()=>void;
  execute=async(file:string,args:string[])=>{
    this.calls.push({file,args:[...args]});
    assert.equal(file,'/bin/launchctl');
    if(args[0]==='print'){
      if(!this.loaded.has(args[1]))throw Error('synthetic service not loaded');
    }else if(args[0]==='bootstrap'){
      if(this.failBootstrap)throw Error('synthetic bootstrap failure');
      this.loaded.add(args[1]+'/'+basename(args[2],'.plist'));
    }else if(args[0]==='bootout'){
      this.loaded.delete(args[1]);
    }else throw Error('unexpected launchctl operation');
  };
  start=async(file:string,args:string[],cwd:string)=>{
    this.onStart?.();
    this.starts.push({file,args:[...args],cwd});
    if(this.failStart)throw Error('synthetic spawn failure');
  };
}

class FakeBackend {
  calls:{method:string;args:unknown[]}[]=[];
  config:MailAutomationConfigInput={...structuredClone(defaultMailAutomationConfig),bindingIds:['fixture-mailbox']};
  rejectSave=false;
  rejectRollback=false;
  queued=0;
  call=async(method:string,...args:unknown[]):Promise<Result<unknown>>=>{
    this.calls.push({method,args:structuredClone(args)});
    if(method==='mail-automation:saveConfig'){
      const input=args[0] as MailAutomationConfigInput;
      if(this.rejectSave||this.rejectRollback&&!input.enabled)return {ok:false,error:'synthetic config write failure'};
      this.config=structuredClone(input);
      return {ok:true,data:{...this.config,nextRunAt:null}};
    }
    if(method==='mail-automation:getConfig')return {ok:true,data:{...this.config,nextRunAt:null}};
    if(method==='mail-automation:runNow'){this.queued++;return {ok:true,data:{queued:true}};}
    throw Error('unexpected backend method '+method);
  };
}

async function fixture(t:TestContext){
  const root=await mkdtemp(join(tmpdir(),'tm-automation-host-test-'));
  t.after(()=>rm(root,{recursive:true,force:true}));
  // Materialized fixture paths must also be legal on Windows. XML quoting is
  // separately checked below without trying to create Windows-illegal names.
  const home=join(root,'Home & 中文');
  const directory=join(root,'Data folder & 中文 $(literal)');
  const runtime:AutomationRuntime={python:join(root,'Runtime with spaces & 中文','python executable'),backendDirectory:join(root,'Backend & 中文')};
  await mkdir(join(runtime.backendDirectory,'task_backend'),{recursive:true});
  await mkdir(join(runtime.python,'..'),{recursive:true});
  await writeFile(runtime.python,'synthetic runtime; never executed');
  await writeFile(join(runtime.backendDirectory,'task_backend','automation_worker.py'),'# synthetic fixture; never executed');
  const launchd=new FakeLaunchd();
  const options={platform:'darwin',uid:501,home,execute:launchd.execute,start:launchd.start};
  return {root,home,directory,runtime,launchd,options,host:new MailAutomationHost(directory,runtime,options)};
}
async function exists(path:string){try{await access(path);return true;}catch{return false;}}
function plistField(dict:Element,key:string):Element{
  const node=Array.from(dict.children).find(element=>element.tagName==='key'&&element.textContent===key)?.nextElementSibling;
  assert.ok(node,'missing plist field '+key);return node;
}

test('plist preserves spaces/XML characters as explicit argv and uses no shell or credentials',async t=>{
  const f=await fixture(t);
  const directory=join(f.root,'Data & <xml> "double" \'single\' $(literal)');
  const runtime={python:join(f.root,'Python & <xml> "double" \'single\''),backendDirectory:join(f.root,'Backend & <xml> "quoted"')};
  const host=new MailAutomationHost(directory,runtime,f.options);
  const source=host.plist();
  const dom=new JSDOM(source,{contentType:'text/xml'});
  t.after(()=>dom.window.close());
  const dict=dom.window.document.querySelector('plist > dict');assert.ok(dict);
  const argumentsNode=plistField(dict,'ProgramArguments');
  assert.equal(argumentsNode.tagName,'array');
  assert.deepEqual(Array.from(argumentsNode.children).map(node=>node.textContent),[
    resolve(runtime.python),'-B','-u','-m','task_backend.automation_worker','--data-dir',resolve(directory),'--once',
  ]);
  assert.equal(plistField(dict,'WorkingDirectory').textContent,resolve(runtime.backendDirectory));
  assert.match(source,/&amp;/);assert.match(source,/&lt;xml&gt;/);assert.match(source,/&quot;/);assert.match(source,/&apos;/);
  assert.equal(plistField(dict,'Label').textContent,host.label);
  assert.equal(plistField(dict,'StartInterval').textContent,'60');
  assert.equal(plistField(dict,'RunAtLoad').tagName,'true');
  assert.equal(plistField(dict,'Umask').textContent,'63');
  const environment=plistField(dict,'EnvironmentVariables');
  assert.deepEqual(Array.from(environment.children).filter(node=>node.tagName==='key').map(node=>node.textContent),['PYTHONUTF8','PYTHONDONTWRITEBYTECODE']);
  assert.equal(await exists(f.host.plistPath),false);
  assert.equal(f.launchd.calls.length,0);assert.equal(f.launchd.starts.length,0);
});

test('construction, status and disabled config never create a LaunchAgent; explicit enable does',async t=>{
  const f=await fixture(t);const backend=new FakeBackend();let changed=0;
  assert.equal((await f.host.status()).installed,false);
  assert.equal(await exists(join(f.home,'Library','LaunchAgents')),false);
  assert.equal(f.launchd.calls.length,0);
  await dispatchPython(backend,true,'mail-automation:getConfig',[],()=>changed++,{automation:f.host});
  await dispatchPython(backend,true,'mail-automation:getBackgroundStatus',[],()=>changed++,{automation:f.host});
  const disabled=await dispatchPython(backend,true,'mail-automation:saveConfig',[backend.config],()=>changed++,{automation:f.host});
  assert.equal(disabled.ok,true);assert.equal(await exists(f.host.plistPath),false);
  assert.equal(f.launchd.calls.filter(call=>call.args[0]==='bootstrap').length,0);
  const enabled=await dispatchPython(backend,true,'mail-automation:saveConfig',[{...backend.config,enabled:true}],()=>changed++,{automation:f.host});
  assert.equal(enabled.ok,true);assert.equal(backend.config.enabled,true);
  assert.equal(await readFile(f.host.plistPath,'utf8'),f.host.plist());
  if(process.platform!=='win32')assert.equal((await stat(f.host.plistPath)).mode&0o777,0o600);
  assert.equal((await f.host.status()).active,true);
  assert.equal(changed,2,'config mutations broadcast, read-only status does not');
  const stopped=await dispatchPython(backend,true,'mail-automation:saveConfig',[{...backend.config,enabled:false}],()=>changed++,{automation:f.host});
  assert.equal(stopped.ok,true);assert.equal(backend.config.enabled,false);
  assert.equal(await exists(f.host.plistPath),false);assert.equal((await f.host.status()).active,false);
  assert.equal(changed,3);
});

test('data-directory identity isolates agents and disabling one preserves other agents/files',async t=>{
  const f=await fixture(t);
  const second=new MailAutomationHost(join(f.root,'Other data'),f.runtime,f.options);
  const equivalent=new MailAutomationHost(join(f.directory,'nested','..'),f.runtime,f.options);
  assert.equal(equivalent.label,f.host.label);assert.notEqual(second.label,f.host.label);
  const unrelated=join(f.home,'Library','LaunchAgents','com.example.unrelated.plist');
  await mkdir(join(unrelated,'..'),{recursive:true});await writeFile(unrelated,'unrelated sentinel');
  f.launchd.loaded.add('gui/501/com.example.unrelated');
  await f.host.enable();await second.enable();await f.host.disable();await f.host.disable();
  assert.equal(await exists(f.host.plistPath),false);
  assert.equal((await second.status()).active,true);
  assert.equal(await readFile(unrelated,'utf8'),'unrelated sentinel');
  assert.equal(f.launchd.loaded.has('gui/501/com.example.unrelated'),true);
  assert.deepEqual(f.launchd.calls.filter(call=>call.args[0]==='bootout').map(call=>call.args),[['bootout','gui/501/'+f.host.label]]);
  assert.equal(await exists(f.host.plistPath+'.tmp'),false);
});

test('same loaded configuration is not repeatedly booted out or installed, including concurrent calls',async t=>{
  const f=await fixture(t);
  await Promise.all([f.host.enable(),f.host.enable(),f.host.enable()]);
  assert.equal(f.launchd.calls.filter(call=>call.args[0]==='bootstrap').length,1);
  assert.equal(f.launchd.calls.filter(call=>call.args[0]==='bootout').length,0);
  const recreated=new MailAutomationHost(f.directory,f.runtime,f.options);
  await recreated.enable();
  assert.equal(f.launchd.calls.filter(call=>call.args[0]==='bootstrap').length,1);
  assert.equal(f.launchd.calls.filter(call=>call.args[0]==='bootout').length,0);
});

test('runtime relocation replaces only its own loaded LaunchAgent with the new explicit path',async t=>{
  const f=await fixture(t);await f.host.enable();
  const relocated={...f.runtime,python:join(f.root,'Moved runtime','new python')};
  await mkdir(join(relocated.python,'..'),{recursive:true});await writeFile(relocated.python,'never executed');
  const moved=new MailAutomationHost(f.directory,relocated,f.options);
  await moved.enable();
  assert.equal(moved.label,f.host.label);
  assert.equal(await readFile(moved.plistPath,'utf8'),moved.plist());
  assert.deepEqual(f.launchd.calls.filter(call=>call.args[0]==='bootout').map(call=>call.args),[['bootout','gui/501/'+f.host.label]]);
  assert.equal(f.launchd.calls.filter(call=>call.args[0]==='bootstrap').length,2);
});

test('failed LaunchAgent installation removes its plist and rolls enabled config back to disabled',async t=>{
  const f=await fixture(t);const backend=new FakeBackend();f.launchd.failBootstrap=true;
  const input={...backend.config,enabled:true};
  const result=await dispatchPython(backend,true,'mail-automation:saveConfig',[input],()=>{},{automation:f.host});
  assert.equal(result.ok,false);assert.equal(backend.config.enabled,false);
  assert.deepEqual(backend.calls.map(call=>({method:call.method,enabled:(call.args[0] as MailAutomationConfigInput).enabled})),[
    {method:'mail-automation:saveConfig',enabled:true},{method:'mail-automation:saveConfig',enabled:false},
  ]);
  assert.equal(await exists(f.host.plistPath),false);assert.equal(await exists(f.host.plistPath+'.tmp'),false);
  assert.equal((await f.host.status()).installed,false);
  assert.equal(f.launchd.starts.length,0);
});

test('if disabled-config rollback also fails, IPC must not falsely claim automation was disabled',async t=>{
  const f=await fixture(t);const backend=new FakeBackend();f.launchd.failBootstrap=true;backend.rejectRollback=true;
  const result=await dispatchPython(backend,true,'mail-automation:saveConfig',[{...backend.config,enabled:true}],()=>{},{automation:f.host});
  assert.equal(result.ok,false);assert.equal(backend.config.enabled,true,'fixture rollback deliberately failed');
  if(!result.ok)assert.doesNotMatch(result.error,/定时整理已停用/,'Do not claim a failed rollback disabled the persisted config');
});

test('manual run queues first then starts an independent once process without installing a schedule',async t=>{
  const f=await fixture(t);const backend=new FakeBackend();let changed=0;
  f.launchd.onStart=()=>assert.equal(backend.queued,1,'request must be durable before spawn is attempted');
  const result=await dispatchPython(backend,true,'mail-automation:runNow',[],()=>changed++,{automation:f.host});
  assert.equal(result.ok,true);assert.equal(backend.queued,1);assert.equal(changed,1);
  assert.equal(backend.config.enabled,false);
  assert.deepEqual(f.launchd.starts,[{file:resolve(f.runtime.python),args:[
    '-B','-u','-m','task_backend.automation_worker','--data-dir',resolve(f.directory),'--once',
  ],cwd:resolve(f.runtime.backendDirectory)}]);
  assert.equal(f.launchd.calls.length,0);assert.equal(await exists(f.host.plistPath),false);
});

test('manual spawn failure reports an already saved request without retrying or enabling a schedule',async t=>{
  const f=await fixture(t);const backend=new FakeBackend();f.launchd.failStart=true;
  const result=await dispatchPython(backend,true,'mail-automation:runNow',[],()=>{},{automation:f.host});
  assert.equal(result.ok,false);if(!result.ok)assert.match(result.error,/请求已保存/);
  assert.equal(backend.queued,1);assert.equal(f.launchd.starts.length,1);assert.equal(f.launchd.calls.length,0);
  assert.equal(backend.config.enabled,false);assert.equal(await exists(f.host.plistPath),false);
});

test('untrusted automation IPC is rejected before backend, filesystem or process operations',async t=>{
  const f=await fixture(t);const backend=new FakeBackend();let changed=0;
  for(const [channel,args] of [
    ['mail-automation:saveConfig',[{...backend.config,enabled:true}]],
    ['mail-automation:runNow',[]],['mail-automation:getBackgroundStatus',[]],
  ] as [string,unknown[]][]){
    assert.equal((await dispatchPython(backend,false,channel,args,()=>changed++,{automation:f.host})).ok,false);
  }
  assert.equal(backend.calls.length,0);assert.equal(f.launchd.calls.length,0);assert.equal(f.launchd.starts.length,0);
  assert.equal(changed,0);assert.equal(await exists(join(f.home,'Library','LaunchAgents')),false);
});

test('rejected config, missing host and illegal status arguments cannot start or install automation',async t=>{
  const f=await fixture(t);const backend=new FakeBackend();backend.rejectSave=true;
  assert.equal((await dispatchPython(backend,true,'mail-automation:saveConfig',[{...backend.config,enabled:true}],()=>{},{automation:f.host})).ok,false);
  assert.equal(backend.calls.length,1);assert.equal(f.launchd.calls.length,0);
  assert.equal((await dispatchPython(backend,true,'mail-automation:saveConfig',[{...backend.config,enabled:true}],()=>{})).ok,false);
  assert.equal((await dispatchPython(backend,true,'mail-automation:runNow',[],()=>{})).ok,false);
  assert.equal((await dispatchPython(backend,true,'mail-automation:getBackgroundStatus',['unexpected'],()=>{},{automation:f.host})).ok,false);
  assert.equal((await dispatchPython(backend,true,'mail-automation:executeShell',['arbitrary'],()=>{},{automation:f.host})).ok,false);
  assert.equal(backend.calls.length,1);assert.equal(f.launchd.starts.length,0);assert.equal(await exists(f.host.plistPath),false);
});
