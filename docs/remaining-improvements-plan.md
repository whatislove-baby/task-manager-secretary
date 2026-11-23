# 恢复指引与完整对话导出

2026-11-23，用户授权在现有 `codex/secretary-incremental-improvements` 分支补齐原建议中的两项并检查。基线为 `fcb9f26`。

## 约束

- 保持 React/Electron → 白名单 IPC → Python → Deep Agents/LangGraph → SQLite 与人工审核架构。
- 不新增数据库结构、模型依赖、自动重试、邮件发送或任务自动审批。
- 新增公开失败诊断只使用可控分类与文案，不将原始供应商异常或请求内容写入这些字段；保留框架原有内部检查点机制。
- 导出仅包含当前对话的可见问答，按需附来源和产物名称；不导出内部事件、凭据、附件内容或隐藏上下文。
- 保留前三项已验收功能；合成数据检查，不接触真实邮箱。

## 任务 1：失败分类

在 Python 的现有失败处理增加可选 `errorCode`：`connection`、`rate_limit`、`authentication`、`configuration`、`execution_limit`、`model_response`、`unknown`。使用异常类型、可信状态码或已知内部校验，不透传供应商错误文本。保留兼容的 `error` 文案；恢复时清除错误分类并沿用相同 run ID 与原产物。覆盖常见断网、认证、限流、执行预算、未知异常与旧记录；全程不用真实模型。

## 任务 2：完整对话导出

增加共享 Markdown 格式器、对话顶部“导出对话”入口，支持“复制完整对话”和“保存 Markdown”，可选附来源/产物名称。复制和保存均重新读取当前指定对话，保留问答顺序；未完成的文本明确标记，已写入回答的 run 不重复追加。导出成功、取消、失败与空对话有清楚反馈。

保存走 `agent:exportConversation` 白名单 IPC，只允许 `{id, includeSources}`；桌面端读取真实对话并通过原生保存窗口决定路径，renderer 不能提交路径或内容。新 `AgentAPI.exportConversation` 必须与 preload、白名单一致，返回 `Result<boolean>`（取消为 false）。格式器 `conversationMarkdown(conversation, includeSources=false)` 与 `conversationFilename(title)` 放在 `src/shared/conversation-export.ts`。检查打包清单包含所有新增主进程依赖。

## 任务 3：恢复界面与整体验证

失败卡解释原因、下一步、已有进度保留，并按类型提供“继续本轮”和“打开模型设置”。不承诺每次都从精确断点恢复，不自动重建任务。旧记录无分类时采用通用指引；暂停、取消和停止中的行为保持原样。补充界面测试，运行类型检查、前后端回归、构建与实际桌面链路检查，独立审查后本地提交。

## 分工及文件边界

- 后端实现：仅 Python 失败分类模块与后端测试。
- 导出实现：共享导出格式器/类型、preload/IPC/原生保存、独立导出组件、AgentApp 顶部接入、相关测试/必要打包清单。
- 主线程：独立恢复组件及测试，导出接入完成后修改 AgentApp 失败区域；文档与整体校验。
- 跨任务接口：`AgentRun.errorCode` 使用上述联合类型（由导出实现者在共享类型补入）；恢复卡消费它，后端产生它。
