import { test } from 'node:test';
import assert from 'node:assert/strict';
import { EventEmitter } from 'node:events';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { runInNewContext } from 'node:vm';
import ts from 'typescript';

function fixture() {
  let tray: FakeTray; let empty = false; const imageSizes: unknown[] = [];
  class FakeTray extends EventEmitter {
    dead = false; menu: any[] = []; tooltip = '';
    constructor(_image: unknown) { super(); tray = this; }
    isDestroyed() { return this.dead; }
    setToolTip(value: string) { this.tooltip = value; }
    setContextMenu(menu: any[]) { this.menu = menu; }
    destroy() { this.dead = true; }
  }
  const module = { exports: {} as any };
  const output = ts.transpileModule(readFileSync(resolve('src/main/tray.ts'),'utf8'), {compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022}}).outputText;
  runInNewContext(output, {module,exports:module.exports,require:()=>({Tray:FakeTray,Menu:{buildFromTemplate:(items:any[])=>items},nativeImage:{createFromPath:()=>({isEmpty:()=>empty,resize:(size:unknown)=>{imageSizes.push(size);return {};}})}})});
  let enabled=false, shown=0, quit=0;
  const options={iconPath:'fixture.png',showMain:()=>{shown++;},toggleWidget:()=>{enabled=!enabled;},isWidgetEnabled:()=>enabled,quit:()=>{quit++;}};
  const controller=module.exports.createAppTray(options);
  return {get tray(){return tray!;},controller,options,get shown(){return shown;},get quit(){return quit;},get enabled(){return enabled;},imageSizes,setEmpty:()=>{empty=true;},create:module.exports.createAppTray};
}

test('托盘点击可重开主界面，菜单随悬浮窗开关刷新，退出委托给主进程', () => {
  const f=fixture();
  f.tray.emit('click'); f.tray.emit('double-click'); assert.equal(f.shown,2);
  f.tray.menu.find(item=>item.label==='打开任务界面').click(); assert.equal(f.shown,3);
  f.tray.menu.find(item=>item.label==='开启悬浮窗').click(); assert.equal(f.enabled,true);
  assert.ok(f.tray.menu.find(item=>item.label==='关闭悬浮窗'));
  f.tray.menu.find(item=>item.label==='关闭悬浮窗').click(); assert.equal(f.enabled,false);
  f.tray.menu.find(item=>item.label==='退出应用').click(); assert.equal(f.quit,1);
  assert.equal(f.tray.dead,false); // app处理真正退出和未保存确认。
});

test('托盘销毁清理事件且幂等，缺图标明确失败', () => {
  const f=fixture(); f.controller.destroy(); f.controller.destroy();
  assert.equal(f.tray.dead,true); assert.equal(f.tray.eventNames().length,0);
  f.controller.refresh(); f.tray.emit('click'); assert.equal(f.shown,0);
  f.setEmpty(); assert.throws(()=>f.create(f.options),/图标/);
});
