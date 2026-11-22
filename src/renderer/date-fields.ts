import type { Deadline } from '../shared/todo';
export function toLocalMinute(at:string):string {
  const date=new Date(at);if(!Number.isFinite(date.getTime()))return '';
  return `${date.getFullYear()}-${String(date.getMonth()+1).padStart(2,'0')}-${String(date.getDate()).padStart(2,'0')}T${String(date.getHours()).padStart(2,'0')}:${String(date.getMinutes()).padStart(2,'0')}`;
}
export function fromLocalMinute(value:string):Extract<Deadline,{kind:'minute'}> {
  if(!/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}$/.test(value))throw new Error('请填写完整的日期和时间。');
  const date=new Date(value);
  if(!Number.isFinite(date.getTime())||toLocalMinute(date.toISOString())!==value)throw new Error('日期或时间不存在，请重新选择。');
  for(let delta=30;delta<=180;delta+=30){
    if([date.getTime()+delta*60000,date.getTime()-delta*60000].some(at=>toLocalMinute(new Date(at).toISOString())===value))throw new Error('该时间存在夏令时歧义，请选择明确的时刻。');
  }
  return {kind:'minute',at:date.toISOString(),timeZone:Intl.DateTimeFormat().resolvedOptions().timeZone};
}
