# 第 6 轮经验草稿正文缺失：只读定位

诊断依据是本次合成课程场景的真实模型运行，不是新增模拟结果。本文件只补充说明；没有修改产品/测试源码、模型答复、旧判分，没有额外调用模型，也没有手工补写或批准经验。

## 结论与证据边界

**没有发现 learning schema 或经验保存服务截断正文。** 前两次调用的 `instruction` 在 LangGraph 保存的模型 `AIMessage.tool_calls` 中就只有标题；进入业务工具的参数与最后保存的 `proposal.instruction` 完全一致。问题发生在业务工具之前。

当前记录没有原始 SSE `function.arguments` 分片。官方适配器接收 arguments 分片，LangChain 汇聚并解析工具参数，因此仅凭已解析的 AIMessage 不能严格区分模型/服务商输出异常与 SDK 流式组装问题。这里不能把原因直接定为“模型不聪明”，也不能把缺失正文归咎于本应用的保存逻辑。

| 实际调用 | checkpoint 参数字符数 | 业务工具参数字符数 | 保存字符数 | 三处一致 | 状态 |
|---|---:|---:|---:|---|---|
| v1 | 16 | 16 | 16 | 是 | 待审核，只有标题 |
| v2 | 28 | 28 | 28 | 是 | 待审核，重复标题 |
| v3 | 294 | 294 | 294 | 是 | 待审核，正文完整；随后实际读回 |

三次模型消息的 `finish_reason` 都为 `tool_calls`。v1 内容是“## 触发场景与适用范围【重要】”，v2 是“## 触发场景与适用范围＃＃ 触发场景与适用范围【重要】”。v3 在同一草稿版本链上自然补齐：仅指定老师邮箱、中文简短、先结论、两三条确认问题、未完成事项不得写成已完成等。`get_learning` 第 6 条日志读回 v3，正文长度和 SHA-256 与写入一致。

## 应用自己的质量缺口

[LearningProposal 的 instruction 定义](../../backend/task_backend/learning_schema.py)只要求长度为 1–20000；[learning.py](../../backend/task_backend/learning.py)检查非空、长度、控制字符并去除首尾空白。它没有检查“正文是否只有标题”或“是否包含用户教的主要规则”，因此前两次标题也能成功写入待审核草稿。

[learning_tools.py](../../backend/task_backend/learning_tools.py)只是克隆 JSON、记录真实用户教学来源并调用保存服务；[agent_tools.py](../../backend/task_backend/agent_tools.py)的 wrapper 进行模型验证和 `model_dump`，不做 Markdown 截断。[agent_openrouter.py](../../backend/task_backend/agent_openrouter.py)的本地兼容层只调整重复的 reasoning 格式元数据，没有修改工具 arguments。用户的完整原话仍保存在教学来源中，但保存来源不等于保存了可复用正文。

这是质量检验不足，和“是否把数据保存完整”是两个问题。人工审批边界仍有效：本轮没有自动批准任何经验。将来若要修复，应先增加可诊断的工具输入完整性证据与轻量内容检查；本次定位没有实施修改。

## 自然恢复与原始失败保留

本轮 144 秒自然结束。模型最后公开说明前两版不完整，要求审核第 3 版，未把它说成已经批准生效。这个恢复说明当前回读/修订机制能起作用，不能据此删去前两次失败。

原 `verification-06.json` 的 `structuralPassed=false` 保持原样：`one_sender_scoped_note` 要求本轮只有一份经验 artifact，而本轮有三个版本。`source_unchanged=true`、`no_automatic_approval=true`。最终 v3 是否值得批准需单独内容审核；本诊断未代替审核，也未重算或覆盖原判分。

## 可复核文件

- [工具原始记录](tools-06.jsonl)：第 3、4、5 行为三次 propose_learning，第 6 行为 get_learning。
- [完整第 6 轮](turn-06.json)：原答复、实际工具结果与会话。
- [原始判分](verification-06.json)：失败项与未改源码/未自动批准记录。
- [小型结构化诊断](memory-draft-diagnosis.json)：版本 ID、调用 ID、长度、SHA-256、逐层匹配与不确定边界；未复制 checkpoint 原始数据库、模型推理文本或凭据。

checkpoint 只在已有隔离验收资料库以只读方式查询；公开 JSON 保留所需摘要。所有邮件身份及课程情境均为合成示例，没有读取实际邮箱或发送邮件。
