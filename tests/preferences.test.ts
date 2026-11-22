import {test} from 'node:test';
import assert from 'node:assert/strict';
import {mkdtempSync,rmSync,writeFileSync} from 'node:fs';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {ApplicationPreferencesStore} from '../src/main/preferences';

test('启动设置持久化，读取不修改系统启动项，只有切换开机启动才注册',()=>{
 const directory=mkdtempSync(join(tmpdir(),'task-preferences-'));const calls:boolean[]=[];
 try{const store=new ApplicationPreferencesStore(directory,{supported:true,set:value=>{calls.push(value);}});
 assert.deepEqual(store.get(),{launchAtLogin:false,widgetOnLaunch:false,closeBehavior:'tray',startupSupported:true});assert.equal(calls.length,0);
 store.save({launchAtLogin:true,widgetOnLaunch:true,closeBehavior:'quit'});
 const reopened=new ApplicationPreferencesStore(directory,{supported:true,set:value=>{calls.push(value);}});assert.equal(reopened.get().widgetOnLaunch,true);assert.deepEqual(calls,[true]);
 reopened.save({launchAtLogin:true,widgetOnLaunch:false,closeBehavior:'tray'});assert.deepEqual(calls,[true]);
 reopened.save({launchAtLogin:false,widgetOnLaunch:false,closeBehavior:'tray'});assert.deepEqual(calls,[true,false]);
 }finally{rmSync(directory,{recursive:true,force:true});}
});
test('注册失败不写入偏好，开发运行不允许注册自启，损坏文件不被静默覆盖',()=>{
 const directory=mkdtempSync(join(tmpdir(),'task-preferences-'));
 try{const value={launchAtLogin:true,widgetOnLaunch:false,closeBehavior:'tray' as const};
 const store=new ApplicationPreferencesStore(directory,{supported:true,set:()=>{throw new Error('blocked');}});assert.throws(()=>store.save(value));assert.equal(store.get().launchAtLogin,false);
 const dev=new ApplicationPreferencesStore(directory,{supported:false,set:()=>{throw new Error('must not call');}});assert.throws(()=>dev.save(value));
 writeFileSync(join(directory,'application-preferences.json'),'broken');assert.throws(()=>store.get());assert.throws(()=>store.save(value));
 }finally{rmSync(directory,{recursive:true,force:true});}
});
