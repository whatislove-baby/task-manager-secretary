# 秘书主页验收记录

[打开最终逐轮测试日志](final-run/TEST-LOG.html)。日志展示每次用户原话、AI 完整回答、工具调用、保存后的任务/文件/草稿，以及独立审查结果。先看口头收信、独立报告任务、改期、并发四段即可。

## 已完成的主线

最终冻结后端使用正式 `agent_worker.run_queues`，从空本地收件箱开始：9 次用户输入、1 个独立后台报告任务。导入、分类、批准待办、生成 Word/PDF、核对实际双附件草稿、零新增刷新、批准同一任务改期均有持久状态证据。主线各次运行时间之和 576.30 秒，有并行重叠，不是墙钟时间或 API 计费时长。

- [原完整 73 项断言](final-run/independent-e2e-verification.json)：72 项通过；并发答复时机断言失败，原结果保留。
- [主报告文件独立审计](final-run/independent-child-audit/verification.json)：41 项通过，包含原始 CSV 数值、每份一页、Word 原生转换 PDF 的实际字节和独立渲染像素、草稿真实附件与无 SMTP。
- [分范围结论](final-run/acceptance-summary.json)：主业务状态检查通过，整体保留部分通过结论。

## 并发补测与限制

从真实第 3 轮快照另开隔离测试环境，不改第 4、5 条原话。父轮结束后立即问进度，没有审查暂停。正式秘书线程约 0.16 秒进入运行，查询时后台仍在工作，证明调度并行。

但秘书用了 85.25 秒才完成答复，后台用 63.65 秒完成；秘书最终仍沿用较早查询的“进行中、尚无草稿”，比实际后台完成晚 21.60 秒。因此“后台运行时快速且准确回答当前进度”未通过，不能用双线程存在代替体验验收。[补测原记录](concurrency-run/concurrency-verification.json)、[线程时间线](concurrency-run/worker-timeline.jsonl) 和 [原回答](concurrency-run/turn-05.json) 均保留。补测后台生成文件本身另有 [41 项通过记录](concurrency-run/independent-child-audit/verification.json)。

其他已知问题：模型把当天邮件说成“昨天”、把分类说成归档；由未完成待办推断用户尚未制作材料；应用交付提示把非必需的中间 CSV 也列为未附附件。这些都在逐轮日志中说明，没有改写模型输出。

## 测试边界

所有人物、地址、正文和附件均为全新虚构资料。邮箱仅替换 IMAP 协议边界，产品自己的 `ImapReader` 真正解析 MIME、分页读取正文和下载附件；没有把预存本地邮件称为实际收信。真实分类与 Agent 使用既有 OpenRouter API。没有连接生产邮箱，没有 SMTP。

另有 [37 项离线边界检查](offline-verification.json)，覆盖 401 封 MIME 分页、分类余量、重复执行、并发刷新去重、掉线重试、导入中断重启、固定秘书和父子关系、误分类纠正。该组分类使用固定 stub，不证明模型判断质量。

## 证据与重放入口

| 范围 | 脚本 / 数据 |
| --- | --- |
| 原始合成邮件与附件 | [inputs/scenario.json](inputs/scenario.json)、[inputs/eml](inputs/eml)、[inputs/attachments](inputs/attachments) |
| 原 7 条输入与追加第 8 条 | [requests.json](requests.json)、[requests-extra.json](requests-extra.json) |
| 协议边界与恢复 | transport_fixture.py（本地历史材料未随仓库发布）、check_transport.py（本地历史材料未随仓库发布）、check_offline.py（本地历史材料未随仓库发布） |
| 最终 9 输入生产 worker | final_live.py（本地历史材料未随仓库发布）、实际启动时脚本（本地历史材料未随仓库发布） |
| 独立两输入并发 | concurrent_live.py（本地历史材料未随仓库发布）、实际启动时脚本（本地历史材料未随仓库发布） |
| 独立文件与状态审计 | audit_child.py（本地历史材料未随仓库发布）、audit_e2e.py（本地历史材料未随仓库发布） |
| 早期跨版本开发记录 | [TEST-LOG.html](TEST-LOG.html) |

真实运行脚本拒绝覆盖已有尝试。重放应使用新的隔离 profile 和输出目录，并自行配置 API。所有业务快照排除凭据；正式任务的两次批准由独立测试代理读取提议后模拟用户，单独调用产品批准接口，模型未被授予自动审批权。首次脚本错误、初次时机不足、补测中的过时回答与后续展示修正分别保存。

本记录是测试代理执行的软件验收，不替代课程要求的实际学生小组交接；成员仍应亲自运行、检查或修改队友模块并记录体验。

## 后续展示修正

[实时任务卡对照图](live-status-ui/screenshots/24-live-status-contrast.png) 保留原回答的旧 `running`，下方卡片通过真实当前状态显示“本轮完成”和查询时间。此改动只有前端展示，没有再调用模型、改变后端验收版本或重写旧答复；口头答复较慢与过时的限制仍保留。[截图与包版本](live-status-ui/screenshots.json)、[数据库原答与运行记录未改证明](live-status-ui/state-preservation.json)。
