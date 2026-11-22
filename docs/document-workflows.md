# PDF、CSV 与 Word 文件工作流

在聊天框上传资料，或让助手查找已收取邮件并读取附件。描述要阅读、计算或修改的内容即可。输出保存在“资料与文件”，可预览、另存或加入邮件草稿。无需切换文件处理模式。

## 已实现的工具

| 类型 | 读取 | 写入与检查 |
| --- | --- | --- |
| PDF | pypdf 提取带页码文字；PDFium 渲染原页面，当前 OpenRouter 模型实际接收页面图像。扫描页、图表无需先手动转文字。 | ReportLab 生成基本报告；富文本及图表经 Word 排版后由 LibreOffice 导出。已有 Word 使用 `convert_to_pdf` 直接转换其原始字节，保留实际列宽、图像与分页。 |
| CSV | pandas 与标准 CSV 解析器保留行列和字符串；分页显示、编码/分隔符识别、缺失值与列信息。 | 按明确规则去重、过滤、填空、排序、选列、改名、分组汇总、计算新列、定点更新，另存 UTF-8 BOM CSV；复读输出。数值使用 Decimal。 |
| Word `.docx` | python-docx 提供段落、表格单元格 ID 和原文；LibreOffice 转换、PDFium 渲染页面以查看图片及布局。 | 根据目标 ID 和精确旧文修改，保留未改文字的行内格式及图片、页眉页脚。`format_word` 调整局部排版；可生成含表格和真实条形图的新文档。 |

Agent 内置文件工作流位于 `backend/task_backend/assets/skills/documents.md`，规定“了解结构 → 阅读必要页面 → 计算或修改 → 检查 → 草稿附件”。现有 LangGraph 继续负责工具调度、澄清与恢复；没有额外安装一个全功能办公 Agent。

`view_file` 每次查看 1–3 页，发送真实 PNG 页面给当前配置的模型。页面图像不进入前端执行日志；只保存带文件哈希、页码、模型与问题指纹的阅读结果。它与只读文字的 `read_file` 分开。图片接口失败会明确报告失败，不以文字摘要代替视觉结果。

最终排版用 `view_file(purpose="layout")`，返回逐页 `pass/needs_fix/uncertain`、阻断问题与可选建议。当前文件 ID、SHA 和全部页码都对应，才认定本版本完整检查；旧版本、漏页、格式错误或截断结果不算通过。`validate_file` 另附 `layoutReviewCoverage`，让父助手复用子任务已取得的页面证据；结构验证、已观察与检查通过分别表示。

真实条形图可用 Markdown 的 `chart` JSON 块提供 `type:"bar"`、`labels`、`values`，可选 `title`、`unit`、`max`。使用 ReportLab 绘图并嵌入文档，最多12项非负有限数值，不执行代码。数值仍需来自来源或计算结果。两种交付格式先完成 Word，再直接转换 PDF，避免各写一份导致内容与版式分叉。

生成内容用 MarkdownIt 解析基本标题、表格和行内格式；链接保留显示文字，代码及转义保留字面符号，不加载外部图片或文档关系。Word 与带强调的 PDF 依赖已打包的 LibreOffice，转换失败会明确返回错误。

## 输出检查与邮件

“检查文件”区分三件事：格式/数据能否重新打开、所有页面能否渲染、内容与布局是否经过视觉核对。渲染成功不代表数字正确或版面无误。文件卡可以查看 PDF/Word 页面或 CSV 行列，支持翻页。

生成 PDF/Word 在加入 Agent 邮件草稿前必须通过结构与页面渲染检查；CSV 须通过行列复读。草稿绑定实际生成文件的 ID、哈希和字节数。原文件保留，修改产生有来源的新版本。发送仍使用原有的人工预览与确认流程，模型没有发信接口。

## 范围和限制

- 输入：PDF、DOCX、CSV/XLSX、UTF-8 TXT/Markdown、PNG/JPEG/WebP 和 ZIP；尚无 PPTX、旧 `.doc` 或视频处理。
- 单文件 10 MB；PDF 最多 80 页。CSV 最多 100000 行、100 列、1000000 单元格；搜索索引可以截断，但原表及计算工具保留全部数据。
- CSV 缺失值的处理需有明确规则；均值/除法保留最多 12 位小数，半偶舍入。公式样式文字按文字处理，导出添加保护前缀并提示。
- Word 定点修改支持普通段落及表格单元格，要求完整旧文相符。复杂修订、字段和嵌套表格等目标会拒绝改写，不静默破坏结构。
- PDF 写入支持新报告和原 Word 保真导出；不承诺任意 PDF 原版面精确改字。新文档支持条形图，尚无任意绘图/远程插图能力；Word 原有图片可保留。保真指与应用的 LibreOffice 原生预览一致，不保证所有特殊 Word 特性在不同办公软件中完全相同。
- 视觉效果取决于当前模型、页面清晰度和实际查看范围；没有查看的页面不得声称已检查。扫描 PDF 按页看图，不自动生成可检索的 OCR 文字层；没有默认对所有邮件附件调用模型。
- Word 转换在受控本地进程中使用独立临时配置、禁用宏及外部动态内容，有限时和资源边界；这不是可运行任意用户代码的沙箱。

## 开发、打包与复现

Python 依赖精确锁定在 `backend/requirements.txt`。Mac 成品另外包含 LibreOffice 原生渲染器；复制时保留原有许可证，版本在 `Resources/libreoffice/runtime-manifest.json`。开发者可使用已安装的 LibreOffice，或设置主进程环境变量 `TM_SOFFICE` 指定其可执行文件。该路径不能由模型或网页传入。

```sh
python scripts/install-python-deps.py
TM_LIBREOFFICE_DIR='/Applications/LibreOffice.app' npm run runtime:documents:mac
npm run check
TM_MAC_OUTPUT=release-documents npm run pack:mac
```

本机验收包采用可运行的 LibreOfficeDev 26.8.0.0.alpha0 原生 headless 构建，并已验证迁移后独立运行；正式对外分发前宜换成稳定版 LibreOffice 并重跑同一验收。不是依赖用户的 Codex 运行时路径才能启动。

公开展示使用合成的课程通知和校园职业中心活动报名资料。实际输入、生成文件、截图和可复现结果见[公开验收报告](../handover/final-guide/TEST-REPORT.md)。技术回归数值与本轮演示的模型测试分别记录，不将内部历史案例的成功当作新演示已通过。

复用项目：[python-docx](https://python-docx.readthedocs.io/)、[pypdf](https://pypdf.readthedocs.io/)、[pypdfium2](https://pypdfium2.readthedocs.io/)、[pandas](https://pandas.pydata.org/docs/)、[ReportLab](https://www.reportlab.com/)、[markdown-it-py](https://markdown-it-py.readthedocs.io/)、[LibreOffice](https://www.libreoffice.org/)。本轮没有引入需要额外下载模型权重的完整 Docling 栈；用这些组件完成当前三条链路。

### 按页另存 PDF

在对话里说明页码，例如“把这份 PDF 的第 1、3 页另存为摘要”。`extract_pdf_pages` 使用现有 pypdf 复制真实页面，不提取文字重排；图片、页面旋转、裁切框和所选页面表单随页保留。原文件不覆盖，新文件记录来源哈希和页序，可照常检查、预览、加入草稿。页码从1开始、顺序明确且不能重复，单次最多80页；加密、损坏或越界输入返回错误，不保存半成品。没有新增压缩包格式、OCR服务或通用脚本执行权限。
