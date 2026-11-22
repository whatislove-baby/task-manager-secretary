import type { TodoDraft } from '../shared/todo';

export interface DraftReviewSnapshot {
  drafts:TodoDraft[];
  selected:string[];
  token:string;
  saved:boolean;
  aiNotes:string;
  addedTempIds:string[];
  /** 打开时的原始草稿签名；重新打开的分析内容不同则不复用旧编辑。 */
  baseDrafts:string;
}

const snapshots=new Map<string,DraftReviewSnapshot>();

export function draftReviewKey(analysisId?:string,extractionToken?:string):string {
  if(analysisId)return 'analysis:'+analysisId;
  if(extractionToken)return 'extraction:'+extractionToken;
  return '';
}

export function loadDraftReview(key:string,baseDrafts:string):DraftReviewSnapshot|undefined {
  const value=snapshots.get(key);
  return value&&value.baseDrafts===baseDrafts?structuredClone(value):undefined;
}

export function saveDraftReview(key:string,snapshot:DraftReviewSnapshot):void {
  if(!key)return;
  snapshots.set(key,structuredClone(snapshot));
}

export function clearDraftReview(key:string):void {
  if(key)snapshots.delete(key);
}
