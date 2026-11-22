# Windows Docker Skill 完整流程验收

日期：2026-11-22。本文补充 Windows 初版中尚未执行的真实容器验收。

## 验收环境

使用 GitHub Windows Server 2025 x64、原生 Windows Python、Rancher Desktop 1.25.0 的 Moby Linux 引擎。Windows Docker CLI 通过 `npipe:////./pipe/docker_engine` 连接本机 WSL2 容器环境，没有远程 Docker、自制客户端代理或宿主脚本执行回退。

Rancher Desktop 官方支持 Windows Server 2025，并提供 Windows 文件挂载转换；Docker Desktop 官方支持的目标是 Windows 10/11。本轮结果证明 Windows 到本机 Linux Docker 的实际执行链路，不能写成已经在组员的 Windows 11 + Docker Desktop 上完成交接。

CI 的 WSL 内核提示不支持 swap 上限，因此本报告不将 swap 限额列为已验证的资源约束。

- [Rancher Desktop 安装要求](https://docs.rancherdesktop.io/getting-started/installation/)
- [Docker Desktop Windows 要求](https://docs.docker.com/desktop/setup/install/windows-install/)

## 场景与方法

一名实习生把已经写好的库存分配 Skill 交给助手，指定它适用于某位同事的来信。Skill 的代码、两个历史输入及完成稿、一个缺价反例均为固定合成资料。助手导入后实际运行三个样例，再由用户批准。

换到没有教学聊天记录的新对话，导入同一来源的新邮件及两个原始 CSV 附件。助手检索已批准 Skill、读取使用说明、运行现成代码、检查结果并创建带附件的回复草稿。新订单的独立标准答案只供验收比较，不放入 Skill 运行包或模型上下文。

模型决策由受控测试替身提供；Deep Agents 的工具披露、导入器、审批接口、Docker 执行器、原文件库、邮件草稿与数据库全部使用产品代码。每次工具调用还检查该工具已由官方 Skill 机制披露，模型没有批准或发送工具。

新邮件的订单号是 `1004`，SKU `0007` 只有 3 件库存，两个订单行依次需要 2 件和 5 件。实际结果如下，SKU 和行号的前导零保留：

| 行号 | SKU | 需求量 | 分配量 | 缺货量 | 缺货金额 USD |
| --- | --- | --- | --- | --- | --- |
| 01 | 0007 | 2 | 2 | 0 | 0.00 |
| 02 | 0007 | 5 | 1 | 4 | 5.34 |
| 03 | 0999 | 3 | 0 | 3 | 6.30 |
| 04 | 0012 | 1 | 0 | 1 | 0.01 |

第四行的单价为 `0.005`，按该 Skill 已声明的十进制四舍五入规则得到 `0.01`。测试还核对两行 `0007` 共用同一份库存，不能各自重复分配 3 件。这些标准值由验收程序独立比较，未交给模型照抄。

## 结果

| 验收层次 | 实际结果 | 构建记录 |
| --- | --- | --- |
| 原生 Windows 源码环境的真实容器回归 | 11 / 11 通过，0 跳过；39.535 秒 | [源码第 5 轮](https://github.com/whatislove-baby/task-manager-secretary/actions/runs/45011464304) |
| 源码环境的完整 Skill 工作流 | 52 / 52 检查通过，实际运行 8 次容器 | 同上，证据标记 `runtimeKind: source` |
| Windows 成品 Python 与包内 backend | 52 / 52 检查通过，实际运行 8 次容器；0 SMTP 尝试、0 发送 | [成品第 9 轮](https://github.com/whatislove-baby/task-manager-secretary/actions/runs/45014478952)，证据标记 `runtimeKind: packaged` |

成品测试提交为 [`42f0dd56`](https://github.com/whatislove-baby/task-manager-secretary/commit/42f0dd56bcf7bd51206ad22eb9a20db96f21b105)，使用包内 Python 3.12.13。后续文档提交只补充结果。成品的原始结果与文件位于发布页 `Windows-Verification.zip` 的 `skill-workflow/`，引擎版本与安装校验值位于 `docker-environment/environment.json`。

| 流程 | 核对内容 |
| --- | --- |
| 导入固定 Skill | 原代码字节保持一致；两个正确样例与一个错误样例在真实 Docker 中执行 |
| 人工批准 | 未批准不能执行；只有独立审批接口能批准指定版本 |
| 新对话复用 | 按真实完整发件地址检索；不需要旧教学聊天记录；读取当前批准版说明 |
| 处理新附件 | 原始邮件附件直接输入；结果与未参与教学的标准答案逐格一致；保留来源与版本哈希 |
| 回复草稿 | 实际输出文件加入对应邮件的回复草稿；附件 ID、哈希、收件人和来源邮件一致 |
| 错误输入 | 缺价格的真实容器任务明确拒绝，没有生成文件或新回复草稿 |
| 无产物坏包 | 即使程序返回退出码 0，缺少约定输出也不能通过测试或批准 |
| Windows 文件路径 | 实际 Docker 暂存目录包含中文和空格，两个只读挂载均经过真实 Docker CLI |
| 重启恢复 | 已批准 Skill、发件人检索、结果文件、回复附件和聊天记录仍在；重启不重新执行 |
| 隔离与清理 | 每次运行断网、只读根文件系统；所有本次容器均删除；没有 SMTP 尝试或发送回执 |

## 修复内容

- 准备镜像与执行 Skill 固定使用同一本机 Linux Docker；不再因 shell 的 Docker 覆盖变量将镜像建到别处。
- Windows containers 模式在下载、构建前明确提示切换到 Linux containers。
- 增加 Docker Desktop 当前用户安装目录的发现，应用尚未继承新 PATH 时也能找到客户端。
- 修正真实容器导入测试的 SQLite 关闭顺序，避免 Windows 清理临时目录时文件仍被占用。

第 8 轮成品 Skill 流程通过后，全量回归发现新增测试把 Windows 环境变量键 `SYSTEMROOT` 与 `SystemRoot` 按大小写严格比较。已修正测试，使其继续核对完整的允许键集合和值，并把平台检查提前到打包前。[该轮失败记录](https://github.com/whatislove-baby/task-manager-secretary/actions/runs/45011953056)保留，未把断言问题计为业务功能失败。

## 复测与证据

固定合成资料在 `scripts/fixtures/windows-skill-workflow` 和 `examples/script-skills`。先准备本机 Docker 与 Skill 镜像，再在源码根目录使用成品 Python 执行：

```powershell
$TaskManagerApp = 'C:\Users\你的用户名\Apps\Task Manager'
& "$TaskManagerApp\resources\python\python.exe" scripts/verify-windows-skill-workflow.py `
  --backend-dir "$TaskManagerApp\resources\backend" `
  --output handover/windows-skill-check
```

脚本默认要求真实 Windows 和成品 Python，缺少环境直接失败，不以跳过作为通过。`--allow-source-runtime` 只供源码 CI 使用，结果会明确标记为源码验证。

证据包含 `skill-workflow.json`、逐轮工具结果、导入审核记录、合成 `.eml`、Skill ZIP、实际输出和独立标准答案。此测试不验证在线模型理解质量、真实邮箱授权或真实 SMTP 投递，也不代替组员本人完成交接。
