import type {AgentArtifact,AgentConversation,AgentRunStatus,AgentSource} from './agent';

const runLabels:Record<AgentRunStatus,string>={
 queued:'等待开始',running:'正在处理',waiting:'等待补充',paused:'已暂停',
 completed:'已完成',failed:'执行失败',canceled:'已停止',
};

function singleLine(text:string):string {
 return text.replace(/[\u0000-\u001f\u007f-\u009f\u2028-\u202e\u2066-\u2069]+/g,' ').trim();
}

function inlineText(text:string):string {
 const value=singleLine(text);
 // Use a code span for names that Markdown readers would autolink.
 if(/\b(?:[a-z]+:\/\/|www\.)|@/i.test(value)){
  const delimiter='`'.repeat(1+Math.max(0,...(value.match(/`+/g)??[]).map(run=>run.length)));
  return `${delimiter} ${value} ${delimiter}`;
 }
 return value.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;')
  .replace(/[\\`#*_\[\]()!~|]/g,'\\$&').replace(/^([-+])(?= )/,'\\$1').replace(/^(\d{1,9})\.(?= )/,'$1\\.');
}

export function conversationFilename(title:string):string {
 const cleaned=singleLine(title).replace(/[<>:"/\\|?*]/g,'-').replace(/^\.+|[. ]+$/g,'').trim();
 const stem=Array.from(cleaned).slice(0,60).join('').replace(/[. ]+$/g,'')||'对话';
 const reserved=/^(?:con|conin\$|conout\$|prn|aux|nul|com[1-9¹²³]|lpt[1-9¹²³]) *(?:\.|$)/i.test(stem);
 return (reserved?'_':'')+stem+'.md';
}

export function conversationMarkdown(conversation:AgentConversation,includeSources=false):string {
 const turns=conversation.turns.filter(turn=>turn.role==='user'||turn.role==='assistant')
  .sort((a,b)=>Date.parse(a.createdAt)-Date.parse(b.createdAt));
 const run=conversation.activeRun??conversation.lastRun;
 const clarification=run?.status==='waiting'?run.clarification:undefined;
 const sections:string[]=[];

 function appendTurn(heading:string,createdAt:string,content:string,sources:AgentSource[]=[],artifacts:AgentArtifact[]=[]){
  const time=inlineText(createdAt);
  sections.push('## '+heading+(time?' · '+time:''));
  if(content)sections.push(content);
  if(!includeSources)return;
  for(const [label,items] of [['来源',sources],['产物',artifacts]] as const){
   const names=items.map(item=>inlineText(item.title)).filter(Boolean);
   if(names.length)sections.push(label+'\n\n'+names.map(name=>'- '+name).join('\n'));
  }
 }

 for(const turn of turns){
  appendTurn(turn.role==='user'?'用户':'助手',turn.createdAt,turn.content,turn.sources,turn.artifacts);
 }
 if(run&&!turns.some(turn=>turn.role==='assistant'&&turn.runId===run.id)&&
    (run.partialText.trim()||clarification||(includeSources&&(run.sources.length||run.artifacts.length)))){
  const label=run.stopping?'正在停止':runLabels[run.status];
  const heading=run.status==='completed'?'助手':`助手（未完成 · ${label}）`;
  const content=[run.partialText];
  if(clarification){
   content.push(inlineText(clarification.question));
   if(clarification.options.length)content.push(clarification.options.map(option=>'- '+inlineText(option)).join('\n'));
  }
  appendTurn(heading,run.updatedAt,content.filter(Boolean).join('\n\n'),run.sources,run.artifacts);
 }

 if(!sections.length)return '';
 const title=inlineText(conversation.title)||'对话';
 return ['# '+title,'创建时间：'+inlineText(conversation.createdAt),...sections].join('\n\n')+'\n';
}
