# 完整 Agent 架构与工作流

更新：2026-11-22。[打开单张可缩放大图](../handover/final-guide/workflow.html)；[SVG 原图](../handover/final-guide/workflow-complete.svg)；[PNG 原图](../handover/final-guide/workflow-complete.png)。所有路径在同一个画布中，可拖动、滚轮缩放、适应全图，或用按钮聚焦同一图中的框架层、收信支路和人工审核。图中只保留关键词与步骤；能力范围、运行条件及状态解释集中写在本文。

灰色代表沿用的上游业务或通用开源机制，蓝色代表本应用新增或增强，橙色代表用户决定。固定秘书、口头刷新和独立任务对话已在同一张图中以蓝色标出；颜色不是测试结论，也不意味着一个模块全部由某人编写。

## 真实主链路

用户文字与所选邮件/文件 → React/Electron 聊天界面 → preload / IPC 白名单 → PythonBackend JSON line RPC → AgentRuntime 保存用户轮次并启动后台 worker → 官方 `create_deep_agent` → 读取当前本地状态、选择业务工具或委派文档子助手 → 业务库/实际文件写入 → `events`、`sources`、`artifacts`、答复经 run 轮询回到前端 → 用户检查并批准正式待办、技能/经验版本或具体邮件发送。

打开应用先进入 `agent:getSecretary` 返回的固定秘书会话。用户说“刷新一下”，主助手调用 `refresh_inbox` 收取已连接邮箱并分类；汇总卡默认展开需处理、重要、待核实，折叠普通与疑似垃圾。后续可直接说“把那个课程截止记一下”，继续用同一上下文，但创建草稿与批准正式任务仍是两步。

复杂文件任务通过 `create_child_conversation`，或界面“单独处理这件事”，建立用户可打开的独立任务对话：保存 `kind=task`、父会话、原请求与所带邮件/文件 ID，并原子写入第一轮和排队 run。模型分派的当前主轮结束后，后台 worker 自动执行子任务；它拥有自己的 LangGraph `thread_id`。`agent_worker.run_queues` 为秘书保留一条固定线程，其他普通/任务对话在另一条线程串行运行。每条线程拥有自己的 BackendService、SQLite 连接与 asyncio loop；构造阶段串行初始化，领取 run 时在事务内复核 queued。同步文档工具占用后台线程时，秘书仍可回答进度。PDFium native 渲染段采用进程内互斥，LibreOffice 每次使用独立临时用户目录。秘书用 `get_child_tasks` 查询实际进度和产物，不凭旧答复猜测，不在主页重复生成报告。`completed` 只表示该轮执行结束，未发草稿仍未发。 前端通过持续读取 `getConversation` 返回的子任务摘要，在消息下的任务卡显示当前状态与查询时间；聊天文字保留生成时的观察，不随后台完成自动改写。实测中调度确实并行，但模型回答可能慢于后台完成，继续复述较早状态，当前状态应结合这张实时卡核对。

模型需要澄清时，`ask_user` 调用 LangGraph `interrupt`，保存任务并等用户补充；`resumeRun` 以 `Command(resume=...)` 继续。澄清回复不是邮件发送批准。暂停/失败可从检查点继续，已保存文件和草稿不因本轮失败而消失。

## 三层框架分别做什么

| 层 | 当前实际使用 | 本应用增加的连接 |
|---|---|---|
| Deep Agents | `create_deep_agent`，官方子助手机制、SkillsMiddleware、SummarizationMiddleware，CompositeBackend / StateBackend | general-purpose 与 document 的职责、只读资料挂载、已批准技能目录与应用文件工具 |
| LangChain | `@tool` / ToolRuntime、模型消息与工具调用、middleware、ChatOpenRouter | AgentTools/EvidenceAgentTools、FreshLocalContext、DeliveryReview、业务收尾状态与少量兼容修补 |
| LangGraph | 执行图、AsyncSqliteSaver、thread_id、interrupt/Command、astream | conversationId 对应 thread_id；SQLite 检查点；UI 的暂停、补充与恢复 |

当前生产模型路径是 `FormatPreservingChatOpenRouter` → `langchain_openrouter.ChatOpenRouter` → OpenRouter API，不是直接实例化 `ChatOpenAI`。本地已安装的 ChatOpenRouter 继承 `BaseChatModel`。兼容层只处理流式元数据和有限连接重试，没有替换官方 Agent 工具循环。API 中的模型由用户设置，不把框架和 DeepSeek 模型混为一谈。

文档写入工具只注册给 document 子助手；主助手负责来源、委派与最终草稿交付。内部子助手并不等于 UI 中可以跳转的子会话。`write_todos` 是执行计划，也不是正式本地日历事项。

## 共用的业务能力

- 邮件：搜索已导入本地资料、读取原文、按需导入附件、检查同账号/发件来源。搜索工具不检查远端是否有新邮件。
- 文件：受管副本、实际结构和页面检查、python-docx/ReportLab 图文生成、CSV 处理、LibreOffice 从原 DOCX 转换 PDF、pypdf 按指定顺序另存原 PDF 页面。原件和版本留存，PDF 并不因此获得任意原位编辑能力。
- 交付：验证实际文件 ID/哈希并加入邮件草稿。结构可打开、模型认为版面通过，都不等于业务内容必然正确；正式发送仍由用户预览确认。
- 任务：创建/改期/取消提议后人工审核，同一任务保持身份与 revision。取消不等于完成；历史查询可包含回收站并检查批准记录。本地 Todo/Canvas 订阅不等于 Google 或 Outlook 日历双向写入。
- 技能：导入已开发 Python Skill 包，验证样例，审核批准特定版本；以后通过官方 SkillsMiddleware 发现并在本地 Docker 执行。文字经验单独保存并审批。本图不把自动编写任意业务脚本画成可靠可用。

## 三条收信入口，共用原件与业务状态

手动收件箱按钮经 `mail-operations:startInboxSync` 启动收信 job，范围为最近七天的已连接收件箱；自动跳过已导入记录。定时整理通过 Mac LaunchAgent 或 Windows 任务计划程序启动短任务 `automation_worker`，按已批准的 IMAP 账号/范围收取，独立调用模型分类，并对需要行动的邮件提取待办草稿。它不启动对话 Agent 的工具循环，不自动执行任意 Skill，也不发送或删除邮件。

秘书的 `refresh_inbox` 复用实际分页导入引擎，初次覆盖最近七天，后续按账号与邮箱身份保存导入进度并留一天重叠。每轮最多分类 20 封；剩余数量、分类失败、未连接账号和导入失败明确返回，未分类不会被当成垃圾隐藏。同一执行 ID 可重放已有结果；进程锁避免两次刷新并发重复分类，中断的导入/分类记录可继续。

分类为 `action / important / normal / spam / review`，保存摘要、依据与版本；`get_secretary_briefing` 读取本地汇报记录，`correct_mail_category` 持久保存人工纠正并保留原分类。符合相同内容版本的既有定时整理结果可复用；分类只改变本地展示，不删原信，不把“需要行动”自动当成获批日历事项。搜索工具仍只查本地，不能代替刷新。

`scopeStart/since` 是导入进度，fingerprint 是内容版本；`unread` 是服务器 Seen/isRead 的读取快照，程序阅读不自动修改远端标记。秘书成功完成汇报后记录本次已展示的分类版本 `briefedAt`，失败不消耗，旧汇报确认不能覆盖更新后的人工纠正。**已收取、已分类、已汇报、任务执行结束、用户已看、业务已完成不是同一个状态**：当前没有用户逐项“已看”回执，业务完成仍须查询任务、草稿、批准与发送记录。

## 数据与能力边界

资料与历史主要本地保存；模型请求涉及的文字与实际页面图像会发送到用户配置的 API。离线 Docker Skill 不代表整个 AI 应用离线。没有任意宿主 shell、任意目录读写、通用 MCP/联网搜索、无限记忆或所有文件格式的保证。日历操作和 SMTP/技能批准分别走真实业务状态，不能以一句聊天答复代替回执。

## 当前源码索引

以下仅链接通用产品源码，不含私人业务程序、数据包或内部验收记录。

| 职责 | 源码 |
|---|---|
| 聊天入口、轮询run和结果卡 | [src/renderer/AgentApp.tsx](../src/renderer/AgentApp.tsx) |
| 手动收取新邮件界面 | [src/renderer/AgentPages.tsx](../src/renderer/AgentPages.tsx) |
| IPC白名单 | [src/main/python-channels.ts](../src/main/python-channels.ts) |
| Electron至Python JSON line RPC | [src/main/python-backend.ts](../src/main/python-backend.ts) |
| 业务RPC分派 | [backend/task_backend/service.py](../backend/task_backend/service.py) |
| 运行图、澄清、子助手与检查点 | [backend/task_backend/agent_runtime.py](../backend/task_backend/agent_runtime.py) |
| 秘书专用与后台执行线程 | [backend/task_backend/agent_worker.py](../backend/task_backend/agent_worker.py) |
| ChatOpenRouter流式兼容层 | [backend/task_backend/agent_openrouter.py](../backend/task_backend/agent_openrouter.py) |
| LangChain业务工具注册表 | [backend/task_backend/agent_tools.py](../backend/task_backend/agent_tools.py) |
| 原文读取证据 | [backend/task_backend/agent_evidence.py](../backend/task_backend/agent_evidence.py) |
| 补入当前本地状态 | [backend/task_backend/agent_fresh_context.py](../backend/task_backend/agent_fresh_context.py) |
| 正式交付前核对 | [backend/task_backend/agent_delivery.py](../backend/task_backend/agent_delivery.py) |
| 官方Backend与只读挂载 | [backend/task_backend/deepagents_backend.py](../backend/task_backend/deepagents_backend.py) |
| 文档业务工具 | [backend/task_backend/document_agent.py](../backend/task_backend/document_agent.py) |
| Word/图表排版 | [backend/task_backend/document_formats.py](../backend/task_backend/document_formats.py) |
| LibreOffice/PDFium渲染 | [backend/task_backend/document_render.py](../backend/task_backend/document_render.py) |
| 文件检查记录 | [backend/task_backend/document_review.py](../backend/task_backend/document_review.py) |
| 子任务收尾机制 | [backend/task_backend/document_completion.py](../backend/task_backend/document_completion.py) |
| 文件版本与实际内容 | [backend/task_backend/mail_files.py](../backend/task_backend/mail_files.py) |
| 技能包导入契约 | [backend/task_backend/skill_import.py](../backend/task_backend/skill_import.py) |
| 技能版本与审批 | [backend/task_backend/skill_library.py](../backend/task_backend/skill_library.py) |
| Docker隔离执行 | [backend/task_backend/skill_execution.py](../backend/task_backend/skill_execution.py) |
| 经验记录 | [backend/task_backend/learning.py](../backend/task_backend/learning.py) |
| 待办变更审批 | [backend/task_backend/task_changes.py](../backend/task_backend/task_changes.py) |
| 待办草稿状态 | [backend/task_backend/task_draft_state.py](../backend/task_backend/task_draft_state.py) |
| 邮件草稿和SMTP批准 | [backend/task_backend/mail_compose.py](../backend/task_backend/mail_compose.py) |
| 手动收信job | [backend/task_backend/operations.py](../backend/task_backend/operations.py) |
| 自动整理和分类状态 | [backend/task_backend/mail_automation.py](../backend/task_backend/mail_automation.py) |
| 系统定时后台宿主（Mac / Windows） | [src/main/mail-automation-host.ts](../src/main/mail-automation-host.ts) |
| 后台worker入口 | [backend/task_backend/automation_worker.py](../backend/task_backend/automation_worker.py) |
| IMAP/Graph只读收信适配 | [backend/task_backend/mail_readers.py](../backend/task_backend/mail_readers.py) |
| 本地邮件与分析状态 | [backend/task_backend/mail_store.py](../backend/task_backend/mail_store.py) |
| 固定秘书与父子会话结构 | [src/shared/agent.ts](../src/shared/agent.ts) |
| 秘书收信、分类与版本汇报 | [backend/task_backend/secretary.py](../backend/task_backend/secretary.py) |
| 秘书对话工具与任务路由 | [backend/task_backend/secretary_tools.py](../backend/task_backend/secretary_tools.py) |
| 折叠分类摘要卡 | [src/renderer/SecretaryBriefing.tsx](../src/renderer/SecretaryBriefing.tsx) |
| 整理结果侧页 | [src/renderer/MailAutomationPanel.tsx](../src/renderer/MailAutomationPanel.tsx) |
| 本地日历与Canvas订阅 | [backend/task_backend/canvas.py](../backend/task_backend/canvas.py) |
