import type { MailFailureStage } from '../shared/mail';

/** 邮件读取错误带阶段与稳定代码；消息面向用户，不包含凭据或正文。 */
export class MailReadError extends Error {
  constructor(message:string, public stage:MailFailureStage, public code:string, public retryable=true){
    super(message);
    this.name='MailReadError';
  }
}

const codes:Record<MailFailureStage,string>={
  list:'LIST_FAILED', identity:'IDENTITY_MISMATCH', transfer:'TRANSFER_FAILED',
  structure:'STRUCTURE_INVALID', parse:'PARSE_FAILED', save:'SAVE_FAILED',
};

export function mailFailure(error:unknown):{stage:MailFailureStage;code:string;reason:string;retryable:boolean} {
  if(error instanceof MailReadError) return {stage:error.stage,code:error.code,reason:error.message,retryable:error.retryable};
  const message=error instanceof Error?error.message:'邮件读取失败。';
  return {stage:'transfer',code:codes.transfer,reason:message.replace(/(?:https?|webcal):\/\/[^\s]+/gi,'［地址］').replace(/Bearer\s+[^\s]+/gi,'［凭据］'),retryable:true};
}
