import React from 'react';
import {RefreshCw,Settings2} from 'lucide-react';
import type {AgentRun} from '../shared/agent';

type Guidance={title:string;next:string;settings?:boolean};
const guidance:Record<NonNullable<AgentRun['errorCode']>,Guidance>={
 connection:{title:'连接中断或超时',next:'检查网络连接，或等待模型服务恢复后，再继续本轮。'},
 rate_limit:{title:'模型服务暂时限制请求',next:'稍等片刻再继续；如果反复出现，请检查服务账户的额度和请求限制。',settings:true},
 authentication:{title:'模型服务认证未通过',next:'检查 API 密钥是否有效，以及账户是否有当前模型的访问权限。',settings:true},
 configuration:{title:'模型配置需要检查',next:'核对服务地址、模型名称和 API 密钥，再决定继续本轮或重新发起。',settings:true},
 execution_limit:{title:'本轮已达到执行限制',next:'先查看已完成的内容。可以继续本轮；若反复达到限制，请结束本轮，将任务拆分后再处理。'},
 model_response:{title:'模型回复未完整完成',next:'可以继续本轮；若反复失败，请检查模型是否支持工具调用，或将任务拆小。',settings:true},
 unknown:{title:'本轮未完成',next:'先查看上方已完成的步骤和结果，检查网络与模型配置后，再继续本轮。',settings:true},
};

export function AgentRunRecovery({run,busy,onContinue,onCancel,onSettings}:{run:AgentRun;busy:boolean;onContinue():void;onCancel():void;onSettings():void}){
 if(run.status!=='failed')return null;
 const help=guidance[run.errorCode??'unknown']??guidance.unknown;
 const disabled=busy||run.stopping;
 return <section className="ag-recovery" role="status" aria-label="执行恢复指引">
  <strong>{help.title}</strong>
  <p>{help.next}</p>
  <p className="ag-recovery-kept">已有文件、草稿和记录保留。“继续本轮”沿用当前任务，不另建对话。</p>
  {help.settings&&<p className="ag-recovery-note">如果更换了服务地址、模型或密钥，请结束本轮，再重新发起任务；已有产物仍保留。</p>}
  {run.stopping&&<p>正在结束上一次执行，请稍候。</p>}
  <div className="ag-recovery-actions">
   <button disabled={disabled} onClick={onContinue}><RefreshCw size={14}/>继续本轮</button>
   {help.settings&&<button disabled={busy||run.stopping} onClick={onSettings}><Settings2 size={14}/>打开模型设置</button>}
   <button disabled={disabled} onClick={onCancel}>结束本轮</button>
  </div>
 </section>;
}
