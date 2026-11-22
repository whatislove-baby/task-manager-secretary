import React, { useId, useState } from 'react';
import type { Due } from '../shared/todo';
import { fromLocalMinute, toLocalMinute } from './date-fields';
export function DueEditor({value,onChange,child,disabled=false}:{value:Due;onChange:(value:Due)=>void;child:boolean;disabled?:boolean}) {
  const id=useId();const [error,setError]=useState('');
  const mode=value.mode==='value'?value.value.kind:value.mode;
  return <div className="due-editor"><label htmlFor={id}>截止方式</label><select id={id} value={mode} disabled={disabled} onChange={event=>{
    setError('');const next=event.target.value;
    if(next==='none'||next==='inherit')onChange({mode:next});
    else if(next==='date')onChange({mode:'value',value:{kind:'date',date:''}});
    else onChange({mode:'value',value:{kind:'minute',at:'',timeZone:Intl.DateTimeFormat().resolvedOptions().timeZone}});
  }}><option value="none">不设截止时间</option>{child&&<option value="inherit">共享父项截止时间</option>}<option value="date">仅日期</option><option value="minute">日期和时间</option></select>
  {value.mode==='value'&&value.value.kind==='date'&&<label className="due-input">截止日期<input type="date" required disabled={disabled} value={value.value.date} onChange={event=>onChange({mode:'value',value:{kind:'date',date:event.target.value}})}/></label>}
  {value.mode==='value'&&value.value.kind==='minute'&&<label className="due-input">截止时间<input type="datetime-local" step="60" required disabled={disabled} value={toLocalMinute(value.value.at)} onChange={event=>{
    try{const next=fromLocalMinute(event.target.value);setError('');onChange({mode:'value',value:next});}
    catch(err){setError((err as Error).message);onChange({mode:'value',value:{kind:'minute',at:'',timeZone:Intl.DateTimeFormat().resolvedOptions().timeZone}});}
  }}/><small>按本机时区 {Intl.DateTimeFormat().resolvedOptions().timeZone}</small></label>}
  {mode==='inherit'&&<small className="field-hint">随父项同步，不重复显示日期。</small>}{error&&<p role="alert" className="form-error">{error}</p>}</div>;
}
