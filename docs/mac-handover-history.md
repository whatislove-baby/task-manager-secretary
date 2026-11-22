# Task Manager Mac / Python

React界面 + Electron桌面适配 + Python业务后端的本地任务管理器。本分支基于 Gaojiashen/task-manager 的 `5e583cd` 增加 Mac 适配和交接回归，原 Windows 入口保留。

## Mac 使用与交接

Apple Silicon 成品在 `release-mac/mac-arm64/Task Manager.app`；双击运行，自带 Python，不要求安装开发工具。数据保存在 `~/Library/Application Support/Task Manager Mac/data`，更新应用不会替换数据。此本地构建采用临时签名，未做 Apple 公证；不代表所有 Mac 已验收。

源码运行需要 Node 24、Python 3.12（打包另需 uv）：

```sh
python3.12 -m venv .venv
.venv/bin/python -m pip install -r backend/requirements-mac.txt
npm ci
npm run dev
```

```sh
npm run check
npm run test:handover
npm run test:runtime
npm run dist:mac
npm run test:package:credentials:mac
npm run test:automation:mac
```

开发数据在项目 `data/`，与成品隔离。Mac 使用钥匙串保护加密主密钥，凭据以 Fernet 密文保存；开发解释器与成品分别使用钥匙串项。跨机器或在开发版和成品间搬移数据后需重新填写凭据。首次系统授权提示可能显示 `python3.12`。

打开 `handover/index.html` 查看问题、修改位置、测试输入和交接记录表。`npm run test:handover` 用合成 Canvas/邮件及受控模型响应测试真实数据库工作流，写入 `handover/results.json`。队友须亲自运行、填写观察与姓名；自动测试不能代替团队交接。至少包含正常导入、改期/追问、失败不误存三类场景。

本分支修复同名邮件任务被合并、追问缺原文、模型截断误接受、Canvas来源混淆和未知时区、Microsoft权限与Mac回调延迟。AI辅助审查、实现和测试；原应用、界面与主要业务来自上游。团队成员、学号和本次具体贡献需在提交前据实补充。

新增邮件写作：AI起草／润色、根据来信写回复、人工编辑与发送预览，再由用户确认通过SMTP发送。使用已配置IMAP邮箱的授权码，支持单个收件人、纯文本正文与文件附件。发送结果不明时不自动重试；服务器接受不等于送达。配置与边界见邮件写作说明（本地历史材料未随仓库发布）。2026-11-20 已实测 QQ 单封收发（本地历史材料未随仓库发布）、OpenRouter 纯文本起草（本地历史材料未随仓库发布）及三封邮件到待办和应用内日历（本地历史材料未随仓库发布）：含初次失败、修复后断点继续、日期精度和重复保护。按自己发给自己测试（本地历史材料未随仓库发布）复测；这些技术记录不代替学生交接。

新增附件与文件处理：选定邮件附件或本地 PDF／DOCX／文本，在对话中生成新文件或修订副本，再加入回复的固定附件预览。原件保留，AI 上传与发信分别由用户触发。见使用与交接报告（本地历史材料未随仓库发布）及合成四场景结果（本地历史材料未随仓库发布）。

Canvas 是日历订阅同步；邮件可手动导入，或在“邮箱 → 自动整理”启用已连接 IMAP 邮箱的定时收取、分类与待办建议。默认香港时间 09:00／18:00、首次回看 7 天、每轮最多分析 20 封；待办草稿仍须人工确认。模型会收到选定范围内需分析邮件的正文，不分析附件。后台运行、补跑、停用与测试边界见自动整理说明（本地历史材料未随仓库发布）。

OpenRouter 在“设置”填写基础地址、模型和 Key；Microsoft 365 需自有公开客户端 ID 与学校许可，QQ 等使用 IMAP 授权码。真实账号结果与离线测试分开记录。

待办树、日期与完成联动、SQLite、草稿确认、备份、Canvas、AI、IMAP/Microsoft邮箱、离线邮件、分析和持久化后台任务均由Python处理。Electron负责窗口、托盘、文件选择、系统通知、开机启动和旧凭据的一次性原生迁移。

## 日常使用

本仓库只保存源码，不上传已构建程序。执行 `npm run dist` 可在本机生成 `release-v2/Task Manager.zip`。解压后运行 `Task Manager.exe`，保留整个文件夹，不能只复制exe。生成的发布包自带Python，使用时不要求电脑另行安装Python或Node。

数据保存在应用旁边的 `data` 文件夹；移动和备份应用时一并保留。AI服务和邮箱需要自行配置真实服务、密钥或授权；本次迁移未复制旧项目的真实数据和凭据。

## 开发环境

开发时需要创建项目专属Python环境 `.venv`。`npm run dev` 和 `npm run test:python` 明确使用它，不依赖PATH中的全局Python。发布包另外携带独立Python3.14.10运行时。

克隆本仓库后，在项目目录中安装独立的Python和Node依赖：

```powershell
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r backend\requirements.txt
npm ci
npm run dev
```

如果本机pip索引/代理配置异常，可执行 `.venv\Scripts\python.exe scripts\install-python-deps.py`：从官方PyPI下载固定版本wheel、验证官方SHA256后装到项目环境。

## 验证与发布

```powershell
npm run check
npm run test:runtime
npm run dist
npm run test:package
```

- `check`：类型、Python后端、React/桌面与兼容测试、生产构建。
- `test:runtime`：隐藏真实Electron双窗口、Python/邮箱/worker调用链及合成旧凭据迁移。
- `test:automation:mac`：构建 Mac 成品后，以临时空数据验证真实 launchd 独立启动包内 Python 并清理；自动整理配置为停用，不访问真实邮箱或模型。
- `dist`：生成自带运行时的Windows ZIP并验证发布内容。首次需要下载官方Python发布包；后续可用校验通过的缓存。
- `test:package`：直接验证app.asar中实际桥接、包内Python、数据库与后台worker。

`npm run dev:legacy`仅供开发时对照原TypeScript后端；正式包不包含旧TypeScript业务模块。两种模式不能同时写同一data。

详细产品规则和邮箱配置指引在docs目录。仓库排除运行数据、密钥、虚拟环境、日志、发布包、截图和本机交接记录。自动化使用合成数据和受控响应；真实QQ／OpenRouter结果仅覆盖记录中的场景。真实学校OAuth、其他邮箱、Canvas尚未验收；外部日历和自动排程未实现，学生交接仍待实际执行。
