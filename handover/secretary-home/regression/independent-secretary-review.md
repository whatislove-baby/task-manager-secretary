# 秘书并行执行：独立只读复核

本次审查没有修改产品源码，没有连接真实邮箱或调用模型 API。证据基于当前源码、14 项现有离线回归和两项独立旧版日志恢复探针。源码哈希见同目录 JSON。

## 已核实

- 排队领取在 `BEGIN IMMEDIATE` 中重新核对 `queued`，取消后的任务不会复活。
- 同一执行按完整分派参数摘要去重，不同工具调用 ID 复用同一子任务；旧版 call-ID 日志无论已写结果，还是子任务已提交但结果尚未写入，都能恢复原子任务。
- `get_child_tasks` 分离当前轮 `answer/answerRunId/currentRunId` 与 `previousAnswer/previousAnswerRunId`。
- 前端分派成功后仅消费确实提交的草稿；处理中改写的新草稿不会被清空。此项为源码核对，未另跑浏览器。
- 两条固定线程各自在所属线程构造、使用、关闭 BackendService 和 SQLite；构造互斥避免初始化竞争。官方图和检查点仍按会话隔离。
- 后台同步阻塞期间秘书能完成独立回答；真实官方图离线回归验证了子任务状态与两会话历史隔离。
- Settings 每次读取文件，AccountManager 的入口和身份检查刷新磁盘配置；没有发现跨线程共享 service/SQLite 连接或长期缓存旧账号的问题。
- PDF 转换使用每次独立 LibreOffice 临时目录/用户 profile；文件锁、SQLite 短事务和秘书刷新锁沿用。

## 发现并已修复

审查发现 `document_render.render_page` 原先没有跨线程互斥。秘书和后台文档都可能进入 PDFium。官方明确即使处理不同文档，也不能同时从多线程调用 PDFium。应以模块级锁覆盖从 PdfDocument 创建到所有 native 对象关闭的完整区间；无需锁住模型 HTTP 和 LibreOffice。root 已补模块级 RLock，复核确认锁覆盖 native 构造至关闭；独立运行 `test_document_threading` 通过（1 个测试，3 路并发含异常清理，native 存活峰值为 1）。未故意进行可能导致 native 崩溃的无锁压力测试。

依据：[pypdfium2 官方线程说明](https://pypdfium2.readthedocs.io/en/stable/python_api.html#incompatibility-with-threading)。

## 验证范围

本次只核对代码与离线执行边界；真实生产 worker / OpenRouter 场景由独立验收代理记录。停止属于合作式控制，同步工具需返回检查点后才响应；这次并行改动没有引入任意强杀线程机制。
