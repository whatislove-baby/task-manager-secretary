from pathlib import Path
import json
from urllib.parse import urlsplit
from .archive import text
from .tree import strict
from . import vault


def endpoint(value):
    text(value,8000,True)
    url=urlsplit(value)
    if url.scheme!='https' or not url.hostname or url.username or url.password or url.query or url.fragment: raise ValueError('服务地址须为不含账号、查询参数的HTTPS地址')
    return value.rstrip('/')


def atomic_json(path,value):
    temp=path.with_name(path.name+'.tmp')
    temp.write_text(json.dumps(value,ensure_ascii=False,indent=2),encoding='utf-8')
    temp.replace(path)


class Settings:
    def __init__(self,directory):
        self.directory=Path(directory);self.file=self.directory/'settings.json'

    def read(self):
        if not self.file.exists(): return dict(baseUrl='',model='')
        try:
            value=json.loads(self.file.read_text(encoding='utf-8-sig'))
            strict(value,('baseUrl','model','encryptedKey'),('baseUrl','model'))
            if any(not isinstance(v,str) for v in value.values()): raise ValueError()
            return value
        except Exception: raise ValueError('配置文件无法读取，请先备份数据目录并检查settings.json') from None

    def get(self):
        value=self.read()
        return dict(baseUrl=value['baseUrl'],model=value['model'],hasApiKey=bool(value.get('encryptedKey')),dataDirectory=str(self.directory))

    def save(self,value):
        strict(value,('baseUrl','model','apiKey'),('baseUrl','model','apiKey'))
        address=endpoint(value['baseUrl']);model=text(value['model'],150,True).strip();key=text(value['apiKey'],1000).strip()
        if not model: raise ValueError('模型名称不能为空')
        old=self.read()
        if old.get('encryptedKey') and old['baseUrl'].rstrip('/')!=address and not key: raise ValueError('服务地址已改变，请重新输入对应服务的密钥')
        result=dict(baseUrl=address,model=model)
        if key: result['encryptedKey']=vault.encrypt(key)
        elif old.get('encryptedKey'): result['encryptedKey']=old['encryptedKey']
        atomic_json(self.file,result);return self.get()

    def clear(self):
        value=self.read();value.pop('encryptedKey',None);atomic_json(self.file,value)

    def for_request(self):
        value=self.read()
        if not value.get('encryptedKey'): raise ValueError('请先配置AI服务、模型和密钥')
        return dict(baseUrl=endpoint(value['baseUrl']),model=text(value['model'],150,True),apiKey=vault.decrypt(value['encryptedKey']))
