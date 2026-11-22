# Windows 使用与交接

Windows 版本沿用同一套秘书对话、邮件、文档和待办功能。面向 **Windows 10/11 x64**；不需要先安装 Python、Node.js、Microsoft Word 或 LibreOffice。Python 和 Word/PDF 渲染器随成品提供。Windows ARM 设备尚未单独验收。

## 下载安装

1. 从本仓库的 [Releases](https://github.com/whatislove-baby/task-manager-secretary/releases) 下载 Windows x64 ZIP。若尚未出现 Windows 下载项，表示该成品仍在构建或验收。
2. 在文件资源管理器中选择“全部解压”。放到自己可写的目录，例如 `C:\Users\你的用户名\Apps\Task Manager`。
3. 在解压后的目录双击 **`Task Manager.exe`**。保留它旁边的 `resources`、DLL 等文件，不要只复制 EXE，也不要直接从压缩包内启动。

这是尚未购买代码签名证书的预览构建，Windows 可能显示未知发布者。请使用本仓库 Releases 提供的文件，下载校验值见 `SHA256SUMS.txt`。

首次进入固定“秘书”主对话。展开左侧栏可进入收件箱、待确认、日历与待办、资料与文件、设置。完整功能介绍见[图文说明书](https://github.com/whatislove-baby/task-manager-secretary/blob/main/docs/product-kit/user-manual.pdf)，解压目录中也提供 `User-Manual.pdf`；其中部分截图来自 Mac，Windows 的窗口边框和定时设置有所不同。

## 第一次配置

在“设置 → 模型与 API”填入自己的服务地址、模型名和 API Key。使用 OpenRouter 时填写 `https://openrouter.ai/api/v1`；模型名以账号实际可用的完整 ID 为准，并选择支持工具调用的模型。保存后先发送一条简单请求，再尝试带文件的任务。

在邮箱设置中添加自己的账户。QQ 邮箱使用已开启 IMAP/SMTP 后取得的**授权码**；其他 IMAP 邮箱填写服务商提供的服务器和端口。Microsoft 365 是否可连接取决于组织授权。仓库和安装包不含可共用的 API Key 或邮箱授权码。

连接邮箱后，在秘书对话说“刷新一下”或“有什么要处理的”。它会收取并整理邮件，之后可接着说“把这次小组汇报记成待办”或“根据这封邮件和模板写一份回复”。待办和修改要经过确认；发送邮件还需要单独确认收件人、正文和附件。

## 建议先完成的一次练习

1. 用自己的邮箱给自己发一封课程通知，写清汇报时间，并附上一个简短的 Word 模板。
2. 回到秘书主页说“刷新一下，看看这封课程通知需要我做什么”。核对来源邮件和日期。
3. 让它提出待办，在“待确认”中核对并确认；到“日历与待办”检查时间。
4. 让它根据模板修改内容、另存 Word 并转成 PDF。打开输出文件，检查正文、表格、图片和分页。
5. 让它把 Word 和 PDF 都加入回复草稿。核对两个附件后再决定是否发送。
6. 退出并重新打开应用，确认秘书对话、待办和草稿仍在。把测试时间、结果及发现的问题记入团队交接记录。

如果只是试用文件能力，也可以直接通过聊天输入框旁的加号上传本地资料；不必先连邮箱。

## 定时整理、Skill 与数据

- 定时整理在设置里配置时间和时区，并按页面提示启用 Windows 计划任务。电脑需开机并能联网；睡眠、关机或未登录时的行为以设置页说明为准。首次建议用“立即整理一轮”核对范围。
- 内置 PDF、Word、CSV 等文件工具不需要 Docker。**执行导入的 Python Skill** 需要额外安装并启动 Docker Desktop，使用 Linux containers 和本机 Docker context。完成下方源码开发环境配置后，在源码根目录执行 `.\.venv\Scripts\python.exe scripts/prepare-skill-runtime.py`，准备 Skill 运行镜像。准备过程与应用固定使用同一本机 Linux 引擎；Windows containers 会提示切换。安装 Docker 后重新打开应用，普通文字经验不需要 Docker。
- Windows 容器验收采用 Windows Server 2025、Rancher Desktop / Moby 和原生 Windows Docker 命名管道，完整流程与实际结果见 [Skill 验收记录](https://github.com/whatislove-baby/task-manager-secretary/blob/main/docs/WINDOWS-SKILL-TEST-REPORT.md)。这与组员的 Windows 10/11 + Docker Desktop 是不同的具体环境，仍需按下方练习完成本人交接；不使用远程 Docker。
- 数据默认保存在 `Task Manager.exe` 同目录的 `data` 文件夹；设置页可查看实际路径。退出应用后备份整个 `data`。升级时先从托盘退出应用，将旧目录的 `data` 备份并复制到新版本目录，再启动新版本；确认资料正常后再清理旧程序。应用目录移动后，需要重新保存启用定时整理，使计划任务使用新路径。
- API Key 和邮箱凭据使用 Windows 当前用户的 DPAPI 保护。复制数据到另一台电脑或另一位 Windows 用户后，需要重新填写凭据；不应把自己的 `data` 发给组员。
- 出错时保留具体操作和时间，查看数据目录的 `logs`。文件无法导出时，先确认解压完整、目录可写，再把日志和错误截图交给开发同学。

## 开发者验收与证据

本次移植的实际结果与 Windows 界面见 [Windows 版本验收记录](https://github.com/whatislove-baby/task-manager-secretary/blob/main/docs/WINDOWS-TEST-REPORT.md)。

下面的验收在独立合成资料目录运行，不使用真实 API Key、收件箱或 SMTP 发送。只有在 **Windows 上使用成品内的 Python 和真实 EXE** 运行成功，才能标记 Windows 成品验收通过。Mac 开发检查不能代替它。

```powershell
& '.\release-windows\win-unpacked\resources\python\python.exe' `
  scripts/verify-windows-workflow.py `
  --backend-dir release-windows/win-unpacked/resources/backend `
  --output handover/windows-release/workflow

node scripts/verify-windows-app.cjs `
  --app-dir release-windows/win-unpacked `
  --output handover/windows-release/app
```

第二条验收命令需要开发环境的 Node.js 24；成品用户不需要安装它。

| 检查 | 证据 |
| --- | --- |
| 固定秘书、官方 Agent 调工具、待办审批和改期、保留任务 ID | `workflow/workflow.json`、`workflow/agent-run.json` |
| 修改现有 Word 模板、真实转换 PDF、打开检查页面、双附件草稿、损坏文件处理 | `workflow/course-progress-reviewed.docx`、`.pdf`、页面 PNG、JSON 中的结构与附件检查 |
| 重启后的秘书、任务、邮件、草稿持久化；Windows DPAPI 跨进程读写 | `workflow/workflow.json` |
| 实际 EXE 主页、侧栏日历、前后端 IPC、关闭重开 | `app/app.json`、主页和日历 PNG、启动日志 |

所有路径均相对 `handover/windows-release`。结果 JSON 会保存实际平台、解释器或 EXE 路径及逐项结果；CI 构建附件提供完整证据。此脚本使用受控模型回答来检查代码执行链路，**不代表真实模型理解质量、真实邮箱连接或真人排版审阅已通过**。发布前还需查看截图和生成文件。

当前状态请以对应 Windows 发布构建的结果为准；没有 Windows 结果的版本应标注“待验证”。

Docker 平台回归位于 `backend/tests/test_skill_execution_platform.py`；实际容器用例使用 `TM_RUN_SKILL_DOCKER_TESTS=1` 启用。完整 Skill 验收入口为 `scripts/verify-windows-skill-workflow.py`，缺少 Docker 或成品 Python 时直接失败，不以跳过代替通过。实现依据 [Docker 本机命名管道说明](https://docs.docker.com/reference/cli/docker/) 与 [Python 跨平台 pipe 限制](https://docs.python.org/3/library/selectors.html)。

## Windows 从源码运行与打包

开发环境需要 Node.js 24、Python 3.12 x64；打包另外需要 uv 0.11.7 或更新版本，以及 Visual Studio 2022 或更新版的 C++ 桌面生成工具。Build Tools 安装器中需包含 `Microsoft.VisualStudio.Component.VC.Tools.x86.x64`，构建脚本从其 `VC/Redist` 目录补齐文档渲染依赖；仅安装 `vc_redist.x64.exe` 不够。GitHub Windows 构建机已提供这些组件。普通 ZIP 用户不需要这些开发环境。

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r backend/requirements.txt
npm.cmd ci
npm.cmd run runtime:install
npm.cmd run dev
```

```powershell
npm.cmd run runtime:documents:win
npm.cmd run check
npm.cmd run dist:win
```

成品输出 `release-windows/Task-Manager-Windows-x64.zip`。构建脚本会准备官方独立 Python 与校验过的 LibreOffice，不使用开发虚拟环境作为成品运行时。GitHub Actions 还会启动成品、运行计划任务、检查文档和持久化，全部通过后才创建预览发布。运行证据同时放在发布页的 `Windows-Verification.zip`。
