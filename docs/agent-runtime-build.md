# Agent 运行时与打包验收

正式 Agent 使用 Python 3.12 的 Mac 独立运行时。开发环境 `.venv`、本次候选验证环境 `.cache/agent-production-venv` 和分发用 `.python-runtime-mac` 各自独立；不要把 Homebrew 虚拟环境复制进应用。

## 依赖锁

`backend/requirements.txt` 保留原业务依赖，并包含 `requirements-agent.txt`；Mac 再由 `requirements-mac.txt` 添加凭据加密依赖。Agent 锁来自独立验证工程，移除了安装工具 pip。全部业务及传递依赖固定精确版本。

`scripts/requirements_lock.py` 递归读取 `-r`，拒绝循环引用和冲突版本。安装与 Windows 复制都使用完整闭包，避免仅安装顶层框架后漏掉传递依赖。安装目标不自动追逐新版本；升级应重新执行接口回归及打包验收。

Mac 运行时采用 `uv pip --no-deps --only-binary :all:` 安装锁定列表，之后校验每个已安装版本及其依赖元数据。`runtime-manifest.json` 记录全部三份 requirements 文件的 SHA256。

## 候选环境验证

在项目根目录运行：

```sh
python3.12 -m venv .cache/agent-production-venv
.cache/agent-production-venv/bin/python -m pip install --only-binary=:all: --no-deps -r backend/requirements-mac.txt
.cache/agent-production-venv/bin/python -m pip check
.cache/agent-production-venv/bin/python -B -I scripts/verify-runtime-deps.py --requirements backend/requirements-mac.txt --smoke
```

最后一个命令验证固定依赖闭包，并用合成模型执行官方 Deep Agents 工具循环、StateBackend 文件读写、技能渐进加载、摘要与可恢复原文、LangGraph SQLite 重开、OpenRouter 工具绑定。当前固定版本为 `deepagents==0.7.41`。它禁止 socket 连接、关闭测试 tracing、不读取真实邮箱或配置、不需要 API Key。它不代表真实模型回答质量验收。

候选环境结果见 `handover/agent-runtime/candidate-verification.json`。这是开发环境验证；打包应用是否通过，以本次正式发布的包验收记录为准。

## Mac 发布验证

等待业务与界面集成完成、退出正在从 release-mac 运行的旧应用后：

```sh
npm run pack:mac
```

若旧应用仍从 `release-mac` 运行，可把新构建放到独立输出目录。打包和验收必须使用同一个变量：

```sh
TM_MAC_OUTPUT=release-agent npm run pack:mac
TM_MAC_OUTPUT=release-agent npm run test:package:mac
```

默认输出仍为 `release-mac`。构建会检查所选输出目录是否有正在运行的应用，禁止覆盖该目标；也禁止把项目目录或其祖先设置为输出。新旧包可并存，生产资料目录与定时整理设置不会因为改变输出路径而迁移。

构建运行时会执行依赖和 Agent smoke。`scripts/verify-python-package-mac.cjs` 随后把已打包的 Python 与后端复制到临时其他目录，在隔离 PATH 下再执行依赖/Agent 检查、Electron Python bridge、SQLite、后台任务失败报告和签名检查。`verify-agent-backend-smoke.py` 仅在独立验收脚本内注入合成模型，让打包的正式 Agent 调用真实任务查询工具、保存流式答复、重新打开服务读取结果；应用不提供环境变量式的模型注入入口。验收还检查 Agent 空列表和未知接口安全报错。只有这些检查通过才能声称分发包包含新框架且能独立运行。

Windows 构建要求 Windows Python 3.14 的项目环境与官方嵌入式运行时匹配。脚本已经改为复制递归锁的全部分发文件并验证闭包；本轮没有在 Windows 执行，不将 Mac 结果视为 Windows 验收。
