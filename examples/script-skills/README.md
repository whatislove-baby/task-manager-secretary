# 自主选择文字经验或脚本：可复现教学样例

这里全部是虚构客户、邮件和订单，不含真实个人资料。历史完成稿为独立手写 CSV；不是被测脚本自己生成的答案。

## 在应用中手动试用

1. 启动 Docker Desktop（本机开发验收已准备好运行镜像）。打开 Task Manager，新建对话。
2. 上传 `attachments/` 中 `orders-a.csv`、`stock-a.csv`、`completed-a.csv`、`orders-b.csv`、`stock-b.csv`、`completed-b.csv`、`orders-missing-price.csv`、`stock-missing-price.csv`。如果不想手选，可上传 `teaching-inputs.zip`，先请助手解压并读取其中的指导与文件。
3. 把 `teaching.json` 中 `scriptTeaching` 的中文指导发给助手；手工试用时一并说明这是虚构客户，固定邮箱是 `orders@harbor-instruments.example.invalid`（ZIP 内指导已经写明）。它应判断需要 Python，编写代码，比较两组完成稿并验证缺价格时拒绝处理。审核卡会展示具体代码、原文与实际测试结果；通过后点击批准。
4. 把 `memoryTeaching` 发到另一个对话。它应将沟通习惯保存成文字经验，核对后批准。
5. **新建对话**，上传 `orders-new.csv` 与 `stock-new.csv`，说“这是 Harbor 的 1004 新订单，按以前的方法处理，检查结果后起草英文附件回复，不发送”。用户未配置或选取可回复邮件时，应先明确收件人或仅生成待补充的草稿，不能伪造邮件 ID。
6. 独立核对输出：4 行，缺货金额依次 `0.00、5.34、6.30、0.01`，总计 `11.65`；行号、SKU 前导零应保留；两行 `0007` 共享库存 3，分配为 2 和 1。`held-out-oracle.csv` 仅供核对，不要在新单测试前上传给模型。

还可以只说“这单周五前确认，不用长期记住”。这一要求应留在该单上下文，不应新建永久技能。

## 自动验收与真实测试范围

`scripts/verify-script-skills-live.py` 用真实 API 和真实 Docker，离线导入本目录 RFC 邮件样本，保留实际发件地址及附件关系。批准动作由独立测试步骤调用用户 IPC，模型没有批准工具。真实邮箱不读取，邮件不发送。

已有结果见 验收报告（本地历史材料未随仓库发布）；完整参数、模型、工具调用和输出保存在 `handover/script-skills/live-a/`。不要把这次机器自动验收写成同学已完成了人工交接；队友仍应按上面的步骤亲自跑一次。
