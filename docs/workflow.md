# 邮件助手工作流 附件与文件处理版

灰色表示本轮开发前已有的能力（包括上一次新增的定时整理）；绿色表示本轮新增；橙色表示本轮改动。上次版本保留在 [定时整理版流程图](workflow-scheduled-mail.md)。

```mermaid
flowchart TD
    INBOX["邮箱／Canvas"]:::old --> IMPORT["手动导入／定时获取邮件<br/>09:00、18:00 香港时间"]:::old
    IMPORT --> MAILS[("本地邮件库")]:::old
    MAILS --> CLASSIFY["自动分类／提取待办建议"]:::old
    CLASSIFY --> REVIEW["人工审核草稿"]:::old
    REVIEW --> TODOS["确认保存 → 待办／日历"]:::old
    MAILS --> CHAT["改动：邮件关联对话<br/>普通问答／文件处理两种模式"]:::changed
    MAILS --> PICKATT["新增：选择单个邮件附件<br/>校验邮箱身份后按需下载"]:::added
    LOCAL["新增：原生窗口选择本地资料<br/>PDF／DOCX／TXT／MD"]:::added --> STORE
    PICKATT --> STORE[("新增：文件工作区<br/>原件副本、来源、校验值")]:::added
    STORE --> READ["新增：读取文字并显示限制<br/>不支持或损坏则明确报错"]:::added
    READ --> SELECT["新增：用户选择参考文件<br/>及只阅读／输出文件格式"]:::added
    SELECT --> CHAT
    CHAT --> JOB["新增：文件后台任务<br/>关联历史＋所选资料＋当前要求"]:::added
    JOB --> MODEL["新增：模型生成结构化内容<br/>校验完整性、资料和配置版本"]:::added
    MODEL --> ANSWER["新增：只阅读 → 对话回答"]:::added
    MODEL --> ARTIFACT[("新增：生成新文件<br/>PDF／DOCX／TXT／MD<br/>原资料不覆盖")]:::added
    ARTIFACT --> CARDS["新增：结果卡片<br/>预览文字／另存／加入回复"]:::added
    CARDS -->|选择上一版继续修改| SELECT
    STORE -->|选为附件| COMPOSE
    CARDS -->|加入邮件附件| COMPOSE["改动：起草／编辑回复<br/>可选让 AI 参考附件文字"]:::changed
    MAILS -->|用户主动回复| COMPOSE
    COMPOSE --> SNAPSHOT["改动：固定发送预览<br/>收件人、正文、附件名称与校验值<br/>附件字节独立保存"]:::changed
    SNAPSHOT --> CONFIRM["人工检查并确认发送"]:::old
    CONFIRM --> SMTP["改动：SMTP 提交正文和附件"]:::changed
    SMTP --> RECEIPT["发送回执／异常结果<br/>结果未知不自动重发"]:::old
    JOB -.-> RESUME["新增：文件任务检查点<br/>暂停／恢复／失败后继续<br/>保留已生成结果避免重复"]:::added
    RESUME -.-> JOB
    classDef old fill:#eeeeee,stroke:#949494,color:#353535
    classDef added fill:#e0f2e8,stroke:#39825b,color:#173d2a
    classDef changed fill:#fff0d4,stroke:#bb8429,color:#604009
```

文件任务由固定工作流调度，不执行模型生成的脚本，也没有新增通用 MCP、跨会话技能或自动发信。每次明确选择资料；定时整理不会自动把所有附件交给模型。

PDF／Word 读取正文文字后生成新文档，不保证保留原排版；扫描件需先 OCR。对话中的历史文件不会被自动全部重新读取，修改时应选中对应文件。只有点击文件处理提交或勾选附件参考后点击 AI 起草，相关文字才发送到设置中的模型。

代码入口：`mail_files.py`（读取和产物）、`document_assistant.py`（文件任务）、`operations.py`（队列和会话）、`mail_compose.py`（固定附件快照）、`MailFiles.tsx`／`AIHistoryPanel.tsx`／`MailComposePanel.tsx`（操作界面）。完整验证记录见 附件与文件处理报告（本地历史材料未随仓库发布）。
