import React from 'react';
import {test,afterEach} from 'node:test';
import assert from 'node:assert/strict';
import {JSDOM} from 'jsdom';
import {createRequire} from 'node:module';
import {AIHistoryPanel} from '../src/renderer/AIHistoryPanel';
import {MailComposePanel} from '../src/renderer/MailComposePanel';
import {MailWorkspacePanel} from '../src/renderer/MailWorkspacePanel';
import type {FileRecord,FileTaskInput} from '../src/shared/mail-files';
import type {ComposeReceipt,MailComposeAPI} from '../src/shared/mail-compose';
import type {MailBinding,LocalMail} from '../src/shared/mail-workspace';
import type {MailConversation,MailOperationsAPI} from '../src/shared/mail-operations';
import type {DesktopAPI} from '../src/shared/contracts';
import {defaultMailFilter} from '../src/shared/mail';
const dom=new JSDOM('<!doctype html><html><body></body></html>',{url:'http://localhost'});
for(const [key,value] of Object.entries({window:dom.window,document:dom.window.document,navigator:dom.window.navigator,HTMLElement:dom.window.HTMLElement}))Object.defineProperty(globalThis,key,{value,configurable:true});
const {render,fireEvent,waitFor,cleanup,within}=createRequire(process.cwd()+'/package.json')('@testing-library/react') as typeof import('@testing-library/react');
afterEach(()=>{cleanup();Reflect.deleteProperty(window,'mailFiles');Reflect.deleteProperty(window,'widget');});
const ok=<T,>(data:T)=>({ok:true as const,data});
const input:FileRecord={id:'file-a',name:'老师要求.pdf',mediaType:'application/pdf',extension:'pdf',byteSize:3000,sha256:'a'.repeat(64),createdAt:'2026-11-20T12:00:00Z',source:'local',warnings:[]};
const output:FileRecord={...input,id:'file-b',name:'项目计划.docx',mediaType:'application/vnd.openxmlformats-officedocument.wordprocessingml.document',extension:'docx',sha256:'b'.repeat(64),source:'generated',parentIds:[input.id]};
const conversation:MailConversation={id:'conversation-a',title:'文件修订',createdAt:input.createdAt,updatedAt:input.createdAt,turnCount:1,messageIds:['mail-a'],turns:[{id:'turn-a',role:'assistant',content:'已生成修订副本。',artifactIds:[output.id],createdAt:input.createdAt}]};
const job={id:'job-a',kind:'files' as const,state:'queued' as const,label:'处理文件',done:0,total:1,createdAt:input.createdAt,updatedAt:input.createdAt,conversationId:conversation.id};
const account:MailBinding={id:'binding-a',method:'imap',state:{method:'imap',clientId:'',account:{id:'a',email:'student@example.com',name:'Student'},imap:{email:'student@example.com',username:'student@example.com',host:'imap.example.com',port:993,security:'tls',hasSecret:true},filter:structuredClone(defaultMailFilter)}};
const receipt:ComposeReceipt={id:'receipt-a',bindingId:account.id,from:'student@example.com',to:'teacher@example.com',subject:'项目计划',body:'请查收附件。',messageId:'<test@example.com>',createdAt:input.createdAt,status:'prepared',detail:''};
function setup(){window.desktop={getSettings:async()=>ok({baseUrl:'https://ai.example/v1',model:'test-model',hasApiKey:true,dataDirectory:'data'})} as DesktopAPI;
 window.mailFiles={list:async()=>ok([input,output]),get:async id=>ok(id===input.id?input:output),read:async()=>ok('项目必须包括测试和团队交接。'),export:async()=>ok(true),importLocal:async()=>ok([input]),importAttachment:async()=>ok({...input,source:'mail',sourceMailId:'mail-a'})};
 window.mailOperations={listConversations:async()=>ok([]),listJobs:async()=>ok([]),getConversation:async()=>ok(conversation),startFileTask:async()=>ok(job)} as unknown as MailOperationsAPI;
 window.mailCompose={getConfig:async()=>ok({bindingId:account.id,email:'student@example.com',host:'smtp.example.com',port:465,security:'ssl',configured:true,hasSecret:true}),saveConfig:async()=>{throw Error('unused');},generate:async()=>ok({subject:receipt.subject,body:receipt.body}),prepare:async value=>ok({...receipt,...value,attachments:(value.attachmentIds??[]).map(id=>id===input.id?input:output)}),send:async()=>ok({...receipt,status:'sent'}),listRecent:async()=>ok([])} as MailComposeAPI;
}
async function readyCompose(ui:ReturnType<typeof render>){await waitFor(()=>assert.equal((ui.getByLabelText('选择发件账号') as HTMLSelectElement).disabled,false));}
function fillCompose(ui:ReturnType<typeof render>){fireEvent.change(ui.getByLabelText('收件人（一个邮箱地址）'),{target:{value:receipt.to}});fireEvent.change(ui.getByLabelText('邮件主题'),{target:{value:receipt.subject}});fireEvent.change(ui.getByLabelText('邮件正文'),{target:{value:receipt.body}});}

test('本地导入与文本预览不发送 AI，明确处理后带选中文件和输出格式并展示真实产物',async()=>{
 setup();const tasks:FileTaskInput[]=[];window.mailOperations.startFileTask=async value=>{tasks.push(value);return ok(job);};let attached:any;
 const ui=render(<AIHistoryPanel onReview={()=>{}} onAttachFile={(id,mail)=>{attached={id,mail};}}/>);
 fireEvent.click(ui.getByLabelText('读取资料／生成文件'));fireEvent.click(ui.getByRole('button',{name:'添加本地资料'}));
 await waitFor(()=>assert.equal((ui.getByRole('checkbox',{name:/老师要求.pdf/}) as HTMLInputElement).checked,true));
 await waitFor(()=>assert.ok(ui.getByRole('button',{name:'查看提取文本'})));fireEvent.click(ui.getByRole('button',{name:'查看提取文本'}));await waitFor(()=>assert.ok(ui.getByText('项目必须包括测试和团队交接。')));assert.equal(tasks.length,0);
 fireEvent.change(ui.getByLabelText('输出格式'),{target:{value:'docx'}});fireEvent.change(ui.getByLabelText('输出文件名（可选）'),{target:{value:'项目计划'}});fireEvent.change(ui.getByLabelText('想让 AI 帮你做什么'),{target:{value:'按老师要求修订计划'}});
 fireEvent.click(ui.getByRole('button',{name:'处理所选资料'}));await waitFor(()=>assert.equal(tasks.length,1));assert.deepEqual(tasks[0],{messageIds:[],text:'按老师要求修订计划',fileIds:[input.id],format:'docx',name:'项目计划',target:{baseUrl:'https://ai.example/v1',model:'test-model'}});
 await waitFor(()=>assert.ok(ui.getByRole('button',{name:'加入邮件附件'})));fireEvent.click(ui.getByRole('button',{name:'加入邮件附件'}));assert.deepEqual(attached,{id:output.id,mail:'mail-a'});
});

test('历史产物可选作下一轮修订资料，并沿用正确会话而不是旧待办提取接口',async()=>{
 setup();let task:FileTaskInput|undefined;window.mailOperations.startFileTask=async value=>{task=value;return ok(job);};
 const ui=render(<AIHistoryPanel initialId={conversation.id} onReview={()=>{}}/>);await waitFor(()=>assert.ok(ui.getByRole('button',{name:'作为本轮资料'})));
 fireEvent.click(ui.getByRole('button',{name:'作为本轮资料'}));await waitFor(()=>assert.equal((ui.getByRole('checkbox',{name:/项目计划.docx/}) as HTMLInputElement).checked,true));
 fireEvent.change(ui.getByLabelText('输出格式'),{target:{value:'pdf'}});fireEvent.change(ui.getByLabelText('继续这次对话'),{target:{value:'将测试日期改为11月22日，生成PDF副本'}});fireEvent.click(ui.getByRole('button',{name:'处理所选资料'}));
 await waitFor(()=>assert.ok(task));assert.equal(task!.conversationId,conversation.id);assert.deepEqual(task!.fileIds,[output.id]);assert.equal(task!.format,'pdf');assert.equal(task!.messageIds,undefined);
});

test('附件导入失败展示具体原因，未发送模型也未伪造文件产物',async()=>{
 setup();let calls=0;window.mailFiles!.importLocal=async()=>({ok:false,error:'扫描件没有可提取文字，请先 OCR。'});window.mailOperations.startFileTask=async()=>{calls++;return ok(job);};
 const ui=render(<AIHistoryPanel onReview={()=>{}}/>);fireEvent.click(ui.getByLabelText('读取资料／生成文件'));fireEvent.click(ui.getByRole('button',{name:'添加本地资料'}));
 await waitFor(()=>assert.ok(ui.getByRole('alert').textContent?.includes('扫描件')));assert.equal(calls,0);assert.equal(ui.queryByRole('button',{name:'加入邮件附件'}),null);
});

test('写信附件默认不交给 AI，显式参考后才发送；预览固定附件版本，修改选择撤销确认',async()=>{
 setup();const generated:any[]=[];let sent=0;window.mailCompose.generate=async value=>{generated.push(value);return ok({subject:receipt.subject,body:receipt.body});};window.mailCompose.send=async()=>{sent++;return ok({...receipt,status:'sent'});};
 const ui=render(<MailComposePanel accounts={[account]}/>);await readyCompose(ui);fillCompose(ui);
 const picker=within(ui.getByRole('region',{name:'待发送附件'}));await waitFor(()=>assert.ok(picker.getByRole('checkbox',{name:/项目计划.docx/})));fireEvent.click(picker.getByRole('checkbox',{name:/项目计划.docx/}));
 fireEvent.click(ui.getByRole('button',{name:'AI 起草／润色'}));await waitFor(()=>assert.equal(generated.length,1));assert.equal(generated[0].fileIds,undefined);
 fireEvent.click(ui.getByLabelText('AI 起草时参考附件内容'));fireEvent.click(ui.getByRole('button',{name:'AI 起草／润色'}));await waitFor(()=>assert.equal(generated.length,2));assert.deepEqual(generated[1].fileIds,[output.id]);
 await waitFor(()=>assert.equal((ui.getByRole('button',{name:'预览并准备发送'}) as HTMLButtonElement).disabled,false));fireEvent.click(ui.getByRole('button',{name:'预览并准备发送'}));await waitFor(()=>assert.ok(ui.getByRole('button',{name:'确认发送这封邮件'})));
 const preview=within(ui.getByLabelText('发送前预览'));assert.ok(preview.getByText(/本次实际附件（1）/));assert.ok(preview.getByText(/bbbbbbbbbbbbbbbb/));assert.equal(sent,0);
 fireEvent.click(picker.getByRole('checkbox',{name:/项目计划.docx/}));assert.equal(ui.queryByRole('button',{name:'确认发送这封邮件'}),null);assert.equal(sent,0);
});

function convertedPdf(word:FileRecord, id:string, name:string):FileRecord {
 return {...input,id,name,source:'generated',conversion:{
  sourceFileId:word.id,sourceSha256:word.sha256,sourceRevision:word.revision??1,
  method:'LibreOffice writer_pdf_Export',preservesSourceLayout:true,visualReviewed:false,
 }};
}

async function prepareWithAttachments(attachments:FileRecord[]) {
 setup();
 let sent=0;
 window.mailCompose.send=async()=>{sent++;return ok({...receipt,status:'sent'});};
 // The picker has a cached view without conversion data. Only the prepared
 // receipt supplies the provenance of the actual bytes selected for sending.
 const pickerRecords=attachments.map(({id,name,mediaType,extension,byteSize,sha256,createdAt,source,warnings})=>
  ({id,name,mediaType,extension,byteSize,sha256,createdAt,source,warnings}));
 window.mailFiles!.list=async()=>ok(pickerRecords);
 window.mailFiles!.get=async id=>ok(pickerRecords.find(file=>file.id===id)!);
 const ids=attachments.map(file=>file.id);
 window.mailCompose.prepare=async value=>{
  assert.deepEqual(value.attachmentIds,ids);
  return ok({...receipt,attachments});
 };
 const ui=render(<MailComposePanel accounts={[account]} initialDraft={{
  id:'manual-pairing',bindingId:account.id,to:receipt.to,subject:receipt.subject,body:receipt.body,attachmentIds:ids,
 }}/>);
 await readyCompose(ui);
 fireEvent.click(ui.getByRole('button',{name:'预览并准备发送'}));
 const panel=await ui.findByLabelText('发送前预览');
 await waitFor(()=>assert.equal((ui.getByRole('button',{name:'确认发送这封邮件'}) as HTMLButtonElement).disabled,false));
 assert.equal(sent,0);
 return {ui,attachmentRow:(name:string)=>{
  const row=within(panel).getByText((_,node)=>node?.tagName==='P'&&node.textContent?.startsWith(`${name} · `)===true);
  return within(row);
 }};
}

test('发送预览为多组附件分别匹配各自的 Word，允许 PDF 使用不同名称',async()=>{
 const otherWord={...output,id:'word-b',name:'预算.docx',sha256:'c'.repeat(64),revision:3};
 const planPdf=convertedPdf(output,'plan-pdf','对外版本.pdf');
 const budgetPdf=convertedPdf(otherWord,'budget-pdf','预算附件.pdf');
 const {attachmentRow}=await prepareWithAttachments([otherWord,planPdf,output,budgetPdf]);
 assert.ok(attachmentRow(planPdf.name).getByText('配套'));
 assert.ok(attachmentRow(planPdf.name).getByText(/项目计划\.docx/));
 assert.ok(attachmentRow(budgetPdf.name).getByText('配套'));
 assert.ok(attachmentRow(budgetPdf.name).getByText(/预算\.docx/));
});

test('发送预览不会把同名同哈希的不同 Word 文件当成源 Word',async()=>{
 const sameNameWord={...output,id:'same-name-word'};
 const pdf=convertedPdf(output,'plan-pdf','项目计划.pdf');
 const {attachmentRow}=await prepareWithAttachments([sameNameWord,pdf]);
 assert.ok(attachmentRow(pdf.name).getByText('不匹配'));
 assert.ok(attachmentRow(pdf.name).getByText(/未附源 Word/));
});

test('发送预览标记源 Word 已附但哈希或修订版本与转换时不同',async()=>{
 const word={...output,revision:2};
 const staleHash=convertedPdf({...word,sha256:'c'.repeat(64)},'old-hash','旧内容.pdf');
 const staleRevision=convertedPdf({...word,revision:1},'old-revision','旧修订.pdf');
 const {attachmentRow}=await prepareWithAttachments([word,staleHash,staleRevision]);
 assert.ok(attachmentRow(staleHash.name).getByText('不匹配'));
 assert.ok(attachmentRow(staleHash.name).getByText(/哈希/));
 assert.ok(attachmentRow(staleRevision.name).getByText('不匹配'));
 assert.ok(attachmentRow(staleRevision.name).getByText(/修订版本/));
});

test('缺少转换记录的同名 PDF 显示来源未知，普通附件仍可预览确认',async()=>{
 const untrackedPdf={...input,name:'项目计划.pdf',parentIds:[output.id]};
 const textFile={...input,id:'notes',name:'说明.txt',mediaType:'text/plain',extension:'txt'};
 const {attachmentRow}=await prepareWithAttachments([output,untrackedPdf,textFile]);
 assert.ok(attachmentRow(untrackedPdf.name).getByText('来源未知'));
 assert.equal(attachmentRow(textFile.name).queryByText('来源未知'),null);
});

test('只附有转换记录的 PDF 会提示未附源 Word，依然可手动确认发送',async()=>{
 const pdf=convertedPdf(output,'plan-pdf','项目计划.pdf');
 const {ui,attachmentRow}=await prepareWithAttachments([pdf]);
 assert.ok(attachmentRow(pdf.name).getByText('未附源 Word'));
 assert.equal(attachmentRow(pdf.name).queryByText('不匹配'),null);
 assert.ok(ui.getByRole('button',{name:'确认发送这封邮件'}));
});

test('发送结果不确定时附件选择也锁定，外部加入附件请求不能解锁或改变原发送快照',async()=>{
 setup();window.mailCompose.send=async()=>ok({...receipt,status:'uncertain',attachments:[output],detail:'可能已被接受'});
 const ui=render(<MailComposePanel accounts={[account]} attachmentRequest={{token:'1',id:output.id}}/>);await readyCompose(ui);fillCompose(ui);fireEvent.click(ui.getByRole('button',{name:'预览并准备发送'}));await waitFor(()=>assert.ok(ui.getByRole('button',{name:'确认发送这封邮件'})));fireEvent.click(ui.getByRole('button',{name:'确认发送这封邮件'}));await waitFor(()=>assert.ok(ui.getByText('发送结果不确定')));
 ui.rerender(<MailComposePanel accounts={[account]} attachmentRequest={{token:'2',id:input.id}}/>);await waitFor(()=>assert.ok(ui.getByRole('alert').textContent?.includes('新写一封')));assert.equal(ui.queryByRole('button',{name:'确认发送这封邮件'}),null);assert.equal((ui.getByRole('checkbox',{name:/老师要求.pdf/}) as HTMLInputElement).disabled,true);assert.ok(within(ui.getByLabelText('发送前预览')).getByText(/本次实际附件（1）/));
});

test('读取邮件附件仅传递点选的邮件和附件索引，并在同一来源上下文里生成文档',async()=>{
 setup();const mail:LocalMail={localId:'mail-a',bindingId:account.id,mailboxIdentity:'a',accountEmail:'student@example.com',cachedAt:input.createdAt,analyzedAt:null,analysisId:null,status:'unanalyzed',id:'remote-a',subject:'项目要求',sender:'teacher@example.com',receivedAt:input.createdAt,preview:'请修订',body:'请修订附件',unread:false,webUrl:'',imported:false,attachments:[{name:'老师要求.pdf',size:3000,type:'application/pdf',inline:false}]};
 let imported:any,task:FileTaskInput|undefined;window.mailFiles!.importAttachment=async(localId,index)=>{imported={localId,index};return ok({...input,source:'mail',sourceMailId:mail.localId});};window.mailOperations.startFileTask=async value=>{task=value;return ok(job);};
 window.mailWorkspace={getAccounts:async()=>ok([account]),listPresets:async()=>ok([]),listFoldersLocal:async()=>ok([]),listLocal:async()=>ok({messages:[mail],total:1}),getLocal:async()=>ok(mail)} as unknown as import('../src/shared/mail-workspace').MailWorkspaceAPI;
 const ui=render(<MailWorkspacePanel onClose={()=>{}}/>);await waitFor(()=>assert.equal((ui.getByRole('button',{name:'写邮件'}) as HTMLButtonElement).disabled,false));fireEvent.click(ui.getByText('项目要求').closest('button')!);await waitFor(()=>assert.ok(ui.getByRole('button',{name:'读取附件并处理'})));assert.equal(imported,undefined);fireEvent.click(ui.getByRole('button',{name:'读取附件并处理'}));
 await waitFor(()=>assert.deepEqual(imported,{localId:mail.localId,index:0}));await waitFor(()=>assert.ok(ui.getByLabelText('想让 AI 帮你做什么')));fireEvent.change(ui.getByLabelText('想让 AI 帮你做什么'),{target:{value:'总结要求'}});fireEvent.click(ui.getByRole('button',{name:'处理所选资料'}));await waitFor(()=>assert.ok(task));assert.deepEqual(task!.messageIds,[mail.localId]);assert.deepEqual(task!.fileIds,[input.id]);assert.equal(task!.format,'none');
});

test('本地文件尚在导入时不能提交不完整资料或切换模式，完成后才允许处理',async()=>{
 setup();let resolveImport!:(value:ReturnType<typeof ok<FileRecord[]>>)=>void;window.mailFiles!.importLocal=()=>new Promise(resolve=>{resolveImport=resolve;});let calls=0;window.mailOperations.startFileTask=async()=>{calls++;return ok(job);};
 const ui=render(<AIHistoryPanel onReview={()=>{}}/>);fireEvent.click(ui.getByLabelText('读取资料／生成文件'));fireEvent.change(ui.getByLabelText('想让 AI 帮你做什么'),{target:{value:'总结这份文件'}});fireEvent.click(ui.getByRole('button',{name:'添加本地资料'}));
 await waitFor(()=>assert.equal((ui.getByRole('button',{name:'处理所选资料'}) as HTMLButtonElement).disabled,true));assert.equal((ui.getByLabelText('读取资料／生成文件') as HTMLInputElement).disabled,true);assert.equal(calls,0);
 resolveImport(ok([input]));await waitFor(()=>assert.equal((ui.getByRole('button',{name:'处理所选资料'}) as HTMLButtonElement).disabled,false));fireEvent.click(ui.getByRole('button',{name:'处理所选资料'}));await waitFor(()=>assert.equal(calls,1));
});
