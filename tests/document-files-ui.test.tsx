import React from 'react';
import {test,afterEach} from 'node:test';
import assert from 'node:assert/strict';
import {JSDOM} from 'jsdom';
import {createRequire} from 'node:module';
import {FileCard} from '../src/renderer/MailFiles';
import type {FileRecord,FilePreview,FileValidation,MailFilesAPI} from '../src/shared/mail-files';

const dom=new JSDOM('<!doctype html><html><body></body></html>',{url:'http://localhost'});
for(const [key,value] of Object.entries({window:dom.window,document:dom.window.document,navigator:dom.window.navigator,HTMLElement:dom.window.HTMLElement}))Object.defineProperty(globalThis,key,{value,configurable:true});
const {render,fireEvent,waitFor,cleanup}=createRequire(process.cwd()+'/package.json')('@testing-library/react') as typeof import('@testing-library/react');
afterEach(()=>{cleanup();Reflect.deleteProperty(window,'mailFiles');});
const ok=<T,>(data:T)=>({ok:true as const,data});
const file:FileRecord={id:'file-a',name:'课程要求.pdf',mediaType:'application/pdf',extension:'pdf',byteSize:4000,sha256:'a'.repeat(64),createdAt:'2026-11-21T01:00:00Z',source:'mail',sourceMailId:'mail-a',warnings:[]};
const other:FileRecord={...file,id:'file-b',name:'报告.docx',extension:'docx',sha256:'b'.repeat(64)};
const png='data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAusB9Wl6SBoAAAAASUVORK5CYII=';
const preview=(id=file.id,page=1):FilePreview=>({fileId:id,sha256:id===file.id?file.sha256:other.sha256,page,pageCount:2,dataUrl:png,width:100,height:140,rendered:true});
const validation=(patch:Partial<FileValidation>={}):FileValidation=>({sha256:file.sha256,structuralPassed:true,rendered:true,visualReviewed:false,pageCount:2,checks:['reopened'],warnings:[],...patch});
function setup(patch:Partial<MailFilesAPI>={}){
 window.mailFiles={get:async id=>ok(id===file.id?file:other),list:async()=>ok([file]),read:async()=>ok('文字内容'),export:async()=>ok(true),importLocal:async()=>ok(null),importAttachment:async()=>ok(file),preview:async(id,page)=>ok(preview(id,page)),inspect:async()=>ok({columns:['id'],rows:[['001']],rowCount:1,nextOffset:null}),validate:async()=>ok(validation()),...patch};
}

test('PDF 页面预览显示实际返回图片并按页读取，收起不修改文件',async()=>{
 const calls:[string,number|undefined][]=[];
 setup({preview:async(id,page)=>{calls.push([id,page]);return ok(preview(id,page));}});
 const ui=render(<FileCard id={file.id}/>);
 await waitFor(()=>assert.ok(ui.getByRole('button',{name:'预览页面'})));
 fireEvent.click(ui.getByRole('button',{name:'预览页面'}));
 await waitFor(()=>assert.equal(ui.getByAltText('课程要求.pdf 第 1 页').getAttribute('src'),png));
 assert.equal((ui.getByRole('button',{name:'预览上一页'}) as HTMLButtonElement).disabled,true);
 fireEvent.click(ui.getByRole('button',{name:'预览下一页'}));
 await waitFor(()=>assert.ok(ui.getByAltText('课程要求.pdf 第 2 页')));
 assert.deepEqual(calls,[[file.id,1],[file.id,2]]);
 assert.equal((ui.getByRole('button',{name:'预览下一页'}) as HTMLButtonElement).disabled,true);
 fireEvent.click(ui.getByRole('button',{name:'收起预览'}));
 assert.equal(ui.queryByRole('img'),null);
});

test('CSV 表格按30行分页，保留前导零且不自动计算或发送数据',async()=>{
 const csv={...file,name:'课程工时.csv',extension:'csv',mediaType:'text/csv'};
 const calls:unknown[]=[];
 setup({get:async()=>ok(csv),inspect:async(id,offset=0,limit)=>{
  calls.push([id,offset,limit]);return ok({columns:['record_id','hours'],rowCount:31,rows:offset===0?Array.from({length:30},(_,i)=>[String(i+1).padStart(3,'0'),'0.10']):[['031','2.50']],nextOffset:offset===0?30:null});
 }});
 const ui=render(<FileCard id={file.id}/>);
 await waitFor(()=>assert.ok(ui.getByRole('button',{name:'查看表格'})));
 assert.equal(ui.queryByRole('button',{name:'预览页面'}),null);
 fireEvent.click(ui.getByRole('button',{name:'查看表格'}));
 await waitFor(()=>assert.ok(ui.getByRole('cell',{name:'001'})));
 assert.equal(ui.getAllByRole('cell',{name:'0.10'}).length,30);
 fireEvent.click(ui.getByRole('button',{name:'表格下一页'}));
 await waitFor(()=>assert.ok(ui.getByRole('cell',{name:'031'})));
 assert.equal(ui.queryByRole('cell',{name:'001'}),null);
 assert.ok(ui.getByText('31–31 / 31 行'));
 assert.deepEqual(calls,[[file.id,0,30],[file.id,30,30]]);
 fireEvent.click(ui.getByRole('button',{name:'表格上一页'}));
 await waitFor(()=>assert.ok(ui.getByRole('cell',{name:'001'})));
 fireEvent.click(ui.getByRole('button',{name:'收起表格'}));
 assert.equal(ui.queryByRole('table'),null);
});

test('文件检查分别展示结构与渲染状态，自动检查不宣称视觉正确',async()=>{
 setup();const ui=render(<FileCard id={file.id}/>);
 await waitFor(()=>assert.ok(ui.getByRole('button',{name:'检查文件'})));
 fireEvent.click(ui.getByRole('button',{name:'检查文件'}));
 await waitFor(()=>assert.ok(ui.getByText('结构检查通过')));
 assert.ok(ui.getByText('页面可渲染 · 共 2 页'));
 assert.ok(ui.getByText('自动检查不代表内容或排版正确，请核对预览。'));
 window.mailFiles!.validate=async()=>ok(validation({structuralPassed:false,rendered:false,warnings:['文件无法重新打开']}));
 fireEvent.click(ui.getByRole('button',{name:'检查文件'}));
 await waitFor(()=>assert.ok(ui.getByText('结构检查未通过')));
 assert.equal(ui.queryByText('结构检查通过'),null);
 assert.equal(ui.queryByText('页面可渲染 · 共 2 页'),null);
 assert.ok(ui.getByText('文件无法重新打开'));
});

test('检查失败清除旧通过状态，预览失败不生成假图片',async()=>{
 setup({get:async()=>ok({...file,validation:validation()}),validate:async()=>({ok:false,error:'无法校验当前副本'}),preview:async()=>({ok:false,error:'渲染超时'})});
 const ui=render(<FileCard id={file.id}/>);
 await waitFor(()=>assert.ok(ui.getByText('结构检查通过')));
 fireEvent.click(ui.getByRole('button',{name:'检查文件'}));
 await waitFor(()=>assert.match(ui.getByRole('alert').textContent??'',/无法校验/));
 assert.equal(ui.queryByText('结构检查通过'),null);
 fireEvent.click(ui.getByRole('button',{name:'预览页面'}));
 await waitFor(()=>assert.match(ui.getByRole('alert').textContent??'',/渲染超时/));
 assert.equal(ui.queryByRole('img'),null);
});

test('拒绝远端图像地址和不匹配文件版本，不向浏览器发起加载',async()=>{
 setup({preview:async()=>ok({...preview(),dataUrl:'https://example.invalid/tracker.png'})});
 const ui=render(<FileCard id={file.id}/>);
 await waitFor(()=>assert.ok(ui.getByRole('button',{name:'预览页面'})));
 fireEvent.click(ui.getByRole('button',{name:'预览页面'}));
 await waitFor(()=>assert.match(ui.getByRole('alert').textContent??'',/本地 PNG/));
 assert.equal(ui.queryByRole('img'),null);
 window.mailFiles!.preview=async()=>ok({...preview(),sha256:other.sha256});
 fireEvent.click(ui.getByRole('button',{name:'预览页面'}));
 await waitFor(()=>assert.match(ui.getByRole('alert').textContent??'',/版本不匹配/));
 assert.equal(ui.queryByRole('img'),null);
});

test('切换文件后晚到的旧预览和文本不会串入新卡片',async()=>{
 let finish!:(value:ReturnType<typeof ok<FilePreview>>)=>void;
 setup({preview:()=>new Promise(resolve=>{finish=resolve;})});
 const ui=render(<FileCard id={file.id}/>);
 await waitFor(()=>assert.ok(ui.getByRole('button',{name:'预览页面'})));
 fireEvent.click(ui.getByRole('button',{name:'预览页面'}));
 ui.rerender(<FileCard id={other.id}/>);
 await waitFor(()=>assert.ok(ui.getByText(other.name)));
 finish(ok(preview()));
 await new Promise(resolve=>setTimeout(resolve,0));
 assert.equal(ui.queryByRole('img'),null);
 assert.equal((ui.getByRole('button',{name:'预览页面'}) as HTMLButtonElement).disabled,false);
 let finishText!:(value:ReturnType<typeof ok<string>>)=>void;
 window.mailFiles!.read=()=>new Promise(resolve=>{finishText=resolve;});
 fireEvent.click(ui.getByRole('button',{name:'查看提取文本'}));
 ui.rerender(<FileCard id={file.id}/>);
 await waitFor(()=>assert.ok(ui.getByText(file.name)));
 finishText(ok('旧文件的延迟文字'));
 await new Promise(resolve=>setTimeout(resolve,0));
 assert.equal(ui.queryByText('旧文件的延迟文字'),null);
});

test('卸载期间结束的请求不影响重新挂载的文件，附件使用原文件ID',async()=>{
 let finish!:(value:ReturnType<typeof ok<FileValidation>>)=>void;
 setup({validate:()=>new Promise(resolve=>{finish=resolve;})});
 const first=render(<FileCard id={file.id}/>);
 await waitFor(()=>assert.ok(first.getByRole('button',{name:'检查文件'})));
 fireEvent.click(first.getByRole('button',{name:'检查文件'}));first.unmount();
 const attachments:unknown[]=[];
 const next=render(<FileCard id={other.id} onAttach={(id,mail)=>attachments.push([id,mail])}/>);
 await waitFor(()=>assert.ok(next.getByRole('button',{name:'加入邮件附件'})));
 finish(ok(validation()));await new Promise(resolve=>setTimeout(resolve,0));
 assert.equal(next.queryByText('结构检查通过'),null);
 fireEvent.click(next.getByRole('button',{name:'加入邮件附件'}));
 assert.deepEqual(attachments,[[other.id,other.sourceMailId]]);
});

test('缺失表格结构时明确报错，不把空表伪装成读取成功',async()=>{
 setup({get:async()=>ok({...file,name:'课程.csv',extension:'csv'}),inspect:async()=>ok({columns:['id'],rows:[],rowCount:8,nextOffset:null})});
 const ui=render(<FileCard id={file.id}/>);
 await waitFor(()=>assert.ok(ui.getByRole('button',{name:'查看表格'})));
 fireEvent.click(ui.getByRole('button',{name:'查看表格'}));
 await waitFor(()=>assert.match(ui.getByRole('alert').textContent??'',/没有返回后续行/));
 assert.equal(ui.queryByRole('table'),null);
});

test('普通图片通过受检PNG预览显示，不依赖文字提取来假装识图',async()=>{
 setup({get:async()=>ok({...file,name:'讲座海报.webp',extension:'webp',mediaType:'image/webp'}),preview:async()=>ok({...preview(),pageCount:1})});
 const ui=render(<FileCard id={file.id}/>);await waitFor(()=>assert.ok(ui.getByRole('button',{name:'预览页面'})));fireEvent.click(ui.getByRole('button',{name:'预览页面'}));await waitFor(()=>assert.equal(ui.getByAltText('讲座海报.webp 第 1 页').getAttribute('src'),png));assert.equal((ui.getByRole('button',{name:'预览下一页'}) as HTMLButtonElement).disabled,true);
});
test('XLSX按文字显示前导零及Decimal结果，展示已批准方法的版本和来源',async()=>{
 const record:FileRecord={...file,name:'报价结果.xlsx',extension:'xlsx',source:'generated',learning:{versionId:'learning-2',versionHash:'c'.repeat(64),scopeId:'customer-a',inputId:'file-input',inputSha256:'d'.repeat(64),outputId:file.id,outputSha256:file.sha256,approvedVersion:2,totals:{金额:'12.30'}}};
 const opened:string[]=[];setup({get:async()=>ok(record),inspect:async()=>ok({fileId:file.id,columns:['PO','金额'],rows:[['00041','12.30']],rowCount:1,nextOffset:null})});const ui=render(<FileCard id={file.id} onLearning={id=>opened.push(id)}/>);
 await waitFor(()=>assert.ok(ui.getByLabelText('应用的学习方法')));fireEvent.click(ui.getByRole('button',{name:'查看处理方法'}));assert.deepEqual(opened,['learning-2']);fireEvent.click(ui.getByRole('button',{name:'查看表格'}));await waitFor(()=>assert.ok(ui.getByLabelText('XLSX 表格预览')));assert.ok(ui.getByRole('cell',{name:'00041'}));assert.ok(ui.getByRole('cell',{name:'12.30'}));
});
test('ZIP目录可查看，解压成员的压缩包路径和校验保留在文件卡片',async()=>{
 let reads=0;setup({get:async()=>ok({...file,name:'课程资料.zip',extension:'zip',mediaType:'application/zip'}),read:async()=>{reads++;return ok('notes/复习提纲.md\nimages/考场.png');}});const ui=render(<FileCard id={file.id}/>);await waitFor(()=>assert.ok(ui.getByRole('button',{name:'查看压缩包目录'})));assert.equal(reads,0);fireEvent.click(ui.getByRole('button',{name:'查看压缩包目录'}));await waitFor(()=>assert.equal(reads,1));assert.ok(ui.getByText(/notes\/复习提纲.md/));ui.unmount();
 setup({get:async()=>ok({...file,name:'复习提纲.md',extension:'md',archiveOrigin:{archiveId:'zip-1',rootArchiveId:'zip-1',memberPath:'notes/复习提纲.md',archiveSha256:'e'.repeat(64),memberSha256:file.sha256,depth:1}})});const member=render(<FileCard id={file.id}/>);await waitFor(()=>assert.ok(member.getByLabelText('压缩包来源')));assert.ok(member.getByText('notes/复习提纲.md'));
});
