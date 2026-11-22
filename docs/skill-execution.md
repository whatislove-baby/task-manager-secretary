# 脚本技能的本地隔离执行

本模块复用本机 Docker Engine / Docker Desktop 的 Linux 容器隔离。它不是把普通目录、Python 虚拟环境或 AST 检查称为沙箱；没有 Docker 时返回不可用，绝不悄悄在宿主机运行生成代码。仅接受本机 Unix socket 的 Docker 上下文，不向远程 Docker 主机上传业务文件。

准备一次运行时：

```sh
open -a Docker
.venv/bin/python scripts/prepare-skill-runtime.py
```

准备脚本使用源码中固定 digest 的官方 Python 3.12 slim-bookworm 镜像，安装 `assets/skill_runtime/requirements.txt` 的固定表格/文档依赖并记录运行时标签。包含 pandas、openpyxl、python-docx、pypdf、Pillow、reportlab 等；模型不能指定镜像、Docker 参数或通过 pip 临时安装依赖。准备阶段需要网络，实际技能执行使用已有镜像 ID、`--pull never`、`--network none`。

## 调用约定

```python
from task_backend.skill_execution import get_status, run_python

result = run_python(source, {"orders.csv": original_bytes}, timeout_seconds=30)
# result: ok, exitCode, outputs:{relative_name:bytes}, stdout, stderr,
#         backend, imageId, runtimeHash, elapsedSeconds, backgroundProcessesReaped,
#         optional error
```

脚本以 Python 3.12 启动，读取环境变量 `TM_SKILL_INPUT_DIR`，写入 `TM_SKILL_OUTPUT_DIR`。输入仅是主程序明确传入的字节；不把实际客户目录、主机 HOME、凭据文件或 Docker socket 挂进容器。现有 SQLite、邮件与发送服务仍在主应用，生成脚本无法直接访问。

业务检查不通过时可向 stderr 输出原因并 `sys.exit(2)`，且必须保持输出目录为空。此时返回 `ok=false`、`exitCode=2`、空 outputs，不添加基础设施 error。失败退出前留下文件、目录或链接时会额外返回 error，即使这些部分产物不会被导入，也不能算“正确拒绝”的测试通过。超时、镜像缺失、资源限制和无效输出协议同样使用 error；不能把这些错误当成“正确拒绝缺价”的业务测试通过。

默认时限 30 秒，调用方上限 60 秒；1 CPU、512 MiB 内存（无额外 swap）、64 进程、只读根文件系统、非 root、删除全部 capabilities、禁止提权。输入只读；`/tmp` 为 96 MiB tmpfs，`/output` 为 32 MiB tmpfs，两者均 noexec/nosuid/nodev。单次最多 16 个输入/输出文件，各侧总量最多 24 MiB；源码最多 128000 字节，stdout/stderr 各最多 32 KiB。

成功退出后只导出普通文件，拒绝软链接、硬链接、特殊文件、路径穿越和大小写冲突。输出先通过内存中的有界字节协议返回，随后仍须由技能库核对声明格式、结构、用户提供的正确样例。非零退出、异常或超限时不导入部分产物。每次调用拥有独立临时目录和随机容器名；结束或超时后只清理本次容器。如果 Docker 在清理期间失联，返回 `cleanupConfirmed=false` 并拒绝导入产物，不声称任务已经可靠结束。

主脚本退出后，容器 PID 1 包装器先终止并回收剩余子进程（包括脱离会话、关闭标准输入输出的子孙进程），再收集产物；返回 `backgroundProcessesReaped` 记录回收数量。这样避免普通生成脚本遗留的后台工作在文件采集期间继续修改结果。包装器不在 PID 1 时拒绝执行，不能直接在宿主机运行。

容器是执行隔离边界，包装脚本负责结果传输；它与生成代码使用同一容器用户，不是对抗恶意代码的独立安全判分边界。不能把包装脚本的“成功”或回收计数当作业务正确；生成代码可以产生错误内容或主动干扰传输，必须由容器外的样例/规则验收。没有主机网络或凭据的执行器也不等于对未知内核漏洞的绝对保证。

## 验证

```sh
TM_RUN_SKILL_DOCKER_TESTS=1 PYTHONPATH=backend \
  .venv/bin/python -m unittest discover -s backend/tests -p test_skill_execution.py -v
```

10 项快速契约检查和 8 项真实 Docker 检查分别覆盖：不可用时不降级、路径/版本/远程上下文/协议检查、正确业务拒绝、清理时 Docker 失联、包装器在非 PID 1 时拒绝启动和发信号；实际 CSV→XLSX、主机/网络/凭据隔离、只读输入与实际 cgroup 限制、失败退出后残留文件或链接不能通过负例验证、输出链接拒绝、日志上限、后台子孙进程回收与超时容器清理。普通全量测试会跳过显式 opt-in 的真实 Docker 部分，应另外保留上述实际运行结果。

设计依据：[Docker 容器运行参数](https://docs.docker.com/engine/containers/run/)与[资源限制](https://docs.docker.com/engine/containers/resource_constraints/)。Docker 是新增的外部运行依赖，不打包进 Electron 应用，也不要求云账号。
