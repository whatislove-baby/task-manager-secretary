"""Encrypted multi-account authentication; IMAP and public-client OAuth PKCE."""
import base64,hashlib,json,re,secrets,time,webbrowser
from copy import deepcopy
from http.server import BaseHTTPRequestHandler,HTTPServer
from socketserver import TCPServer
from pathlib import Path
from urllib.parse import parse_qs,urlencode,urlsplit
from uuid import uuid4
from . import vault
from .locks import file_lock
from functools import wraps
from .archive import text
from .tree import identifier,strict
from .settings import atomic_json
from .transport import https_request
from .mail_types import DEFAULT_FILTER,filter_value
from .mail_store import digest

MICROSOFT_SCOPES='openid profile offline_access https://graph.microsoft.com/User.Read https://graph.microsoft.com/Mail.Read'


class LoopbackCallbackServer(HTTPServer):
    def server_bind(self):
        # HTTPServer normally reverse-resolves its address. This listener is
        # always loopback; DNS adds no information and can stall on macOS VPNs.
        TCPServer.server_bind(self)
        self.server_name='localhost';self.server_port=self.server_address[1]


def config_value(value):
    strict(value,('email','username','host','port','security','password'),('email','host','port','security'))
    result=deepcopy(value);email=text(result['email'],320,True).strip()
    if not re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+',email): raise ValueError('请输入有效邮箱地址')
    host=text(result['host'],253,True).strip().rstrip('.').encode('idna').decode().lower()
    if re.search(r'[\s/@\\?#]',host) or not re.fullmatch(r'[a-z0-9.:-]+',host): raise ValueError('请输入有效IMAP主机名或IP地址')
    if type(result['port']) is not int or not 1<=result['port']<=65535 or result['security'] not in ('tls','starttls'): raise ValueError('无效IMAP端口或加密方式')
    return dict(email=email,username=text(result.get('username',''),320).strip() or email,host=host,port=result['port'],security=result['security'],password=text(result.get('password',''),1000))


def imap_account(config): return dict(id='imap:'+digest([config['host'],config['port'],config['security'],config['username']]),email=config['email'],name=config['email'])


def write_encrypted(path,value):
    path.parent.mkdir(parents=True,exist_ok=True);temp=path.with_name(path.name+'.tmp');temp.write_bytes(base64.b64decode(vault.encrypt(json.dumps(value,ensure_ascii=False))));temp.replace(path)


def read_encrypted(path,default):
    if not path.exists(): return deepcopy(default)
    try: return json.loads(vault.decrypt(base64.b64encode(path.read_bytes()).decode()))
    except Exception: raise ValueError('邮箱本机配置无法解密或已损坏，请备份后重新保存或授权') from None


def auth_write(function):
    @wraps(function)
    def wrapped(self,id,*args,**kwargs):
        with file_lock(self.auth_file(id).with_suffix('.lock')):
            return function(self,id,*args,**kwargs)
    return wrapped


class AccountManager:
    def __init__(self,directory,reader_factory=None,request=https_request):
        self.directory=Path(directory);self.file=self.directory/'mail-accounts.json';self.reader_factory=reader_factory;self.request=request;self.generations={}
        if self.file.exists():
            try:
                self.entries=json.loads(self.file.read_text(encoding='utf-8-sig'))
                if not isinstance(self.entries,list) or len(self.entries)>100 or len({v['id'] for v in self.entries})!=len(self.entries): raise ValueError()
                for value in self.entries:
                    strict(value,('id','method'),('id','method'))
                    if value['method'] not in ('imap','microsoft'): raise ValueError()
                    if value['id'].startswith('legacy-'):
                        if value['id']!='legacy-'+value['method']: raise ValueError()
                    else: identifier(value['id'])
            except Exception: raise ValueError('多邮箱配置损坏，请先备份data并检查mail-accounts.json') from None
        else:
            self.entries=[dict(id='legacy-'+method,method=method) for method,file in [('imap','imap-auth.bin'),('microsoft','outlook-auth.bin')] if (self.directory/file).exists()]
            atomic_json(self.file,self.entries)

    def refresh(self):
        self.entries=AccountManager(self.directory,self.reader_factory,self.request).entries

    def entry(self,id):
        self.refresh()
        for value in self.entries:
            if value['id']==id: return value
        raise ValueError('找不到此邮箱绑定')
    def path(self,id):
        self.entry(id)
        return self.directory if id.startswith('legacy-') else self.directory/'mail-accounts'/id
    def auth_file(self,id): return self.path(id)/('imap-auth.bin' if self.entry(id)['method']=='imap' else 'outlook-auth.bin')
    def read(self,id):
        default=dict(config=None,connected=False,filters={}) if self.entry(id)['method']=='imap' else dict(clientId='',account=None,filters={},cache='')
        value=read_encrypted(self.auth_file(id),default)
        if not isinstance(value,dict) or not isinstance(value.get('filters'),dict): raise ValueError('邮箱配置结构无效')
        if self.entry(id)['method']=='imap':
            if value.get('config') is not None: value['config']=config_value(value['config'])
            if type(value.get('connected')) is not bool or (value['connected'] and (not value.get('config') or not value['config']['password'])): raise ValueError('IMAP连接配置缺失')
        else:
            if value.get('clientId'): identifier(value['clientId'])
        return value
    def write(self,id,value): write_encrypted(self.auth_file(id),value)
    def get(self,id):
        method=self.entry(id)['method'];value=self.read(id)
        if method=='imap':
            config=value['config'];account=imap_account(config) if config else None
            state=dict(method=method,clientId='',account=account if value['connected'] else None,filter=deepcopy(value['filters'].get(account['id'],DEFAULT_FILTER) if account else DEFAULT_FILTER))
            if config: state['imap']={k:v for k,v in config.items() if k!='password'};state['imap']['hasSecret']=bool(config['password'])
        else:
            account=value.get('account');state=dict(method=method,clientId=value.get('clientId',''),account=account,filter=deepcopy(value['filters'].get(account['id'],DEFAULT_FILTER) if account else DEFAULT_FILTER))
        return dict(id=id,method=method,state=state)
    def all(self):
        self.refresh()
        return [self.get(value['id']) for value in self.entries]
    def add(self,method):
        with file_lock(self.directory/'mail-accounts.lock'):
            self.refresh()
            if method not in ('imap','microsoft') or len(self.entries)>=100:raise ValueError('无效邮箱类型或数量超过限制')
            value=dict(id=str(uuid4()),method=method);self.entries.append(value);atomic_json(self.file,self.entries)
        return self.get(value['id'])
    def ensure_legacy(self,method):
        id='legacy-'+method
        with file_lock(self.directory/'mail-accounts.lock'):
            self.refresh()
            if not any(v['id']==id for v in self.entries):self.entries.append(dict(id=id,method=method));atomic_json(self.file,self.entries)
        return id
    def remove(self,id):
        self.disconnect(id)
        with file_lock(self.directory/'mail-accounts.lock'):
            self.refresh();self.entries=[v for v in self.entries if v['id']!=id];atomic_json(self.file,self.entries)
    def commit(self,id,before,generation,changes):
        with file_lock(self.auth_file(id).with_suffix('.lock')):
            if generation!=self.generations.get(id,0) or self.read(id)!=before:raise ValueError('操作期间邮箱设置已变化，未保存迟到结果')
            value={**before,**changes};self.write(id,value)
        return value
    @auth_write
    def configure_imap(self,id,value):
        if self.entry(id)['method']!='imap': raise ValueError('此绑定不使用IMAP')
        valid=config_value(value);state=self.read(id);old=state['config']
        if not valid['password']:
            if not old or any(old[k]!=valid[k] for k in ('email','username','host','port','security')) or not old['password']: raise ValueError('更换服务器或账户后需要重新填写授权码或密码')
            valid['password']=old['password']
        self.generations[id]=self.generations.get(id,0)+1;state.update(config=valid,connected=False);self.write(id,state);return self.get(id)
    @auth_write
    def configure_microsoft(self,id,client):
        if self.entry(id)['method']!='microsoft': raise ValueError('此绑定不使用Microsoft授权')
        identifier(client);state=self.read(id);self.generations[id]=self.generations.get(id,0)+1
        if state['clientId']!=client: state.update(clientId=client,account=None,cache='');state.pop('tokens',None)
        self.write(id,state);return self.get(id)
    @auth_write
    def disconnect(self,id):
        self.generations[id]=self.generations.get(id,0)+1;state=self.read(id)
        if self.entry(id)['method']=='imap':
            state['connected']=False
            if state['config']: state['config']['password']=''
        else: state.update(account=None,cache='');state.pop('tokens',None)
        self.write(id,state);return self.get(id)
    @auth_write
    def save_filter(self,id,value):
        value=filter_value(value);account=self.get(id)['state']['account']
        if not account: raise ValueError('请先连接此邮箱')
        state=self.read(id);state['filters'][account['id']]=value;self.write(id,state);return self.get(id)
    def identity(self,id):
        state=self.get(id)['state'];account=state['account']
        if not account: raise ValueError('请先连接此邮箱')
        return dict(identity=account['id'],email=account['email'],filter=state['filter'])
    def check_identity(self,id,identity):
        if self.identity(id)['identity']!=identity: raise ValueError('邮箱身份已变化，请重新导入')
    def reader(self,id):
        if self.reader_factory: return self.reader_factory(self,id)
        from .mail_readers import ImapReader,GraphReader
        return ImapReader(self,id) if self.entry(id)['method']=='imap' else GraphReader(self,id)
    def connect(self,id):
        state=self.read(id);generation=self.generations.get(id,0)
        if self.entry(id)['method']=='imap':
            if not state['config'] or not state['config']['password']: raise ValueError('请先保存IMAP服务器、账户及授权码或密码')
            self.reader(id).test_connection()
            if generation!=self.generations.get(id,0) or self.read(id)!=state: raise ValueError('连接期间设置已变化，请重新连接')
            self.commit(id,state,generation,dict(connected=True))
        else:
            if not state.get('clientId'): raise ValueError('请先配置微软公开客户端ID')
            tokens=self.oauth_login(state['clientId'],lambda:generation==self.generations.get(id,0) and self.read(id)==state)
            profile=json.loads(self.request('https://graph.microsoft.com/v1.0/me?$select=id,displayName,mail,userPrincipalName',None,{'Authorization':'Bearer '+tokens['access_token']},2*1024*1024,20))
            account_id=profile['id']
            if tokens.get('client_info'):
                info=json.loads(base64.urlsafe_b64decode(tokens['client_info']+'='*(-len(tokens['client_info'])%4)));account_id=info['uid']+'.'+info['utid']
            account=dict(id=account_id,email=profile.get('mail') or profile.get('userPrincipalName',''),name=profile.get('displayName',''))
            if generation!=self.generations.get(id,0) or self.read(id)!=state: raise ValueError('授权期间配置已变化，未保存')
            self.commit(id,state,generation,dict(account=account,tokens={**tokens,'expires_at':time.time()+int(tokens.get('expires_in',3600))}))
        return self.get(id)
    def token_request(self,client,payload):
        body=urlencode(dict(client_id=client,scope=MICROSOFT_SCOPES,**payload)).encode()
        try:
            result=json.loads(self.request('https://login.microsoftonline.com/organizations/oauth2/v2.0/token',body,{'Content-Type':'application/x-www-form-urlencoded'},2*1024*1024,20))
            if not result.get('access_token'): raise ValueError()
            return result
        except Exception: raise ValueError('Microsoft授权未完成或已失效，请重新连接；学校可能要求管理员批准') from None
    def get_token(self,id):
        state=self.read(id)
        if not state.get('account'): raise ValueError('请先连接Microsoft邮箱')
        tokens=state.get('tokens',{})
        if tokens.get('access_token') and tokens.get('expires_at',0)>time.time()+120: return tokens['access_token']
        refresh=tokens.get('refresh_token')
        if not refresh and state.get('cache'):
            try:
                cache=json.loads(state['cache'])
                refresh=next(v['secret'] for v in cache.get('RefreshToken',{}).values() if v.get('client_id')==state['clientId'] and v.get('home_account_id')==state['account']['id'])
            except Exception: pass
        if not refresh: raise ValueError('Microsoft登录已失效，请重新连接')
        generation=self.generations.get(id,0);new=self.token_request(state['clientId'],dict(grant_type='refresh_token',refresh_token=refresh))
        if self.generations.get(id,0)!=generation or self.read(id)!=state: raise ValueError('授权配置已变化，未保存令牌')
        self.commit(id,state,generation,dict(tokens={**tokens,**new,'refresh_token':new.get('refresh_token',refresh),'expires_at':time.time()+int(new.get('expires_in',3600))}));return new['access_token']
    def oauth_login(self,client,valid):
        verifier=secrets.token_urlsafe(64);challenge=base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip('=');state=secrets.token_urlsafe(32);received={}
        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                parsed=urlsplit(self.path);params=parse_qs(parsed.query)
                if parsed.path!='/' or params.get('state')!=[state]:
                    self.send_response(400);self.end_headers();return
                received.update({k:v[0] for k,v in params.items()});self.send_response(200);self.send_header('Content-Type','text/plain; charset=utf-8');self.end_headers();self.wfile.write('授权已处理，请返回Task Manager。'.encode())
            def log_message(self,*args): pass
        with LoopbackCallbackServer(('127.0.0.1',0),Handler) as server:
            server.timeout=0.5;redirect=f'http://localhost:{server.server_port}'
            url='https://login.microsoftonline.com/organizations/oauth2/v2.0/authorize?'+urlencode(dict(client_id=client,response_type='code',redirect_uri=redirect,response_mode='query',scope=MICROSOFT_SCOPES,state=state,code_challenge=challenge,code_challenge_method='S256',prompt='select_account'))
            webbrowser.open(url);end=time.monotonic()+180
            while not received and time.monotonic()<end and valid(): server.handle_request()
        if not valid(): raise ValueError('Microsoft连接已取消')
        if not received.get('code'): raise ValueError('Microsoft授权取消、失败或超时，请重新连接')
        return self.token_request(client,dict(grant_type='authorization_code',code=received['code'],redirect_uri=redirect,code_verifier=verifier))
