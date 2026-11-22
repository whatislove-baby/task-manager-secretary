# PDF CSV Word 完整工作流验收

这些输入全部为合成课程资料，与真实老师、同学或成绩无关。测试收件人 `training@example.invalid` 不会投递；整个验收只生成邮件草稿。

| 输入 | 检查目的 | 可核对结果 |
| --- | --- | --- |
| `inputs/course-chart-scan.pdf` | 页面只有图片，没有文字层；必须真正看图 | January 18、February 27、March 39；January 到 March 增加 21 |
| `inputs/course-hours.csv` | 前导零 ID、重复行、缺失值、取消记录和确定性聚合 | 按 ID 去重、仅 active、空 hours 填 0 后 5 行；A 合计 10，B 合计 20，总计 30 |
| `inputs/course-progress.docx` | 局部修改，保留其他文字、图像、页眉页脚和表格 | 修改指定分析段落和 Data review 的 Status 单元格，输出新版本 |
| `inputs/corrupt.pdf` | 失败路径 | 明确说明文件无法读取，不编造内容，不创建伪造结果 |

`expected.json` 保存输入 SHA256、精确预期和原件保留条件；`build-fixtures.py` 可用 Python 的 Pillow、ReportLab、python-docx 重建输入。图表原图保留用于核对，实际 Agent 输入只提供 PDF、CSV、DOCX 三个文件，不能从额外图表原图或 expected.json 获取答案。

自然对话提示保存在 `prompt.txt`。完整流程应生成分析 PDF、清洗 CSV、汇总 CSV、修订 Word，检查后将四个文件加入一封可编辑邮件草稿。文件生成成功、内容检查通过、页面视觉检查通过是分别记录的检查结果，不能互相替代。

这批验收检查本地附件处理和真实模型理解，不声称验证了 IMAP 下载。邮件收取传输另有 `handover/inbox-sync` 和 `handover/mail-scenarios-1121` 验收记录。
