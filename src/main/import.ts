import { simpleParser } from 'mailparser';
import { extname, basename } from 'node:path';
import type { ImportedText } from '../shared/contracts';
export async function parseImportedText(path: string, content: Buffer): Promise<ImportedText> {
  if (content.length > 2 * 1024 * 1024) throw new Error('文件超过 2MB，请粘贴需要处理的正文。');
  const extension = extname(path).toLowerCase();
  if (!['.eml','.txt'].includes(extension)) throw new Error('只支持 .eml 或 UTF-8 .txt 文件。');
  let text: string;
  if (extension === '.eml') {
    const mail = await simpleParser(content, { skipHtmlToText:false, skipTextToHtml:true, skipImageLinks:true });
    text = [mail.subject ? `主题：${mail.subject}` : '', mail.date ? `邮件日期：${mail.date.toISOString()}` : '', mail.text || ''].filter(Boolean).join('\n\n');
  } else text = content.toString('utf8').replace(/^\uFEFF/, '');
  if (text.length > 20000) throw new Error('正文超过20000字符，请选择需要处理的片段。');
  if (!text.trim()) throw new Error('没有可读取的正文。');
  return { name: basename(path), text };
}
