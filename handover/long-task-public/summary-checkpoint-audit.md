# 长会话摘要机制：实际 checkpoint 核验

检查范围：主会话第 1–12 轮，终点为 [第 12 轮完成结果](turn-12.json)。只读查询现有隔离验收库，未修改源码、未强行触发摘要、未补模型调用。完整数字与 checkpoint ID 见 [结构化核验](summary-checkpoint-audit.json)。

## 实际发生了什么

**官方摘要实际触发过，因此这次并非只有普通窗口内的十二轮保持。** 第 1–5 轮没有摘要事件；第 6 轮首次出现，到第 12 轮共记录 16 个不同 cutoff 的 `_summarization_event`。这些事件的 summary_message 带有 `lc_source=summarization`，并指向官方 `/conversation_history/…` 虚拟历史文件。历史文件从官方 files DeltaChannel 重建后实际存在：68,654 字符、358 行。报告只保存长度和 SHA-256，没有复制原始历史文本或模型推理内容。

当前源码配置为 `SummarizationMiddleware(trigger=('tokens',32000), keep=('tokens',6000), trim_tokens_to_summarize=None)`。摘要计数检查的是实际保存事件，不是看到这行配置就宣称通过。16 次说明机制确实工作，也提示压缩较频繁；本次未评估每次是否必要或是否有性能优化空间。

| 用户轮次终点 | 原始状态消息数 | 应用摘要后的消息数 | 摘要状态 | 本轮新增摘要事件 |
|---|---:|---:|---|---:|
| 1 | 7 | 7 | 未发生 | 0 |
| 2 | 19 | 19 | 未发生 | 0 |
| 3 | 26 | 26 | 未发生 | 0 |
| 4 | 34 | 34 | 未发生 | 0 |
| 5 | 39 | 39 | 未发生 | 0 |
| 6 | 53 | 21 | 已发生 | 4 |
| 7 | 66 | 21 | 已发生 | 4 |
| 8 | 73 | 24 | 已发生 | 2 |
| 9 | 81 | 27 | 已发生 | 2 |
| 10 | 86 | 26 | 已发生 | 2 |
| 11 | 91 | 24 | 已发生 | 1 |
| 12 | 94 | 23 | 已发生 | 1 |

第 12 轮原始状态有 94 条消息：24 HumanMessage、36 AIMessage、34 ToolMessage。HumanMessage 包含中间件补入的当前状态，不等于 24 次用户发言。应用最近摘要后是 23 条消息：9 human、9 ai、5 tool。

当前 LangGraph 对 messages/files 使用 DeltaChannel；有些 checkpoint 骨架不直接包含 messages。核验使用官方 `SqliteSaver.get_delta_channel_history` 和 Deep Agents 原 reducer 重建消息/文件，未把骨架的空字段误判成“历史丢失”。这不会执行 Agent 或发送网络请求。

## Token 数字的准确含义

主会话 checkpoint 中可观察到 36 个去重的 AIMessage，均带 provider usage_metadata：

| 可观察指标 | 数值 | 能说明什么 |
|---|---:|---|
| 这些模型调用累计 input tokens | 1,106,403 | 每次请求反复携带历史，故重复计入；不是一段百万 token 长上下文 |
| 累计 output tokens | 23,326 | 这些已保存模型消息报告的输出量 |
| 累计 total tokens | 1,129,729 | 上述消息元数据合计，不是完整账单 |
| 单次调用 input tokens 范围 | 16,390–53,592 | 可观察请求输入范围，包含 provider 计算的上下文 |
| 第 12 轮原始消息近似 token | 23,297 | 仅消息列表，未计 system prompt 和 tool schema |
| 第 12 轮应用摘要后消息近似 token | 8,361 | 同样不包含 system/tools；也不是 API 下一次请求的完整输入 |

独立摘要调用、子助手调用、重试若未保存在主会话 AIMessage 中，就不在这里的 usage 合计。缓存计费由 provider 决定，所以不能拿累计数直接算成本。近似计数使用 `count_tokens_approximately`，没有 system/tools，不应直接拿 23,297 或 8,361 对比 32,000 的完整请求摘要阈值。

## 长记忆验收应分三种证据

1. **第 1–5 轮：普通窗口保持。** 没有摘要事件，不能称为压缩后的记忆测试。
2. **第 6–12 轮：已经发生摘要后的继续工作。** 特别是第 12 轮回问第一封的页数、摘要与展示时长，发生在真实摘要之后；答对哪些内容仍应与邮件原文、实际检索和原答复逐条核对。机制触发不等于证明摘要无遗漏。
3. **第 13/15 轮：新会话跨会话检索。** 这是不同 conversationId 下从已保存经验/邮件/任务查回信息；不能说成模型在同一上下文中永久记住，也不能仅凭最后答复判断发生过检索。应分别引用对应轮的工具和持久化记录。本文件的主会话数字截至第 12 轮，不能冒称整套 15 轮的总 token。

## 依据

- [主会话第 12 轮](turn-12.json)与[原验证结果](verification-12.json)：自然完成、原答复、会话 ID 和 source_unchanged。
- [结构化摘要审计](summary-checkpoint-audit.json)：311 个相关主会话 checkpoint、16 个事件摘要、逐轮重建消息量、usage 的范围限制。
- [第 6 轮经验草稿诊断](memory-draft-diagnosis.md)：保留残缺 v1/v2、自行修复 v3 与未覆盖原判分的事实；摘要已经触发并不抹掉这一失败。
- [当前运行源码](../../backend/task_backend/agent_runtime.py)：官方摘要配置、SQLite saver、thread_id 与执行调用。

本检查不替代全 15 轮的人工语义评估；摘要后仍可能出现错误推断，跨会话检索也需要确定范围和读取原文。
