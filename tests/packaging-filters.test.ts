import {test} from 'node:test';
import assert from 'node:assert/strict';
import {mkdtemp,mkdir,writeFile,rm,stat} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import path from 'node:path';
import {createRequire} from 'node:module';

const require=createRequire(path.join(process.cwd(),'package.json'));
const {FileMatcher}=require('app-builder-lib/out/fileMatcher');
const config=require('./package.json');
const asar=require('@electron/asar');
const universalRequire=createRequire(require.resolve('@electron/universal'));
const {mergeASARs}=universalRequire('./asar-utils.js');

test('打包文件过滤保留运行入口和对话导出，排除私人数据与源码测试',async()=>{
 const root=await mkdtemp(path.join(tmpdir(),'tm-pack-filter-'));
 try{
  const app=path.join(root,'app'),output=path.join(root,'output');
  const filter=new FileMatcher(app,output,(value:string)=>value,config.build.files).createFilter();
  const examples:[string,boolean][]=[
   ['dist-backend/main/main.js',true],['dist-backend/shared/conversation-export.js',true],
   ['dist-renderer/index.html',true],['dist-renderer/assets/界面.js',true],['package.json',true],
   ['data/settings.json',false],['.env',false],['tests/private.test.ts',false],
   ['src/main/main.ts',false],['backend/tests/test_private.py',false],
  ];
  for(const [relative,expected] of examples){
   const file=path.join(app,relative);await mkdir(path.dirname(file),{recursive:true});await writeFile(file,'synthetic');
   assert.equal(filter(file,await stat(file)),expected,relative);
  }
  const backend=new FileMatcher(app,output,(value:string)=>value,['**/*.py','assets/**/*']).createFilter();
  for(const [relative,expected] of [['service.py',true],['assets/skill/SKILL.md',true],['state.sqlite',false]] as const){
   const file=path.join(app,relative);await mkdir(path.dirname(file),{recursive:true});await writeFile(file,'synthetic');
   assert.equal(backend(file,await stat(file)),expected,relative);
  }
 }finally{await rm(root,{recursive:true,force:true});}
});

test('ASAR 打包与双架构文件合并仍执行允许规则，拒绝未授权的独有文件',{
 skip:process.platform!=='darwin'?'Universal app merging is a macOS packaging path':false,
},async()=>{
 const root=await mkdtemp(path.join(tmpdir(),'tm-asar-contract-'));
 try{
  for(const arch of ['x64','arm64']){
   const source=path.join(root,arch);await mkdir(source);await writeFile(path.join(source,'app.js'),'shared application');
   if(arch==='arm64')await writeFile(path.join(source,'only-arm.txt'),'arm resource');
   await asar.createPackage(source,path.join(root,arch+'.asar'));
  }
  const options={x64AsarPath:path.join(root,'x64.asar'),arm64AsarPath:path.join(root,'arm64.asar'),outputAsarPath:path.join(root,'merged.asar')};
  await assert.rejects(mergeASARs({...options,singleArchFiles:'allowed-*.txt'}),/not covered by/);
  await mergeASARs({...options,singleArchFiles:'only-*.txt'});
  assert.equal(asar.extractFile(options.outputAsarPath,'app.js').toString(),'shared application');
  assert.equal(asar.extractFile(options.outputAsarPath,'only-arm.txt').toString(),'arm resource');
 }finally{await rm(root,{recursive:true,force:true});}
});
