import type {Result,Settings} from './contracts';

/** An immutable local copy. The renderer never receives its private filesystem path. */
export interface FileRecord {
 id:string; name:string; mediaType:string; extension:string; byteSize:number; sha256:string;
 createdAt:string; source:'local'|'mail'|'generated'; sourceMailId?:string;
 parentIds?:string[]; warnings:string[];
 revision?:number;
 conversion?:{sourceFileId:string;sourceSha256:string;sourceRevision:number;method:string;preservesSourceLayout:boolean;visualReviewed:boolean};
 validation?:FileValidation;
 pageSelection?:{sourceFileId:string;sourceSha256:string;sourcePageCount:number;pages:number[];pageCount:number;method:string};
 archiveOrigin?:{archiveId:string;rootArchiveId:string;archiveSha256:string;memberPath:string;memberSha256:string;depth:number};
 skill?:{versionId:string;versionHash:string;scopeId:string;approvedVersion:number;inputIds:string[];inputHashes:Record<string,string>;outputSha256:string};
 learning?:{versionId:string;versionHash:string;scopeId:string;inputId:string;inputSha256:string;outputId:string;outputSha256:string;approvedVersion:number;totals:Record<string,string>};
}
export interface FileValidation {sha256:string;structuralPassed:boolean;rendered:boolean;visualReviewed:boolean;pageCount?:number;rowCount?:number;columnCount?:number;checks:string[];warnings:string[]}
export interface FilePreview {fileId:string;sha256:string;page:number;pageCount:number;dataUrl:string;width:number;height:number;rendered:boolean}
export type FileCellValue=string|number|boolean|null;
export interface FileInspection {fileId?:string;format?:string;layout?:'table'|'worksheet';columns?:string[];rows?:FileCellValue[][];rowCount?:number;nextOffset?:number|null;warnings?:string[];sheets?:{name:string;rowCount?:number}[]}
export type FileOutputFormat='none'|'pdf'|'docx'|'md'|'txt';
export interface FileTaskInput {
 conversationId?:string; messageIds?:string[]; text:string; fileIds:string[];
 format:FileOutputFormat; name?:string; target:Pick<Settings,'baseUrl'|'model'>;
}
export interface MailFilesAPI {
 importLocal():Promise<Result<FileRecord[]|null>>;
 importAttachment(localId:string,index:number):Promise<Result<FileRecord>>;
 list():Promise<Result<FileRecord[]>>;
 get(id:string):Promise<Result<FileRecord>>;
 read(id:string):Promise<Result<string>>;
 export(id:string):Promise<Result<boolean>>;
 inspect?(id:string,offset?:number,limit?:number):Promise<Result<FileInspection>>;
 preview?(id:string,page?:number):Promise<Result<FilePreview>>;
 validate?(id:string):Promise<Result<FileValidation>>;
}
