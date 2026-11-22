import type {Result} from './contracts';
import type {LearningScope,LearningEvidence,LearningTeaching,ReviewInput} from './agent';

export type SkillKind='memory'|'script'|'hybrid';
export type SkillStatus='draft'|'approved'|'disabled'|'superseded';
export interface SkillContractInput {name:string;description:string}
export interface SkillContractOutput extends SkillContractInput {comparison:'table'|'workbook'|'text'|'bytes'}
export interface SkillExample {name:string;kind:'success'|'failure';inputFiles:Record<string,string>;expectedFiles:Record<string,string>;expectedError?:string}
export interface SkillValidation {
 passed:boolean;status:string;normalExamples?:number;failureExamples?:number;summary?:string;detail?:string;
 cases:{name:string;kind:'success'|'failure';passed:boolean;detail?:string;exitCode?:number|null;durationSeconds?:number;
  backend?:string;stdout?:string;stderr?:string;runnerError?:string|null;outputHashes?:Record<string,string>;expectedHashes?:Record<string,string>}[];
}
export interface SkillRecord {
 id:string;kind:'skill'|'skill_draft';title:string;scopeId:string;scope:LearningScope;version:number;versionHash:string;
 parentId?:string|null;status:SkillStatus;createdAt:string;reviewedAt?:string|null;packagePath?:string;
 canApprove:boolean;canRun?:boolean;
 proposal:{title:string;kind:SkillKind;rationale:string;instruction:string;markdown:string;code?:string|null;scope:LearningScope;
  inputs:SkillContractInput[];outputs:SkillContractOutput[];examples:SkillExample[];sources:LearningEvidence[];teaching?:LearningTeaching;
  packageManifest?:Record<string,string>;parentId?:string;
  importedPackage?:{schemaVersion:number;sourceFileId:string;sourceSha256:string;originalFiles:Record<string,string>;runtimeFiles:string[];supplement:string}};
 validation:SkillValidation;
}
export interface SkillsAPI {
 list(query?:{scopeId?:string;status?:SkillStatus;limit?:number}):Promise<Result<SkillRecord[]>>;
 get(id:string):Promise<Result<SkillRecord>>;
 importPackage(input:{fileId:string;scope:LearningScope;instruction?:string;parentId?:string}):Promise<Result<SkillRecord>>;
 approve(input:ReviewInput):Promise<Result<SkillRecord>>;
 disable(input:ReviewInput):Promise<Result<SkillRecord>>;
}
