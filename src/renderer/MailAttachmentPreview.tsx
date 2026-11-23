import React from 'react';
import type {FileRecord} from '../shared/mail-files';
import {fileSize} from './MailFiles';

type PdfPairing = {
  label: '配套' | '不匹配' | '来源未知' | '未附源 Word';
  detail?: string;
};

function compareWithSourceWord(pdf: FileRecord, words: ReadonlyMap<string, FileRecord>): PdfPairing {
  const source = pdf.conversion;
  if (!source?.sourceFileId || !/^[a-f0-9]{64}$/.test(source.sourceSha256) ||
      !Number.isInteger(source.sourceRevision) || source.sourceRevision < 1) {
    return {label: '来源未知', detail: '缺少完整的 Word 转 PDF 记录'};
  }

  const word = words.get(source.sourceFileId);
  if (!word) {
    return words.size > 0 ? {label: '不匹配', detail: '未附源 Word'} : {label: '未附源 Word'};
  }

  const differences = [];
  if (word.sha256 !== source.sourceSha256) differences.push('内容哈希');
  // The converter records unmanaged Word files as revision 1.
  if ((word.revision ?? 1) !== source.sourceRevision) differences.push('修订版本');
  const wordLabel = `源 Word「${word.name}」`;
  return differences.length > 0
    ? {label: '不匹配', detail: `${wordLabel}的${differences.join('和')}与转换记录不同`}
    : {label: '配套', detail: wordLabel};
}

export function MailAttachmentPreview({attachments = []}: {attachments?: readonly FileRecord[]}) {
  const words = new Map(attachments.filter(file => file.extension === 'docx').map(file => [file.id, file]));
  return <div className="mf-snapshot">
    <strong>本次实际附件（{attachments.length}）</strong>
    {attachments.map(file => {
      const pairing = file.extension === 'pdf' ? compareWithSourceWord(file, words) : null;
      return <p key={file.id}>
        {file.name} · {fileSize(file.byteSize)}
        <small>版本 {file.sha256.slice(0, 16)}</small>
        {pairing && <small className={pairing.label === '不匹配' ? 'mf-warning' : undefined}>
          <strong>{pairing.label}</strong>{pairing.detail && <> · <span>{pairing.detail}</span></>}
        </small>}
      </p>;
    })}
  </div>;
}
