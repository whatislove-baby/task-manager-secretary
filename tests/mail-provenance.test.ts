import {test} from 'node:test';
import assert from 'node:assert/strict';
import {draftMailSources,provenanceNotes} from '../src/main/mail-provenance';
import type {TodoDraft} from '../src/shared/todo';
import type {LocalMail} from '../src/shared/mail-workspace';
const mail=(id:string,subject:string,body:string):LocalMail=>({id,localId:id,bindingId:'binding',accountEmail:'student@example.invalid',mailboxIdentity:'account',subject,body,sender:'teacher@example.invalid',receivedAt:'2026-10-29T01:35:00Z',preview:'',unread:true,webUrl:'',imported:true,cachedAt:'2026-10-31T00:00:00Z',analyzedAt:null,analysisId:null,status:'unanalyzed'});
const batch=[mail('one','Paper checking: Network Modeling','Please check the quiz paper.'),mail('two','Submission Posted: Quiz I.1','Grade changes for quiz one.'),mail('three','Submission Posted: Quiz I.2','Grade changes for quiz two.'),mail('four','Course reminder','Prepare for the next lecture.'),mail('five','Submission Posted: Mock Interview, MSDM5051','Your instructor has released grade changes and new comments for Mock Interview. Please log in to Canvas. Graded: Oct 29 at 9:31am.')];
const draft=(source:string,extra:Partial<TodoDraft>={}):TodoDraft=>({tempId:'1',parentTempId:null,title:'查看 Mock Interview 成绩与评语',notes:'保留用户原有备注。',due:{mode:'none'},source,...extra});

test('批次前几封不能代替实际来源，邮件5前缀与省略号证据归属唯一正确邮件',()=>{
 const value=draft('邮件5：Your instructor has released grade changes and new comments for Mock Interview... graded: Oct 29 at 9:31am');
 assert.deepEqual(draftMailSources(value,batch).map(m=>m.localId),['five']);
 const notes=provenanceNotes(value,batch,'analysis-fixture');
 assert.ok(notes.startsWith('保留用户原有备注。'));assert.ok(notes.includes(batch[4].subject));assert.ok(notes.includes(batch[4].receivedAt));assert.ok(!notes.includes(batch[0].subject));assert.ok(!notes.includes(batch[1].subject));assert.ok(!notes.includes(batch[2].subject));
});

test('逐字证据不受大小写和排版标点影响；有效显式ID仍须有原文证据',()=>{
 const value=draft('YOUR INSTRUCTOR HAS RELEASED GRADE CHANGES AND NEW COMMENTS FOR MOCK INTERVIEW.',{sourceMailIds:['five']});
 assert.deepEqual(draftMailSources(value,batch).map(m=>m.localId),['five']);
 const conflicting=draft('Please check the quiz paper.',{sourceMailIds:['five']});
 assert.deepEqual(draftMailSources(conflicting,batch).map(m=>m.localId),['one']);
 assert.ok(!provenanceNotes(conflicting,batch,'fixture').includes(batch[4].subject));
 const outside=draft('No evidence matches this fabricated text.',{sourceMailIds:['not-in-batch']});
 assert.deepEqual(draftMailSources(outside,batch),[]);assert.ok(provenanceNotes(outside,batch,'fixture').includes('待核实'));
});

test('跨邮件证据由有效ID引用且每封都有对应片段，不能夹带无关邮件',()=>{
 const value=draft('Please check the quiz paper... Prepare for the next lecture.',{sourceMailIds:['one','four']});
 assert.deepEqual(draftMailSources(value,batch).map(m=>m.localId),['one','four']);
 assert.deepEqual(draftMailSources({...value,sourceMailIds:['one','three','four']},batch),[]);
});

test('共用模板、空证据及无匹配不猜测具体来源',()=>{
 const duplicated=[mail('a','Notice A','Please check your account today.'),mail('b','Notice B','Please check your account today.')];
 for(const value of [draft('Please check your account today.'),draft(''),draft('Other entirely unrelated content')]){
  assert.deepEqual(draftMailSources(value,duplicated),[]);const notes=provenanceNotes(value,duplicated,'fixture');assert.ok(notes.includes('待核实'));assert.ok(!notes.includes('Notice A'));assert.ok(!notes.includes('Notice B'));
 }
});

test('单邮件批次确定其输入来源，保留原备注与原文链接',()=>{
 const only={...batch[4],webUrl:'https://outlook.office.com/mail/id/test'};
 const notes=provenanceNotes(draft(''),[only],'single');assert.ok(notes.includes(only.subject));assert.ok(notes.includes(only.webUrl));assert.ok(notes.includes('分析记录：single'));assert.ok(notes.startsWith('保留用户原有备注。'));
});

test('来源追加不得截断用户备注或超过待办备注长度边界',()=>{
 const full='原'.repeat(10000);assert.equal(provenanceNotes(draft('',{notes:full}),[batch[4]],'fixture'),full);
 const short=provenanceNotes(draft('',{notes:'原'.repeat(9500)}),[batch[4]],'fixture');assert.ok(short.length<=10000);assert.ok(short.startsWith('原'.repeat(9500)));assert.ok(short.includes(batch[4].subject));
});
