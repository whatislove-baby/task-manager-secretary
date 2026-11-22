import { readFileSync, writeFileSync, renameSync, existsSync } from 'node:fs';
import { join } from 'node:path';
import { z } from 'zod';
import { SettingsInput, Settings, settingsInputSchema } from '../shared/contracts';
export interface KeyVault { available(): Promise<boolean>; encrypt(value: string): Promise<Buffer>; decrypt(value: Buffer): Promise<{ result: string; shouldReEncrypt: boolean }> }
const storedSchema = z.strictObject({ baseUrl: z.string(), model: z.string(), encryptedKey: z.string().optional() });
type Stored = z.infer<typeof storedSchema>;
export class SettingsStore {
  private file: string;
  constructor(private directory: string, private vault: KeyVault) { this.file = join(directory, 'settings.json'); }
  private read(): Stored {
    if (!existsSync(this.file)) return { baseUrl:'', model:'' };
    try { return storedSchema.parse(JSON.parse(readFileSync(this.file, 'utf8'))); }
    catch { throw new Error('配置文件无法读取，请先备份数据目录并检查 settings.json。'); }
  }
  get(): Settings {
    const value = this.read();
    return { baseUrl:value.baseUrl, model:value.model, hasApiKey:Boolean(value.encryptedKey), dataDirectory:this.directory };
  }
  private write(value: Stored) { writeFileSync(this.file+'.tmp', JSON.stringify(value,null,2), { mode:0o600 }); renameSync(this.file+'.tmp', this.file); }
  async save(value: SettingsInput): Promise<Settings> {
    const input = settingsInputSchema.parse(value); const old = this.read();
    if (old.encryptedKey && old.baseUrl.replace(/\/$/,'') !== input.baseUrl.replace(/\/$/,'') && !input.apiKey) throw new Error('服务地址已改变，请重新输入对应服务的密钥。');
    let encryptedKey = old.encryptedKey;
    if (input.apiKey) { if (!await this.vault.available()) throw new Error('系统加密能力不可用，未保存密钥。'); encryptedKey = (await this.vault.encrypt(input.apiKey)).toString('base64'); }
    this.write({ baseUrl:input.baseUrl.replace(/\/$/,''), model:input.model, encryptedKey }); return this.get();
  }
  clearKey() { const value=this.read(); delete value.encryptedKey; this.write(value); }
  async forRequest() {
    const value=this.read(); if (!value.encryptedKey) throw new Error('请先配置AI服务、模型和密钥。');
    const decoded=await this.vault.decrypt(Buffer.from(value.encryptedKey,'base64'));
    const config=settingsInputSchema.parse({ baseUrl:value.baseUrl, model:value.model, apiKey:decoded.result });
    if (decoded.shouldReEncrypt) {
      const rotated=(await this.vault.encrypt(decoded.result)).toString('base64');
      // 解密期间用户可能已修改服务；密钥轮换不能覆盖较新的配置。
      const current=this.read();
      if (JSON.stringify(current)===JSON.stringify(value)) this.write({ ...value, encryptedKey:rotated });
    }
    return config;
  }
}
