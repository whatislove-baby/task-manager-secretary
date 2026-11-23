# 组员上手与交接指南

**Windows 用户：** 优先下载仓库 Releases 中的 Windows x64 便携 ZIP，全部解压后运行 `Task Manager.exe`。配置、备份与练习步骤见 [Windows 使用与交接](WINDOWS.md)。

这份指南用于从公开源码启动应用、复测一个完整流程，再接手同学的模块。先完成“不接邮箱”的文件测试，就可以检查 API、Agent、文件与邮件草稿链路。

仓库：[whatislove-baby/task-manager-secretary](https://github.com/whatislove-baby/task-manager-secretary)。这是源码与合成示例，**不含已安装依赖、Mac 成品及其 Python/LibreOffice 运行时、个人邮箱数据或私人业务资料**。每位成员使用自己的配置；不要复制别人的 `data/` 或密钥。

## 1. 获取代码和启动

准备 Git、Node.js **24**、Python **3.12**。以下为 Mac 开发流程，在终端执行：

```sh
git clone https://github.com/whatislove-baby/task-manager-secretary.git
cd task-manager-secretary
node --version
python3.12 --version
python3.12 -m venv .venv
.venv/bin/python -m pip install -r backend/requirements-mac.txt
npm ci
npm run dev
```

`requirements-mac.txt` 已包含基础与 Agent 依赖，无需再单独安装 LangChain 或 Deep Agents。`npm run dev` 会先构建，再启动 Electron；Python 后端使用仓库内的 `.venv`。开发数据保存在本仓库的 `data/`，不会自动继承其他 Mac 成品应用的数据。

如果 Electron 提示安装缺失，可运行仓库提供的 `npm run runtime:install` 后重新启动。遇到依赖安装错误，先记录 Python/Node 版本和原错误，不要随意升级锁定依赖或删除锁文件。

**Word 预览与转 PDF 另需 LibreOffice。** Mac 安装到 `/Applications/LibreOffice.app` 后，程序会自动发现。安装位置不同，可在启动时指定真实可执行文件，例如：

```sh
TM_SOFFICE='/Applications/LibreOffice.app/Contents/MacOS/soffice' npm run dev
```

此环境变量只配置本地渲染器。源码不自带成品包的渲染资源；基础 CSV 测试可以先做，Word/PDF 完整流程须准备好 LibreOffice。开发启动不需要先打包，也不需要 Docker；只有运行导入的 Python Skill 才需要另配 Docker，见[技能执行说明](skill-execution.md)。

### 已有源码如何更新

2026-11-23 的五项增量功能随 `main` 源码提供，操作入口见[更新使用说明](../output/pdf/TaskManager-使用说明-2026-11-23.pdf)。退出旧应用，在仓库根目录先运行 `git status --short`；有自己的修改时先提交或备份，再更新：

```sh
git switch main
git pull --ff-only origin main
npm ci
```

Mac 更新 Python 依赖后启动：

```sh
.venv/bin/python -m pip install -r backend/requirements-mac.txt
npm run dev
```

Windows 使用下节的 `.\.venv\Scripts\python.exe` 与 `backend/requirements.txt` 对应命令。若 Git 提示分支分歧，保留本地提交并请组员检查，不要强制覆盖。开发数据仍在原 `data/`，无需复制别人的配置；旧交接 ZIP 只代表生成时的快照，以仓库最新源码为准。

**找不到 Word 渲染器时：** 安装 LibreOffice，或将 `TM_SOFFICE` 指向这台电脑上的实际可执行文件。仅将程序加入 `PATH` 不保证被应用发现；不能复制另一台电脑的绝对路径。本次复核在指定已有组件后通过了实际 Word 转 PDF 和页面检查。该要求原本就存在，并非新增配套提示引入的限制。源码更新不等于发布新安装包；Windows/Mac 成品需重新构建并单独验收。

### Windows 组员

Windows 成品和源码的启动、配置、打包与原生验收范围见 [Windows 使用与交接](WINDOWS.md)。每个预览发布都附有对应 Windows 构建的测试证据；本页下方的旧 Mac 记录单独保留。若已安装 Python Launcher，可在 PowerShell 中使用以下对应命令：

```powershell
git clone https://github.com/whatislove-baby/task-manager-secretary.git
cd task-manager-secretary
node --version
py -3.12 --version
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r backend/requirements.txt
npm ci
npm run dev
```

Windows ZIP 已附带 Word/PDF 渲染器。从源码运行时，可用 `npm run runtime:documents:win` 准备渲染器，也可安装 LibreOffice；应用会查找默认安装位置，特殊位置可通过 `TM_SOFFICE` 指向实际的 `soffice.com`。不要运行名称带 `:mac` 的打包脚本。将实际成功和失败写入交接记录。

## 2. 配置自己的 API；邮箱可以稍后接

打开“设置 → 模型与 API”，在 AI 服务中填写：

| 字段 | 填写方式 |
| --- | --- |
| 服务地址（HTTPS） | OpenRouter 使用 `https://openrouter.ai/api/v1` |
| 模型名称 | 填账号实际可调用的完整模型 ID；需支持工具调用，页面看图还需支持图像输入 |
| API 密钥 | 在应用密码框填写自己的 Key，再点“保存服务配置” |

保存配置只证明字段已保存。下面用合成文件实际完成一轮，才算连接和工具调用测试。模型处理所需的文字和页面图像会发给配置的 API，消耗该账号额度。不要把 Key 写进 README、截图、Issue 或 Git；本机首次访问系统凭据存储时可能出现系统授权提示。

邮箱从“设置 → 邮箱账号”配置，也可从收件箱打开邮箱设置。IMAP 邮箱按服务商要求开启访问并取得客户端授权码；Microsoft 365 需要适用的应用注册与组织授权。SMTP 发信另配，读取成功不能代替发信验证。参考[图文手册](../handover/final-guide/index.html)、[邮箱配置](mail-setup.md)与[发信流程](mail-compose.md)。旧配置页说明中的部分布局可能不同，以当前侧栏入口为准。

接好邮箱后，在“秘书”说“刷新一下”。应用会实际收取已连接邮箱并分类；未接邮箱时不应编造新邮件。组员若要试收信，可从自己的邮箱给自己发合成内容，再刷新并核对原文。一次自发自收不证明不同真实发件人的隔离能力。最初验收只保存草稿，不必真实发送。

## 3. 最小合成测试：两条正常路径与一条失败路径

从聊天框加号上传以下文件。每条记录用户原话、模型实际回答、文件/草稿和遇到的问题；不要把预期答案文件上传给模型。

### A. CSV 处理：无需邮箱或 LibreOffice

上传 [`course-hours.csv`](../examples/document-workflows/inputs/course-hours.csv)，输入：

> 按 record_id 去重，只保留 active，把空 hours 填成 0，保留 ID 前导零。另存清洗后的 CSV，再按 team 汇总 hours。请实际生成两个文件，保留原件。

打开实际输出核对：清洗后 5 行，ID 为 `001` 到 `005`；A 合计 10、B 合计 20、总计 30。不能只看聊天说“完成”。这条可以检验读文件、计算、写文件与来源保留。

### B. 修改 Word → 转 PDF → 双附件草稿

准备好 LibreOffice；上传 [`course-progress.docx`](../examples/document-workflows/inputs/course-progress.docx)，输入：

> 把完整段落“Analysis is pending.”改为“Analysis is complete. Total eligible hours are 30.”，把 Data review 行的 Status 从 Pending 改为 Reviewed。其他文字、图片、页眉页脚和表格保留，另存 Word，再从这份 Word 转成 PDF。检查两份实际页面，给 training@example.invalid 写一封结果草稿并附上这两个文件。发件账号暂不选，不发送。

核对修改位置、原件未变、其余图片/文字保留、两份页面可读；打开邮件草稿，看附件是否确实是本轮生成的 Word 和 PDF。收件人为合成地址，不会投递。重新打开应用，再从最近对话检查成果是否保留。完整样例与精确预期见[文件工作流示例](../examples/document-workflows/README.md)及 [`expected.json`](../examples/document-workflows/expected.json)。

### C. 损坏文件：应拒绝，不编造

尝试上传 [`corrupt.pdf`](../examples/document-workflows/inputs/corrupt.pdf)。预期在导入或读取阶段明确报无法解析；如果进入对话，询问内容也不能编造摘要或伪造成功文件。保留错误提示，并确认前面已经生成的文件与草稿仍在。这条是有意失败输入，正确拒绝才符合预期。

需要检查日历时，可在秘书明确给出一个未来日期和香港时区，要求“只建立待确认”。先核对草稿，再由接手同学批准，检查日历中的日期。模型提议与实际批准分开记录。

## 4. 跑本地回归；理解既有结果

在仓库根目录执行：

```sh
npm run typecheck
npm run test:python
npm test
npm run build
npm run test:handover
```

前四条也可合并为 `npm run check`。`test:handover` 使用合成输入与受控 HTTP、模型、SMTP 响应，写入 `handover/results.json`；不会用真实邮箱发信，也不能代替组员亲手做上面的交接。

向 `main` 提交合并请求后，GitHub 会自动运行 Mac / Windows 回归测试和依赖审计。
Mac 如需不访问系统钥匙串的自动测试，可运行 `npm run test:python:ci`；其凭据
隔离范围、Excel 预览修复和依赖更新详见[仓库检查修复](repository-health.md)。

| 已有验证范围 | 已记录结果与边界 |
| --- | --- |
| 首次公开源码副本的 Mac 后端回归 | 650 项：**638 通过、12 跳过、0 失败**；在 Mac 普通权限环境复测。与后续 Windows 构建统计不混加。 |
| 首次公开源码副本的 Mac 前端与桌面回归 | 400 项：**399 通过、1 个 Windows 平台项跳过、0 失败**。见[公开验收报告](../handover/final-guide/TEST-REPORT.md)。 |
| 真实 API 秘书主线 | 9 次用户输入＋1 个后台任务；原完整断言 **72/73**，保留失败。不能写“全部通过”。见[逐轮记录](../handover/secretary-home/final-run/TEST-LOG.html)。 |
| 真实并发补测 | 调度并行已证明；秘书答复慢于后台完成，仍复述旧状态，快速准确的口头进度未通过。[独立审计](../handover/secretary-home/concurrency-run/audit-concurrency.md)。当前任务卡读取最新状态，旧答复没有被改写。 |

这些数字是既有运行记录，不是承诺所有电脑复跑相同。记录自己的系统、版本、提交号、跳过原因和实际输出。API 例子中的邮件传输采用合成 IMAP 协议输入，不代表已验证每位组员的真实邮箱。

## 5. 交接记录模板

将模板复制为一份小组记录，由接手人实际填写。至少让一位同学运行、测试或修改另一位同学负责的模块，说明接口和哪一步难以接手。

| 项目 | 本次填写 |
| --- | --- |
| 日期 / 执行者 / 原开发者 |  |
| 系统与 Node / Python 版本 |  |
| 分支与提交号（`git rev-parse HEAD`） |  |
| 接手模块与输入 / 输出接口 |  |
| 使用文件与用户原话 |  |
| 预期结果 |  |
| 实际回答、文件、草稿、日历状态 |  |
| 原件与来源核对 |  |
| 失败输入、错误提示与恢复结果 |  |
| 结论：通过 / 部分通过 / 失败 / 未执行 |  |
| 修改了什么、如何复测 |  |
| 哪些说明有帮助、哪些交接信息缺失 |  |

保留首次失败，修复后另记复测结果。截图遮去个人账号和凭据。AI 代理及自动测试记录不能冒充学生实际交接。提交前，每位成员在根 [README](../README.md) 表格中据实填写姓名、学号和具体贡献，一人一行。详细步骤放在本页，主 README 仍遵守课程的 500 词限制。

## 6. 分支协作与阅读顺序

每个功能使用独立分支，下面的分支名换成自己的简短主题：

```sh
git switch -c feat/member-topic
git status --short
git diff
```

提交前运行与改动相关的检查，逐个暂存本次文件，再提交；不要把依赖、`data/`、密钥或个人邮件加入 Git。完成后，有仓库写权限的同学可执行 `git push -u origin HEAD`，再提 Pull Request。没有写权限时先 Fork 到自己的账号，从个人 Fork 提 PR。说明修改目的、影响接口、执行了哪些测试及仍存在的问题，请另一位成员审阅后合并。

建议阅读顺序：

1. [图文手册](../handover/final-guide/index.html)：先了解实际界面和入口。
2. [单张流程大图](../handover/final-guide/workflow.html)与[架构说明](workflow-complete.md)：定位前端、Deep Agents、LangChain、LangGraph、后台与人工审核。
3. [文档工具](document-workflows.md)、[收信](inbox-sync.md)、[邮件草稿](mail-compose.md)：按负责模块查接口。
4. [Skill 包导入](skill-package-import.md)与[经验记录契约](learning-contract.md)：需要复用流程时再看。

GitHub 上的 HTML 通常显示为源码；克隆后用浏览器打开本地 HTML，即可查看可缩放流程图和逐轮测试记录。
