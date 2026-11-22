import React from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import type {AgentSource} from '../shared/agent';

/** No raw HTML, remote images or privileged navigation from model-generated text. */
export function AgentMessageBody({text,sources=[],onSource}:{text:string;sources?:AgentSource[];onSource?(source:AgentSource):void}) {
 function sourceFor(href:string|undefined){
  if(!href)return undefined;
  const match=href.match(/^(mail|file|task|learning|skill):(?:\/\/)?(.+)$/);
  if(!match)return undefined;
  try{return sources.find(source=>source.kind===match[1]&&source.id===decodeURIComponent(match[2]));}catch{return undefined;}
 }
 return <div className="ag-markdown"><ReactMarkdown remarkPlugins={[remarkGfm]} skipHtml
  urlTransform={url=>sourceFor(url)||/^https?:\/\//i.test(url)?url:''}
  components={{
   img:({alt})=><span className="ag-inline-url">{alt?'［图片：'+alt+'］':'［图片］'}</span>,
   a:({href,children})=>{const source=sourceFor(href);return source&&onSource?<button className="ag-inline-source" onClick={()=>onSource(source)}>{children}</button>:<span>{children}{href&&<span className="ag-inline-url"> ({href})</span>}</span>;},
   table:({children})=><div className="ag-table"><table>{children}</table></div>,
   pre:({children})=><div className="ag-code"><pre>{children}</pre></div>,
  }}>{text}</ReactMarkdown></div>;
}
