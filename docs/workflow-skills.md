# 已开发技能导入与邮件工作流

本轮范围调整：复用已开发、可核对的业务程序；不再把“AI 根据自然语言重新开发该程序”作为交付要求。灰色是已有功能，蓝色为新增导入，橙色为本轮修复。

```mermaid
flowchart TD
  U[用户上传现成 Skill ZIP及用途]:::new --> I[导入原字节与资源 / 输入输出契约]:::new
  I --> T[隔离运行包内验收样例 / 核对正确结果]:::new
  T -->|通过| R[用户审核版本、用途、结果]:::old
  T -->|未通过| E[说明缺口 / 由开发者修正技能包]:::new
  R --> S[版本化技能库 / 可停用和替换]:::old
  M[新邮件、原附件、对话请求]:::old --> A[Deep Agents 检索、读取、澄清、调用工具]:::old
  S --> A
  A --> K[按对象选择并读取当前技能]:::old
  K --> X[run_skill 原附件在隔离环境执行]:::changed
  X --> F[受管产物 / 核对内容、版本和实际文件]:::changed
  F --> D[回复草稿加真实附件 / 核对完整交付]:::changed
  D --> SEND[用户预览后确认发送]:::old
  A --> DOC[文档子任务 / PDF Word CSV 读取生成修改]:::changed
  DOC --> F
  A --> CAL[待办创建或改期提议]:::old
  CAL --> REVIEW[用户确认同一 Todo 的变更]:::old
  REVIEW --> STATE[读回真实保存进度与当前状态]:::changed
  STATE --> A
  A --> MEM[沟通经验和报告格式 / Markdown审核后复用]:::old
  MEM --> S
  classDef old fill:#f1f1f1,stroke:#a3a3a3,color:#333;
  classDef new fill:#eaf1ff,stroke:#4478d4,color:#143264;
  classDef changed fill:#fff0da,stroke:#d18b2e,color:#663d0a;
```

技能说明使用 Deep Agents 的渐进加载；执行复用现有 Docker 运行器。首次包验证和用户审核与后续业务调用分开，正常新输入不重新要求样例。技能不会自行发信或改变日历状态。

普通文档处理不需要用户开发业务程序。未找到适用的客户技能时说明缺口，不能为追求完成而生成未经验证的替代转换脚本。

[导入格式、使用方法和第一个实例](imported-skills.md)。此前自动编写脚本的验证记录保留在历史 handover 目录，不代表本轮目标或当前通过结果。
