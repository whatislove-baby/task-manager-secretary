"""Inside-container transport only. Docker, not this Python wrapper, isolates code."""
import base64
import json
import os
from pathlib import Path
import selectors
import signal
import stat
import subprocess
import sys
import time

LIMIT = 32768
MAX_TOTAL = 24 * 1024 * 1024
MAX_FILES = 16


def stop_background_processes():
    # This wrapper is PID 1 in a private Docker PID namespace. Kill/reap only
    # that namespace's remaining same-UID processes, including detached orphans.
    # Never allow this operation if someone accidentally runs the file on host.
    if os.getpid() != 1:
        raise RuntimeError('技能包装器只能作为隔离容器的 PID 1 运行')
    try:
        os.kill(-1, signal.SIGKILL)
    except ProcessLookupError:
        pass
    reaped = 0
    while True:
        try:
            os.waitpid(-1, 0)
        except ChildProcessError:
            return reaped
        reaped += 1


def main():
    if os.getpid() != 1:
        raise RuntimeError('技能包装器只能作为隔离容器的 PID 1 运行')
    # Do not propagate Docker image/environment additions to generated code.
    env = {'PATH': '/usr/local/bin:/usr/bin:/bin', 'HOME': '/tmp',
           'LANG': 'C.UTF-8', 'TMPDIR': '/tmp', 'PYTHONDONTWRITEBYTECODE': '1',
           'TM_SKILL_INPUT_DIR': '/input', 'TM_SKILL_OUTPUT_DIR': '/output',
           'OPENBLAS_NUM_THREADS': '1', 'OMP_NUM_THREADS': '1'}
    process = subprocess.Popen([sys.executable, '-I', '-B', '/work/main.py'],
                               cwd='/tmp', env=env, stdin=subprocess.DEVNULL,
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    selector = selectors.DefaultSelector()
    for pipe, name in ((process.stdout, 'stdout'), (process.stderr, 'stderr')):
        selector.register(pipe, selectors.EVENT_READ, name)
    logs = {'stdout': bytearray(), 'stderr': bytearray()}
    exceeded = False
    background_reaped = 0
    background_stopped = False
    while selector.get_map():
        if process.poll() is not None and not background_stopped:
            background_reaped = stop_background_processes()
            background_stopped = True
        for event, _ in selector.select(.25):
            data = os.read(event.fileobj.fileno(), 8192)
            if not data:
                selector.unregister(event.fileobj)
                event.fileobj.close()
                continue
            name = event.data
            left = LIMIT - len(logs[name])
            logs[name].extend(data[:max(0, left)])
            if len(data) > left:
                exceeded = True
                process.kill()
    code = process.wait()
    if not background_stopped:
        background_reaped = stop_background_processes()
    result = dict(protocol=1, exitCode=code, stdout=logs['stdout'].decode('utf-8', 'replace'),
                  stderr=logs['stderr'].decode('utf-8', 'replace'), outputs={},
                  backgroundProcessesReaped=background_reaped)
    root = Path('/output')
    if exceeded:
        result['error'] = '脚本日志超过上限，执行已停止'
    elif code != 0 and any(root.iterdir()):
        # Negative test cases must reject input before creating any artifacts.
        # Discarding partial bytes alone would falsely mark such a case passed.
        result['error'] = '脚本失败退出前已产生部分输出；不能作为无产物的正确拒绝通过'
    elif code == 0:
        total = 0
        count = 0
        for directory, folders, files in os.walk(root, followlinks=False):
            for name in folders + files:
                path = Path(directory) / name
                info = path.lstat()
                if stat.S_ISLNK(info.st_mode) or not (stat.S_ISDIR(info.st_mode) or stat.S_ISREG(info.st_mode)):
                    raise ValueError('输出包含链接或特殊文件，未导出')
            for name in files:
                path = Path(directory) / name
                if path.stat().st_nlink != 1:
                    raise ValueError('输出包含硬链接，未导出')
                count += 1
                size = path.stat().st_size
                total += size
                if count > MAX_FILES or total > MAX_TOTAL:
                    raise ValueError('输出文件数量或总大小超过上限')
                result['outputs'][path.relative_to(root).as_posix()] = base64.b64encode(path.read_bytes()).decode('ascii')
    print(json.dumps(result, ensure_ascii=True, separators=(',', ':')), flush=True)


if __name__ == '__main__':
    try:
        main()
    except BaseException as exc:
        print(json.dumps(dict(protocol=1, exitCode=1, outputs={}, stdout='', stderr='',
                              error=str(exc)[:1000]), ensure_ascii=True), flush=True)
