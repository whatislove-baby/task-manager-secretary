import { existsSync,readFileSync,writeFileSync,renameSync } from 'node:fs';
import { join } from 'node:path';
import { applicationPreferencesSchema,defaultApplicationPreferences,type ApplicationPreferences,type PreferencesState } from '../shared/preferences';
export class ApplicationPreferencesStore {
 private file:string;
 constructor(directory:string,private startup:{supported:boolean;set(enabled:boolean):void}){this.file=join(directory,'application-preferences.json');}
 get():PreferencesState {
  let value=defaultApplicationPreferences;
  if(existsSync(this.file)){try{value=applicationPreferencesSchema.parse(JSON.parse(readFileSync(this.file,'utf8')));}catch{throw new Error('应用偏好文件无法读取，请备份 data 后检查 application-preferences.json。');}}
  return {...value,startupSupported:this.startup.supported};
 }
 save(input:ApplicationPreferences):PreferencesState {
  const value=applicationPreferencesSchema.parse(input),before=this.get();
  if(value.launchAtLogin!==before.launchAtLogin&&!this.startup.supported)throw new Error('开机启动仅支持已构建的 Windows 应用。');
  if(value.launchAtLogin!==before.launchAtLogin)this.startup.set(value.launchAtLogin);
  try{writeFileSync(this.file+'.tmp',JSON.stringify(value),'utf8');renameSync(this.file+'.tmp',this.file);}
  catch(error){if(value.launchAtLogin!==before.launchAtLogin)this.startup.set(before.launchAtLogin);throw error;}
  return {...value,startupSupported:this.startup.supported};
 }
}
