# Task Manager · 对话式学习与邮件助手

| 成员 | 学号 | 具体贡献 |
| --- | --- | --- |
| 提交前由团队据实填写 | 待填写 | 原项目、Mac 适配、Agent、界面、测试与实际交接分别注明 |

面向需要处理课程、工作和邮件的用户。直接描述任务，AI 检索已导入邮件、文件与待办，必要时澄清，再给出带来源的分析、文档副本或待确认草稿。不要求先选课程空间。

**Windows 用户：** 从 [Releases](https://github.com/whatislove-baby/task-manager-secretary/releases) 下载通过 Windows CI 验收的 x64 便携预览 ZIP，全部解压后运行 `Task Manager.exe`；详见 [Windows 使用与交接](docs/WINDOWS.md)、[验收记录](docs/WINDOWS-TEST-REPORT.md)和 [Docker Skill 完整流程](docs/WINDOWS-SKILL-TEST-REPORT.md)。成品包含 Python 和 Word/PDF 渲染器。

Apple Silicon 构建输出（成品未放入源码仓库）：`release-secretary-home/mac-arm64/Task Manager.app`。构建后退出旧版并双击运行，成品自带 Python 和文档渲染器；数据保存在 `~/Library/Application Support/Task Manager Mac/data`。这是本机临时签名构建，尚未公证。

从[图文手册与演示入口](handover/final-guide/index.html)开始；[完整可缩放流程图](handover/final-guide/workflow.html)和[源码说明](docs/workflow-complete.md)展示当前实现。

**产品说明书与流程图：** [完整图文说明书 PDF](docs/product-kit/user-manual.pdf) · [SVG 流程图](docs/product-kit/workflow-complete.svg) · [整套资料 ZIP](docs/task-manager-product-kit.zip?raw=true)；[资料目录](docs/product-kit/README.md)另有离线 HTML 与两页概览。

**2026-11-23 主分支更新：** [更新日志 PDF](output/pdf/TaskManager-更新日志-2026-11-23.pdf) · [增量功能使用说明 PDF](output/pdf/TaskManager-使用说明-2026-11-23.pdf)。涵盖待办重复提醒、Word/PDF 配套检查、待确认筛选、失败恢复和完整对话导出；拉取最新 `main` 后从源码启动，旧安装包不会自动包含这些更新。已有源码的[更新步骤与 Word/PDF 运行要求](docs/TEAM-START.md#已有源码如何更新)已补充。

## 配置与运行

**组员第一次运行：先看[组员上手与交接指南](docs/TEAM-START.md)**，包含配置、样例练习、协作方式和交接记录模板。

在“设置 → 模型与 API”配置 OpenRouter 地址、支持工具调用的模型和 Key。密钥由 Mac 钥匙串／Windows DPAPI 及本地加密文件保护，不放进源码或提交包。邮箱与 Canvas 可选；QQ 使用 IMAP 授权码，Microsoft 365 取决于学校或组织授权。

启动进入固定“秘书”对话。说“刷新一下”或“有什么要处理的”，收取已连接邮箱并分类汇总；首次查看最近 7 天，之后增量收取。接着讨论邮件、提出待办或起草回复；复杂文件任务进入子对话，秘书可继续使用。普通通知与广告折叠保留。定时整理需电脑在线且未休眠；Windows 需保持用户登录。

开发需要 Node 24、Python 3.12；Mac 打包另需 uv 和 LibreOffice 渲染资源。运行 Python Skill 需准备本机 Linux Docker 引擎与 Skill 镜像；Windows 配置见上方指南。

```sh
git clone https://github.com/whatislove-baby/task-manager-secretary.git
cd task-manager-secretary
python3.12 -m venv .venv
.venv/bin/python -m pip install -r backend/requirements-mac.txt
npm ci
npm run dev
```

```sh
npm run check
npm run test:handover
npm run runtime:documents:mac
TM_MAC_OUTPUT=release-secretary-home npm run pack:mac
open -a Docker
.venv/bin/python scripts/prepare-skill-runtime.py
```

开发数据在 `data/`，与成品隔离；模型请求消耗所配置 API 的额度。

## 主要接口与能力

- React/Electron → 白名单 IPC → Python AgentRuntime。
- 官方 Deep Agents `create_deep_agent`、LangChain 工具与 ChatOpenRouter → OpenRouter；LangGraph 保存检查点并支持澄清恢复。
- 邮件、受管文件、任务、草稿与经验保存在本地；来源和实际产物回到聊天界面。
- 内置工具读取和生成 PDF、Word、CSV，保留原件，检查输出后加入回复草稿；Word 转 PDF 使用随包渲染器。
- 导入已开发的 Skill 包，测试样例、审核指定版本后复用；沟通偏好和报告写法保存为文字经验。当前路线不以 AI 现场编写业务程序作为交付保证。
- 正式待办变更、经验/Skill 批准、SMTP 发信分别由用户确认。本地日历不等于外部日历同步；定时整理不自动执行 Skill。

## 示例结果与交接

[秘书主页验收](handover/secretary-home/final-run/TEST-LOG.html)记录收取与分类、上下文处理、报告子任务、Word/PDF 双附件草稿、同项改期和重复刷新。邮件使用合成协议输入，模型调用真实 API；并行执行与口头进度回复的结果分别列出。

[学生测试](handover/final-guide/student-test/README.md)覆盖待办新增、改期、取消及新会话历史查询，保留原失败和修后复测。[合成实习测试](handover/public-demo-intern/README.md)完成报名资料 → Word/PDF 简报 → 页面检查 → 双附件回复草稿。

[15 轮长任务时间线](handover/long-task-public/TEST-LOG.html)记录 **9 轮通过、6 轮部分通过**，包含实际摘要和新会话检索证据；“三个月后”仅为参考时钟模拟。公开示例使用合成邮件和真实模型，没有验证真实 IMAP 收取、SMTP 发送或外部日历写入。

交接经验：工具执行成功与业务解释正确必须分别检查，当前审批状态不能只依赖旧对话。同学仍需按手册亲自运行并记录贡献和交接；开发者测试不能代替团队交接。结构检查也不保证内容或排版必然正确。

基础项目来自 [Gaojiashen/task-manager](https://github.com/Gaojiashen/task-manager) 的 `5e583cd`。本分支增加 Mac 适配、邮件整理、附件与草稿、对话界面和技能库；复用 Deep Agents、LangChain/LangGraph、Docker、ReportLab/python-docx 等开源工具。Codex 辅助实现、排错和测试；团队贡献提交前据实补齐。
