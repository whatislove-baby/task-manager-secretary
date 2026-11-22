"""Bounded offline Python jobs in Docker; never falls back to host execution.

Only a private read-only code directory and explicitly provided input bytes are
mounted. Outputs live on a size-limited tmpfs and cross back as validated bytes.
No model-controlled Docker flags, image names, paths, environment or installs.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import selectors
import shutil
import subprocess
import tempfile
import time
import uuid

IMAGE = 'task-manager-skills:py312-v1'
RUNTIME = Path(__file__).parent / 'assets' / 'skill_runtime'
MAX_TOTAL = 24 * 1024 * 1024
MAX_FILES = 16
MAX_TRANSPORT = 34 * 1024 * 1024


def runtime_hash():
    h = hashlib.sha256()
    for name in ('Dockerfile', 'requirements.txt'):
        h.update(name.encode()); h.update(b'\0'); h.update((RUNTIME / name).read_bytes())
    return h.hexdigest()


def _docker():
    for path in (shutil.which('docker'), '/usr/local/bin/docker',
                 '/Applications/Docker.app/Contents/Resources/bin/docker'):
        if path and Path(path).is_file() and os.access(path, os.X_OK):
            return path
    return None


def _control(args, timeout=8):
    # The CLI needs its existing Docker context, but no environment is forwarded
    # into the container unless explicitly named in the fixed run command.
    return subprocess.run(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                          text=True, timeout=timeout, check=False, env=_cli_env())


def _cli_env():
    # Keep the user's on-disk local context, but do not inherit provider secrets
    # or environment overrides that could silently select a remote daemon.
    return {key: value for key, value in os.environ.items()
            if key in ('PATH', 'HOME', 'USER', 'TMPDIR', 'DOCKER_CONFIG')}


def get_status():
    command = _docker()
    result = dict(backend='docker', available=False, image=IMAGE, network=False,
                  arbitraryHostExecution=False, runtimeHash=runtime_hash())
    if not command:
        return dict(result, reason='未找到 Docker。请安装并启动 Docker Desktop，再准备技能运行时。')
    try:
        local = _control([command, 'context', 'inspect', '--format', '{{.Endpoints.docker.Host}}'])
        endpoint = local.stdout.strip()
        if local.returncode or not endpoint.startswith('unix:///') or '\n' in endpoint:
            return dict(result, reason='技能执行仅允许本机 Unix socket Docker；当前上下文不是本机环境。')
        prefix = [command, '--host', endpoint]
        inspected = _control(prefix + ['image', 'inspect', IMAGE, '--format',
                              '{{.Id}}|{{ index .Config.Labels "org.taskmanager.skill-runtime" }}'])
        if inspected.returncode:
            daemon = _control(prefix + ['version', '--format', '{{.Server.Version}}'])
            reason = ('Docker 尚未启动或不可连接。请启动 Docker Desktop。' if daemon.returncode
                      else '技能运行镜像未准备。请运行 scripts/prepare-skill-runtime.py。')
            return dict(result, reason=reason)
        ident, _, label = inspected.stdout.strip().partition('|')
        if not ident.startswith('sha256:') or label != result['runtimeHash']:
            return dict(result, reason='技能运行镜像版本与本应用不一致，请重新准备运行时。')
        return dict(result, available=True, imageId=ident, localDockerHost=endpoint, reason='离线 Docker 脚本环境可用')
    except (OSError, subprocess.TimeoutExpired):
        return dict(result, reason='Docker 响应超时或不可用；没有改为在宿主机执行。')


def _name(name):
    if not isinstance(name, str) or not name or len(name) > 200:
        raise ValueError('文件名缺失或过长')
    p = PurePosixPath(name)
    if p.is_absolute() or '\\' in name or ':' in name or any(ord(c) < 32 for c in name):
        raise ValueError('只接受输入/输出目录中的相对文件名')
    if any(part in ('', '.', '..') for part in name.split('/')) or len(p.parts) > 4:
        raise ValueError('文件路径无效或层级过深')
    return name


def _check_inputs(source, inputs, timeout_seconds):
    if not isinstance(source, str) or not source.strip() or len(source.encode('utf-8')) > 128000 or '\0' in source:
        raise ValueError('Python 源码缺失、过长或无效')
    if not isinstance(inputs, dict) or len(inputs) > MAX_FILES:
        raise ValueError('输入最多 16 个文件')
    total = 0
    names = []
    for name, data in inputs.items():
        _name(name)
        folded = name.casefold()
        if any(folded == prior or folded.startswith(prior + '/') or prior.startswith(folded + '/') for prior in names):
            raise ValueError('输入文件名有大小写冲突或文件/目录冲突')
        names.append(folded)
        if not isinstance(data, bytes):
            raise ValueError('输入须为已管理文件的字节内容')
        total += len(data)
    if total > MAX_TOTAL:
        raise ValueError('输入文件总大小不能超过 24 MiB')
    if type(timeout_seconds) not in (int, float) or not 1 <= timeout_seconds <= 60:
        raise ValueError('执行时限须为 1 至 60 秒')


def _command(docker, image_id, name, work, inputs, endpoint):
    return [docker, '--host', endpoint, 'run', '--rm', '--name', name, '--pull', 'never', '--network', 'none',
            '--read-only', '--cap-drop', 'ALL', '--security-opt', 'no-new-privileges',
            '--user', '65534:65534', '--pids-limit', '64', '--cpus', '1',
            '--memory', '512m', '--memory-swap', '512m', '--log-driver', 'none',
            '--ulimit', 'nofile=64:64', '--ulimit', 'fsize=25165824:25165824',
            '--tmpfs', '/tmp:rw,nosuid,nodev,noexec,size=96m,mode=1777',
            '--tmpfs', '/output:rw,nosuid,nodev,noexec,size=32m,mode=1777',
            '--mount', f'type=bind,src={work},dst=/work,readonly',
            '--mount', f'type=bind,src={inputs},dst=/input,readonly',
            '--env', 'TM_SKILL_INPUT_DIR=/input', '--env', 'TM_SKILL_OUTPUT_DIR=/output',
            image_id]


def _capture(command, timeout_seconds):
    started = time.monotonic()
    process = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                               stderr=subprocess.PIPE, start_new_session=True, env=_cli_env())
    selector = selectors.DefaultSelector()
    for pipe, key in ((process.stdout, 'stdout'), (process.stderr, 'stderr')):
        selector.register(pipe, selectors.EVENT_READ, key)
    parts = {'stdout': bytearray(), 'stderr': bytearray()}
    error = None
    try:
        while selector.get_map():
            if time.monotonic() - started > timeout_seconds:
                error = '脚本超过执行时间上限，隔离任务已停止'
                break
            for event, _ in selector.select(.1):
                chunk = os.read(event.fileobj.fileno(), 65536)
                if not chunk:
                    selector.unregister(event.fileobj); event.fileobj.close(); continue
                cap = MAX_TRANSPORT if event.data == 'stdout' else 65536
                if len(parts[event.data]) + len(chunk) > cap:
                    error = '脚本输出超过传输上限，隔离任务已停止'
                    break
                parts[event.data].extend(chunk)
            if error:
                break
        if error:
            process.kill()
        code = process.wait(timeout=2)
        return code, bytes(parts['stdout']), bytes(parts['stderr']), error
    finally:
        selector.close()
        for pipe in (process.stdout, process.stderr):
            if pipe and not pipe.closed: pipe.close()
        if process.poll() is None:
            process.kill(); process.wait(timeout=2)


def _decode(code, stdout, stderr):
    if code:
        return dict(ok=False, exitCode=code, outputs={}, stdout='', stderr=stderr.decode('utf-8', 'replace')[:32768],
                    error='Docker 隔离任务未正常完成（可能资源超限）；没有导入部分产物')
    try:
        value = json.loads(stdout)
        if not isinstance(value, dict) or value.get('protocol') != 1 or type(value.get('exitCode')) is not int:
            raise ValueError()
        if any(not isinstance(value.get(k), str) or len(value[k].encode()) > 131072 for k in ('stdout', 'stderr')):
            raise ValueError()
        encoded = value['outputs']
        if not isinstance(encoded, dict) or len(encoded) > MAX_FILES: raise ValueError()
        outputs = {}; total = 0
        for name, data in encoded.items():
            _name(name)
            if any(name.casefold() == previous.casefold() for previous in outputs): raise ValueError()
            if not isinstance(data, str): raise ValueError()
            content = base64.b64decode(data, validate=True)
            total += len(content)
            if total > MAX_TOTAL: raise ValueError()
            outputs[name] = content
        if 'error' in value and not isinstance(value['error'], str): raise ValueError()
        background_reaped = value.get('backgroundProcessesReaped', 0)
        if type(background_reaped) is not int or not 0 <= background_reaped <= 64: raise ValueError()
        ok = value['exitCode'] == 0 and not value.get('error')
        result = dict(ok=bool(ok), exitCode=value['exitCode'], outputs=outputs if ok else {},
                      stdout=value['stdout'], stderr=value['stderr'],
                      backgroundProcessesReaped=background_reaped)
        if value.get('error'): result['error'] = value['error'][:1000]
        elif value['exitCode'] != 0 and outputs:
            result['error'] = '脚本失败退出前已产生部分输出；不能作为无产物的正确拒绝通过'
        return result
    except (ValueError, TypeError, KeyError, UnicodeError):
        return dict(ok=False, exitCode=code, outputs={}, stdout='', stderr='',
                    error='隔离任务输出协议无效；没有导入任何文件')


def run_python(source: str, inputs: dict[str, bytes], *, timeout_seconds=30, package_files=None):
    """Run a fixed Python entry point; no custom requirements or network access.

    Code reads os.environ['TM_SKILL_INPUT_DIR'] and writes under
    os.environ['TM_SKILL_OUTPUT_DIR']. A business rejection may exit 2 and print
    an explanation to stderr; it returns ok=False with no infrastructure error.
    Input/contract errors raise ValueError; unavailable runtime returns error.
    """
    _check_inputs(source, inputs, timeout_seconds)
    package_files={} if package_files is None else package_files
    if not isinstance(package_files,dict) or len(package_files)>128:raise ValueError('技能运行资源最多 128 个文件')
    resource_names=[];total=sum(len(v) for v in inputs.values())
    for filename,data in package_files.items():
        _name(filename);folded=filename.casefold()
        if any(folded==n or folded.startswith(n+'/') or n.startswith(folded+'/') for n in resource_names):raise ValueError('技能运行资源路径冲突')
        if not isinstance(data,bytes):raise ValueError('技能运行资源须为原始字节')
        resource_names.append(folded);total+=len(data)
    if total>MAX_TOTAL:raise ValueError('技能运行资源和输入总大小不能超过 24 MiB')
    status = get_status()
    common = dict(backend='docker', network=False, imageId=status.get('imageId'),
                  runtimeHash=status['runtimeHash'])
    if not status['available']:
        return dict(common, ok=False, exitCode=None, outputs={}, stdout='', stderr='', error=status['reason'])
    docker = _docker(); name = 'tm-skill-' + uuid.uuid4().hex
    start = time.monotonic()
    cleanup_confirmed = False
    try:
        with tempfile.TemporaryDirectory(prefix='tm-skill-') as temp:
            root = Path(temp).resolve(); work = root / 'work'; input_dir = root / 'input'
            work.mkdir(mode=0o755); input_dir.mkdir(mode=0o755)
            work.chmod(0o755); input_dir.chmod(0o755)
            if ',' in str(root): raise ValueError('临时目录含 Docker 挂载不支持的字符')
            (work / 'main.py').write_text(source, encoding='utf-8')
            (work / 'runner.py').write_bytes((RUNTIME / 'runner.py').read_bytes())
            for path in work.iterdir(): path.chmod(0o444)
            if package_files:
                package=work/'package';package.mkdir(mode=0o755)
                for filename,data in package_files.items():
                    path=package/filename;path.parent.mkdir(parents=True,exist_ok=True)
                    for directory in path.relative_to(package).parents:(package/directory).chmod(0o755)
                    path.write_bytes(data);path.chmod(0o444)
            for filename, data in inputs.items():
                path = input_dir / filename; path.parent.mkdir(parents=True, exist_ok=True)
                for directory in path.relative_to(input_dir).parents:
                    (input_dir / directory).chmod(0o755)
                path.write_bytes(data); path.chmod(0o444)
            try:
                code, stdout, stderr, error = _capture(_command(docker, status['imageId'], name, work, input_dir, status['localDockerHost']), timeout_seconds)
                result = (dict(ok=False, exitCode=None, outputs={}, stdout='', stderr='', error=error)
                          if error else _decode(code, stdout, stderr))
            finally:
                # Only this invocation's unguessable container is touched. The
                # Docker CLI can die before its container does, so always clean.
                try:
                    cleaned = _control([docker, '--host', status['localDockerHost'], 'rm', '-f', name], timeout=8)
                    cleanup_confirmed = (cleaned.returncode == 0 or
                                         ('No such container' in cleaned.stderr and name in cleaned.stderr))
                except (OSError, subprocess.TimeoutExpired): pass
    except (OSError, subprocess.SubprocessError) as exc:
        result = dict(ok=False, exitCode=None, outputs={}, stdout='', stderr='',
                      error='无法运行 Docker 隔离任务：' + type(exc).__name__)
    if not cleanup_confirmed:
        result.update(ok=False, outputs={}, error='无法确认本次隔离容器已清理，请检查 Docker 状态；未导入产物')
    return dict(result, **common, cleanupConfirmed=cleanup_confirmed, elapsedSeconds=round(time.monotonic() - start, 3))
