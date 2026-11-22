"""Bounded NDJSON RPC. Network calls use isolated service connections off the input loop."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import re,sys,threading
from .service import BackendService
from .tree import strict

MAX_REQUEST=2*1024*1024
HEAVY={'mail-files:importAttachment','mail-files:importLocalFile','mail-files:read','mail-files:exportFile','mail-compose:generate','mail-compose:send','extract','syncCanvas','mail:connect','mail:read','mail:getMessage','mail:extract','mail:openMessage','mail:listFolders','mail-workspace:connect','mail-workspace:listFolders','mail-workspace:importMail','mail-workspace:analyze'}


def serve(directory,factory=None):
    factory=factory or BackendService;directory=Path(directory).resolve();directory.mkdir(parents=True,exist_ok=True);service=factory(directory);lock=threading.Lock();executor=ThreadPoolExecutor(max_workers=4);slots=threading.BoundedSemaphore(32)
    def emit(id,result):
        with lock:
            sys.stdout.write(json.dumps(dict(id=id,result=result),ensure_ascii=False)+'\n');sys.stdout.flush()
    def perform(value,isolated=False):
        backend=None
        try:
            backend=factory(directory,worker=True) if isolated else service
            data=backend.call(value['method'],*value['args']);emit(value['id'],dict(ok=True,data=data))
        except (ValueError,KeyError,TypeError) as error:
            message=str(error) if isinstance(error,ValueError) else '接口参数不正确';message=re.sub(r'(?:https?|webcal)://\S+|Bearer\s+\S+','［已隐藏］',message)
            emit(value['id'],dict(ok=False,error=message))
        except Exception:emit(value['id'],dict(ok=False,error='本地操作失败，请检查数据目录、连接和配置。'))
        finally:
            if isolated:
                if backend:backend.close()
                slots.release()
    try:
        while True:
            line=sys.stdin.buffer.readline(MAX_REQUEST+1)
            if not line:break
            request_id=None
            try:
                if len(line)>MAX_REQUEST:
                    while line and not line.endswith(b'\n'):line=sys.stdin.buffer.readline(MAX_REQUEST+1)
                    raise ValueError('请求超过2MiB限制')
                value=json.loads(line.decode('utf-8'));strict(value,('id','method','args'),('id','method','args'));request_id=value['id']
                if type(request_id) not in (int,str) or isinstance(request_id,str) and len(request_id)>100:request_id=None;raise ValueError('无效请求标识')
                if not isinstance(value['args'],list) or not isinstance(value['method'],str):raise ValueError('无效接口参数')
                if value['method']=='health' and value['args']==[]:
                    emit(request_id,dict(ok=True,data=dict(backend='python',protocol=1,schema=6,methods=sorted(service.METHODS),environment=dict(executable=sys.executable,prefix=sys.prefix,basePrefix=sys.base_prefix))))
                elif value['method'] in HEAVY:
                    if not slots.acquire(blocking=False):raise ValueError('并发请求过多，请稍后重试')
                    executor.submit(perform,value,True)
                else:perform(value)
            except (ValueError,TypeError,KeyError):emit(request_id,dict(ok=False,error='请求格式或参数无效'))
    finally:
        service.close();executor.shutdown(wait=True)


def main():
    sys.stdout.reconfigure(encoding='utf-8');sys.stderr.reconfigure(encoding='utf-8');parser=argparse.ArgumentParser();parser.add_argument('--data-dir',required=True);options=parser.parse_args()
    try:serve(options.data_dir)
    except Exception:
        print('Python后端无法启动；请检查数据库版本、结构及目录权限。',file=sys.stderr);return 1
    return 0


if __name__=='__main__':sys.exit(main())
