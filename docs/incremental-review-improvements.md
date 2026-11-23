# 审核体验增量实现

用户于 2026-11-23 确认在 `codex/secretary-incremental-improvements` 实现以下三项并测试。基线为 `d7aa505`。

## 范围和约束

沿用 React/Electron → IPC → Python → Deep Agents/LangGraph → SQLite 与人工审核流程。不改数据库结构，不加入新模型依赖，不自动合并任务或发送邮件。

## 实现内容

1. 待办重复提醒：在现有草稿审核中，以邮件来源、规范化标题和截止日期提示可能重复的本地待办。显示匹配原因与已有任务详情，允许调整选择或明确继续添加。排除删除的任务，显示已完成状态；不能把同一封邮件中的所有不同事项视为确定重复。保存前读取最新待办；读取失败时明确报错、保留草稿。保留原有批次幂等和父子关系。
2. Word/PDF 配套检查：从发送预览中实际附件的转换来源 ID、哈希和版本判断，不按文件名猜测。显示配套、不匹配、来源未知，区分未附源 Word 的情况；任意附件不被强制成对。保持原有版本校验和人工确认发送机制。
3. 待确认事项分组筛选：沿用 `listPending` 返回值和原有审核卡，增加类型计数、标题搜索、新旧排序、分组和清空筛选。区分空列表和无匹配结果，保留返回原对话和原审核操作。

## 分工与验证

- 任务 1：`Tools.tsx`、独立重复匹配模块和相关测试。
- 任务 2：`MailComposePanel.tsx`、独立附件配套模块、必要的文件类型声明和相关测试。
- 任务 3：`AgentApp.tsx`、独立待确认列表组件和相关测试。
- 先补能暴露缺失功能的测试，再实现；合并后运行类型检查、完整前端/后端测试及构建，并审查差异。
- 重点覆盖：同邮件不同事项、改期、完成/删除任务、重复确认、父子草稿；同名不同源/同源旧哈希/无转换记录/只附 PDF；筛选后的计数、空结果和审核后列表更新。
- 使用合成数据验证，不连接真实邮箱，不实际发送邮件。

## 使用方法

### 待办重复提醒

在待办草稿审核窗口，候选事项下方会显示“可能重复的已有待办”，包括标题、完成状态、截止日期、父待办和匹配原因。点击“确认保存”或“全部添加”会重新读取当前待办。

发现疑似重复时不会立即保存。可以取消勾选相应草稿，再点击“确认保存”；如果确实需要创建另一个任务，点击“仍然添加 N 项待办”。“全部添加”仍包含未勾选项。读取当前待办或已保存父项关联失败时保留草稿，修复读取问题后重试。

规则为规范化标题相同，并且来源邮件相同，或在来源不冲突时截止日期相同。仅有相同来源邮件或相同标题不会触发。标题会统一大小写、全半角、空白和部分末尾标点。已完成事项会提示，回收站事项不参与匹配。子项默认继承及分批保存后父项改期均按实际保存规则核对。

这是保存前的提醒，不是语义判断或数据库唯一性约束；换一种标题表述、或两个窗口恰好同时保存，仍可能出现重复。程序不会自动合并、覆盖或删除已有任务。

### Word/PDF 配套检查

在邮件“预览并确认发送”的实际附件列表中查看每份 PDF：

- **配套**：附带源 Word，且来源 ID、内容哈希和修订版本全部一致。
- **不匹配**：所选 Word 与 PDF 的转换来源不一致，或版本/内容不同；文字说明具体原因。
- **来源未知**：没有完整的 Word 转 PDF 记录，不能由文件名判断。
- **未附源 Word**：PDF 有转换记录，但本次仅附 PDF；如同时选择其他 Word，显示“不匹配 · 未附源 Word”。

多个 Word/PDF 组合分别检查。提示不会强制用户附带 Word，也不会自行发送邮件。现有附件失效检查和发送前人工确认仍生效。

### 待确认事项分组筛选

打开侧栏“待确认”，按类型按钮筛选，输入事项标题关键词，并选择“最新优先”或“最旧优先”。排序在每个类型组内执行。类型按钮数字表示整个待确认队列数量，“显示 X / Y 项”表示当前筛选结果。

“清空筛选”恢复全部类型、空关键词、最新优先。审核卡和“回到原对话”沿用原操作；审核完成后重新计算数量，其他事项仍在时保留所选类型。没有待确认事项与筛选无结果分别提示。

## 验证环境与范围

2026-11-23 在 macOS arm64、Node 24、Python 3.12 验证。没有改动依赖清单、后端、IPC 白名单或数据库结构。界面新增逻辑使用现有 API。

| 检查 | 最终结果 |
| --- | --- |
| `npm run typecheck` | 通过 |
| `npm test` | 418 项：417 通过、1 跳过、0 失败；跳过项仅适用于 Windows 临时目录占用 |
| 原 Python 套件，使用下述临时测试适配 | 691 项：680 通过、11 跳过、0 错误 |
| `npm run build` | 通过；保留大于 500 kB 分块的体积提示 |
| 实际 Electron → preload → Python → SQLite 启动检查 | 通过；临时目录中的合成任务与页面操作 |
| 新增组件的桌面渲染检查 | 通过；合成数据，无真实邮件或凭据 |
| 独立代码审查及修复复审 | 通过 |
| `git diff --check` | 通过 |

初次直接运行后端套件遇到 13 项环境错误：9 项无法访问系统钥匙串、2 项本机端口受限、2 项没有配置 Word 渲染器。随后配置本机 LibreOffice，并仅在临时测试入口用内存中的随机主密钥替代系统钥匙串存取，重跑原套件。Fernet 加解密、服务、存储和文档转换保持原实现；未验证真实钥匙串权限。11 项真实 Docker 验收按原配置跳过。

初次环境错误涉及：`test_sync_is_idempotent_preserves_local_state_and_skips_deleted`、`test_real_chart_image_and_searchable_values_survive_rendering`、`test_table_prioritizes_narrative_and_keeps_each_row_together`、`test_bindings_have_independent_encrypted_credentials_and_disconnect`、`test_changed_server_requires_new_secret_and_bad_registry_not_overwritten`、`test_separate_connection_cannot_restore_credentials_after_disconnect`、`test_pkce_uses_existing_localhost_registration_and_rejects_wrong_state`、`test_windows_socketpair_and_event_loop_still_work`、`test_successful_segments_are_not_requested_again_after_pause_and_restart`、`test_ai_extract_validates_output_without_saving_and_confirmation_writes`、`test_endpoint_change_requires_new_key_and_http_rejected`、`test_malformed_ai_output_never_saves`、`test_settings_encrypts_key_and_clearing_preserves_endpoint`。

两项父子任务漏报由独立代码审查发现，先补回归测试复现再修复，并已通过复审。发送预览的转换来源还用实际 Python 服务和合成附件验证保留，未调用 SMTP。桌面检查使用独立临时目录，验证生产界面、preload、Python、SQLite 和页面切换；三个新增组件另用合成数据检查排版。

本次未测试真实邮箱收发、真实模型调用、Windows 打包或真实 Docker 技能执行。构建保留原有的大文件分块体积提示。
