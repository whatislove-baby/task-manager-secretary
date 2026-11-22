# 后端架构 · 统一待办树

## 新对话 Agent 入口（2026-11-21）

Mac 主界面现在默认进入 `AgentApp`：用户自然语言 → `window.agent` 白名单 IPC → Python Agent 调度 → LangChain `create_agent` / LangGraph 多工具循环。模型可以查阅已导入邮件、文件与待办，在必要时通过 `ask_user` 澄清，并基于 SQLite 检查点继续原任务。框架调用经过业务适配，不直接获得数据库、shell 或任意目录访问权限。

自然答复、文件副本、任务草稿和邮件草稿回到同一个对话。正式保存待办和 SMTP 发送仍由原人工审核链路控制；模型没有自动发送工具。完整的新旧同图标色流程、上下文与恢复方式、当前能力边界见[对话 Agent 工作流](workflow-agent.md)，依赖与运行环境见[Agent 运行时与打包验收](agent-runtime-build.md)。

下文保留上一版 Mac 业务说明及更早的 TypeScript 设计记录。固定任务提取的请求参数和“模型无工具调用”等历史描述只适用于各自原流程，不应套用于本节新增的 Agent 主入口；已有邮件收取、定时整理、文件工作区与人工审核仍复用。

## 当前 Mac 版运行路径（2026-11-20）

正式 Mac 包使用 React → Electron preload 白名单 → NDJSON Python 子进程 → SQLite。主进程保留窗口、IPC 与打包运行时职责；`backend/task_backend/` 承担业务逻辑。下面 2026-10-30 的 TypeScript 说明保留为历史设计记录，不能用其中的模型参数推断当前 Python 行为。

2026-11-20 已加入独立的定时邮件整理后台：计划／立即整理 → 增量导入 → 规则及 AI 分类 → 复用或生成待办草稿 → 既有人工确认。见[同图标色的最新流程](workflow.md)：灰色为本轮前已有，绿色为本轮新增，橙色为本轮修改。

新增写信链路为 `MailComposePanel` → `window.mailCompose` → `mail-compose:*` → `MailCompose`：模型仅生成主题和正文，界面编辑后由后端保存固定预览，再经独立确认发送。SMTP 使用现有 IMAP 账号的加密凭据；发送记录与普通待办草稿分开保存。协议参数、结果语义和测试边界见 [写信与发送](mail-compose.md)。

当前 Python 任务提取请求使用已保存的兼容 `/chat/completions` 地址，45 秒请求超时、200000 字节响应上限，提示要求 JSON 并进行结构校验，没有 TypeScript 历史实现的 DeepSeek 专用参数或 JSON mode 降级。追问重新加载邮件原文，来源上限 24000 个 UTF-16 字符，超出明确报错。详见 [当前邮件功能](mail-setup.md)。

## 历史 TypeScript 设计记录

更新时间：2026-10-30。状态：统一待办后端已实现，用户确定的新前端已接入。已确认需求以 product.md 为准，实际验证以 verification.md 为准。以下包含已采用的工程默认值。

## 最小分层

保留 Electron + TypeScript + SQLite。渲染进程经 preload 白名单调用业务接口；主进程负责参数验证、事务、文件和模型请求。领域层独立处理树、完成联动和截止规则，不依赖 Electron。页面不能直接访问 SQL、文件系统或密钥；模型无工具调用或直接写库权限。

业务命令：创建、编辑、移动、设置完成、回收子树、恢复批次、查询、生成草稿、确认草稿、导出、更新配置。业务写操作串行执行；一个命令使用一份配置快照，一个事务内校验、修改、联动、提交。失败全部回滚，提交后才更新界面。编辑带 revision，陈旧提交返回冲突并保留输入。

## 最小领域模型

| 数据 | 字段与含义 |
|---|---|
| 待办身份/内容 | id、parent_id（根为空）、title（去空白后非空）、notes、completed |
| 截止模式 | due_mode：none / inherit / value，明确区分主动不设截止与继承 |
| 截止值 | value 时 due_kind 为 date 或 minute；date 存 YYYY-MM-DD；minute 存规范 UTC 时刻及录入时区，精确到分钟；不适用字段为空 |
| 回收状态 | deleted_at、trash_batch_id；保留原 parent_id |
| 元信息 | created_at、updated_at、revision，真实时间不由 ID 推测 |
| 回收批次 | 批次 ID、删除根 ID、原父 ID、删除时间 |
| 配置 | 完成联动开关存SQLite metadata并随备份恢复；云端配置独立JSON，密钥仅保存系统加密后的密文，不导出 |

子待办与父待办同结构。以 parent_id 邻接表保存树，外键和 CHECK 约束基础字段，事务内验证环路及有效/回收一致性。索引覆盖父项/有效状态、回收批次；暂不引入闭包表。遍历用迭代队列/栈，不设置产品层级上限、不依赖调用栈递归。批量读取受影响节点，避免逐节点查询；资源不足时整体拒绝，不能截断后部分保存。

## 截止规则

- 编辑字段缺席表示不修改；none 表示显式清空；inherit 表示重新继承。根默认 none，不接受 inherit。
- 显示与生效分别返回：未手动填写截止的子项不显示日期，但仍受动态继承值约束。
- 动态继承：沿父链遇第一个显式值或 none 即停止，不把继承结果覆写为显式值。父截止修改后重算继承分支，遇显式覆盖即停止传播；显式空值也阻断传播。
- 移动后继承值按新父项重算，显式设置保持；移为根时 inherit 转 none，提示截止变化。
- 日期仅代表日历日，不补 23:59 或 UTC 午夜；本机日历跨过该日才算逾期。分钟值表示实际时刻，换时区仍保留同一时刻。非法日期拒绝。后端只接收已明确的实际时刻和时区；本地输入遇夏令时歧义的选择交互待前端阶段验证。
- 双方均为分钟精度时比较实际时刻；有一方仅日期时按本机日历日比较，同日不判定更晚；任一方无截止则不比较。
- 创建、编辑、移动、恢复、父截止变化复用上述比较；晚于父项只提醒、不阻止，批量影响汇总提示。

## 完成、移动与回收

- 显式完成父项：向下开关开启则完成所有有效后代，再按向上开关检查祖先。向上自动完成只改变祖先，不额外触发向下操作。
- 向上判断只计有效直接子项，且必须非空；所有直接子项完成可继续向上递推。关闭联动允许父子完成状态不同。
- 已确认取消父项完成仅取消目标，保留后代状态。祖先联动仍为默认草案：向上开关开启时重开已完成祖先，不改后代和兄弟项。本次取消命令不立即按「子项全完成」把目标勾回，后续相关状态/结构事件才重算。
- 新增、删除、移动和恢复检查受影响父链。新增/恢复未完成子项按重开祖先规则处理；非空子项全完成触发向上完成；空子集不自动完成。修改配置只影响后续操作，不追溯扫描存量。
- 移动必须在事务内验证新父存在且有效，沿新父祖先链不能遇见移动节点；自身/后代作为父项一律拒绝。禁止跨回收状态移动，串行写入避免并发移动成环。
- 删除按当前有效子树形成一个批次，父和全部有效后代一起软删除；早已回收的分支留在原批次，不混入。
- 默认整批恢复：原父有效则回原位置；原父不存在或仍在回收站则恢复为根，提示位置与继承截止变化，不自动恢复其他批次。
- 恢复保留自身完成状态，恢复未完成子项时按配置重开外部祖先。重复恢复幂等。有效节点不得挂在回收节点下；回收节点保留原父引用。首版暂不自动清空或永久删除。

## 本地存储与旧原型

打包应用以实际可执行文件目录下 data 为默认路径，不使用当前工作目录；开发模式明确使用项目根/data。当前布局：tasks.sqlite、settings.json（包含加密后的密钥）、backups/；桌面适配器另指定 browser-cache/ 和 logs/。密钥使用操作系统加密能力保存为密文。

启动验证目标目录可创建/写入且 SQLite 可正常事务读写。保护目录不可写时展示路径、原因和处理办法，不得静默回退 C 盘 userData、另建空库或替代路径。数据库伴随文件、配置和备份也放 data；发布验收核查 Electron 缓存和日志，不能仅改数据库路径就声称无 C 盘写入。

数据库有版本号；首版打开旧v1原型库会明确拒绝，原库保持不变。尚未提供旧原型的数据迁移器。旧 task/event、priority 模型已废弃，不能默默把旧日程开始时间当成截止时间，也不能删库重建。旧 userData 库只能明确导入，不自动覆盖新 data。版本化 JSON 导出包含层级、截止模式/精度、完成和回收语义，不包含密钥。

## AI 保存边界

用户主动发起请求才发送文字，显示发送范围与目标服务。.eml 内容作为不可信文本，不执行 HTML 或附件。模型输出经结构校验后成为内存草稿，用户可修改；缺失/歧义日期保留为空，不能重新引入优先级或日程类型。

确认时重新校验标题、截止、树引用及环路，再一次事务保存全部节点；临时 ID 统一映射为真实 ID。确认令牌防重复写入。取消、超时、解析或任一条校验失败均不写库。错误与日志不回显密钥或完整请求。

## 后端测试矩阵（执行结果见 verification.md）

| 范围 | 关键用例 |
|---|---|
| 字段与精度 | 空白标题拒绝；无旧类型/优先级；无截止/日期/分钟往返；非法日期、时区和夏令时边界 |
| 继承与提醒 | 多层继承、覆盖、显式清空、恢复继承；父修改传播边界；混合精度同日/跨日，提醒不阻止保存 |
| 完成与撤销 | 两开关四种组合；全部后代、直接子项递推、空子集；取消父/子；兄弟保留；开关不追溯 |
| 树与性能 | 自身/后代父项拒绝；深树迭代不溢栈；移动截止重算；陈旧编辑/并发移动；超资源完整回滚 |
| 回收与恢复 | 同批完整子树、旧回收批次隔离；原父有效/回收后恢复；继承/完成联动；重复恢复幂等 |
| 持久化与迁移 | 非 C 盘 data；重启不丢；工作目录变化；不可写无静默回退；迁移失败保留原库；无损导出无密钥，备份仅允许导入全新空库 |
| 原子性与 AI | 批量中断全部回滚；草稿一条非法不保存；重复确认；取消/超时/非法 JSON；修改后保存 |
| 打包实测 | Windows 包实际数据/缓存路径；真实云端测试单独记录；深树/大树测试记录规模、硬件与耗时 |

单元测试不等于打包版、真实 API 或产品验收通过；现有原型测试不能替代新模型测试。

## 当前接口与构建边界

- `src/shared/todo.ts` 定义统一模型、日期精度与联动配置；`src/domain/tree.ts` 处理纯规则。
- `TaskRepository` 将单次命令及联动放入同一SQLite事务；`createBackendService` 对输入验证、串行写入，并返回结构化成功/错误。
- `src/shared/contracts.ts` 的 `DesktopAPI` 是前端未来接入点；`main.ts`/`preload.ts` 提供单实例窗口入口、数据目录配置和白名单接口，加载新的 dist-renderer。
- `npm run check` 覆盖前后端类型、领域/仓储/服务/组件测试及完整构建；`dev` 启动窗口，`pack` 目录打包，`dist` ZIP打包。

## 日期投影和排序（schema v3）

`src/shared/planner.ts` 为浏览器纯投影模块，迭代计算有效截止与根定位。dailyRoots(day)仅该日或无截止根；weekEntries(day)仅该格的显式截止根和异日覆盖子项。继承截止不在周格重复展示。使用本机日历日，跨DST按日历推进。

SQLite `day_orders(day,value)` 按日期保存完整根列表的ID顺序，默认无排序时使用创建顺序；新项目追加。后端拒绝含子项、重复或过时成员集合的排序，不改变待办内容或revision。根移动成子时清理其历史显示排序。版本2完整结构和数据校验后事务升级3，失败回滚；备份导出3带排序，导入仍兼容版本2。

详情的字段与完成状态通过 editTodo 单一事务提交，避免内容保存但完成状态失败的部分成功。IPC增加getDayOrder/setDayOrder/editTodo白名单方法。

CSP限制远程连接与脚本，只有React动态树缩进需要内联样式；渲染进程仍不可直接联网，云端请求由主进程发出。子树折叠与深层滚动不限制存储层级。工具面板通过对话框焦点管理与未保存提醒保护输入。


## 双窗口与乌鸦悬浮窗

Electron进程持有单个TaskRepository和service；主窗口与`?widget=1`透明悬浮窗通过同一preload调用DesktopAPI。IPC校验窗口对象、主frame与精确本地URL；成功变更广播`desktop:changed`，读请求不广播。独立WidgetAPI负责开启、关闭、展开、重开主界面与退出，订阅可解绑。

主窗口关闭按应用偏好隐藏到托盘或请求退出整个应用；关闭悬浮窗不退出托盘进程。仅will-quit释放服务，避免未保存修改取消退出后数据服务失效。悬浮窗由原生alwaysOnTop窗口承载，尺寸按显示器工作区约束。renderer用原始revision提交编辑，跨窗刷新不覆写草稿。

窗口显示以ready-to-show为主，页面加载完成（did-finish-load）与3秒兜底（PAGE_SHOW_FALLBACK_MS）为辅，避免首次绘制缺失时窗口永不出现；关闭语义区分“已请求关闭”“托盘隐藏”“已销毁”三态，加载失败只提示一次。窗口创建、页面事件、失败与GPU合成状态写入data/logs/window.log（1MB轮换，不含业务内容），写盘失败静默跳过。主进程入口按“无父模块或require.main指向本文件”判定，兼容Electron以default_app加载`electron .`的开发启动方式。


## Canvas 订阅接入

Calendar Feed由Node侧HTTPS拉取，ical.js解析，20秒请求超时、5MB响应、正常事项和异常记录合计最多5000项，重复展开后的说明总量不超过40MiB；禁止自动重定向。iCalendar UID+实例标识作为键，日期保留日期精度，分钟按时区解析；支持有限范围重复事件及例外。单条事项按UID组隔离，验证失败跳过并报告固定原因，其他有效组可继续；整个ICS语法损坏、下载失败、超过整体资源上限时不保存。来源说明按纯文本解析，原文链接只保留无用户凭据的HTTP(S)地址。

独立CanvasFeedStore使用safeStorage加密URL，renderer只获得configured/host；稳定随机sourceId在同域链接轮换时保持。网络请求在写队列/数据库事务外进行；提交前重新核对URL/sourceId，避免换源后的迟响应写入。并发同步共享进行中的请求。

数据库v5在canvas_items映射内独立保存description/url；由v2/v3/v4事务迁移，旧备注一律保留，旧映射来源字段先为空，下次同步补齐。事项与来源在同一事务提交。来源拥有标题、截止、说明和原文链接，本地拥有个人备注、完成、层级；回收项跳过，缺项不删。仅来源变化不递增Todo revision，但报告updated并广播刷新。备份v5包含映射与来源正文/事项链接，排除订阅URL，兼容旧v2/v3/v4备份；导入导出均为64MB上限，首次绑定唯一备份来源时可恢复来源ID。

DesktopAPI增加getCanvasSource(todoId)/openCanvasOriginal(todoId)。读取只返回来源纯文本和事项链接，不返回feed订阅；打开原文时主进程按ID读取存储的来源、重新校验HTTP(S)及无userinfo，再调用系统浏览器，renderer不能传入任意外链。来源HTML不执行、不使用dangerouslySetInnerHTML。同步成功经既有事件广播两窗口，阅读组件重新读取来源。


Canvas 同步仅提交截止日期为本机今天及之后的事项；分钟按本机时区换算日历日，不以当前时分排除当天事项。在入库前重新确定今天，避免网络请求跨午夜后导入昨日事项。已保存历史项不自动删除；同步结果expired计数并包含于skipped，不列为异常。此规则不修改底层通用ICS解析范围。


## Outlook 邮件接入

新增独立MailAPI经mail:* IPC提供登录、文件夹、筛选、摘要、正文与单封提取。与DesktopAPI使用同样的窗口/mainFrame/本地URL校验，退出时注销全部handler。OutlookAuth采用官方MSAL PublicClientApplication organizations + Mail.Read，浏览器PKCE及本机回调；180秒取消等待，getToken静默续期，getState不发网络。state和MSALcache整体系统加密保存，epoch保护断开/换client后的迟授权；账户筛选按homeAccountId隔离。

OutlookGraph固定Graph GET，只允许同源v1.0/me分页，20000ms单请求超时、列表5MiB响应限制、单封正文32MiB响应限制、最多100分页/500总请求/500文件夹；整封超过2MB进入大邮件流程，文字正文单次最多10MB，明确标记部分正文，附件只取名称、类型和大小。摘要和正文分开，纯文本显示，ImmutableId头减少移文件夹后ID变化。支持组合日期/文件夹/子文件夹/发件人/主题/未读条件；读取最多200匹配项并标limited，不宣称跨文件夹全局最新。

createMailService在每次跨网络操作前后核对账户；读取/查看不触发AI，extract仅单封、最多20000字符，复用extractDrafts校验。生成草稿附可靠邮件来源；确认令牌为账户ID+ImmutableMessageID哈希，复用draft_confirmations与原有saveDrafts事务，已处理邮件不再提取。同邮件并发生成共享请求；草稿取消不产生处理标记。现有JSON备份不含draft_confirmations，因此邮件处理标记暂不随JSON恢复。openMessage后台验证Outlook官方域名后才打开系统浏览器。

真实学校登录取决于应用注册和学校授权策略。源码无公用clientID，不能借用其他产品clientID；配置入口及docs/outlook-setup.md提供一次性注册步骤。


## 按绑定方式路由与通用IMAP

MailState增加可选method（旧无字段按microsoft兼容）和仅公开的IMAP配置；MailAPI增加selectMethod/configureImap。MailConnections按imap/microsoft路由，未配置默认imap，旧Microsoft注册或账户继续沿用，data/mail-connection.json仅存mode。切换方式保留两侧配置并使用epoch拒绝迟响应。

ImapAuth将服务器、用户名、口令、连接状态及账户筛选整体加密存data/imap-auth.bin，只有实际连接测试成功后account非空。口令空仅同连接身份允许复用，更换身份不得带旧凭据。断开清凭据保留公开配置／筛选；epoch保护迟测试不能恢复断开状态。

ImapMailbox使用ImapFlow和mailparser，按操作建立连接并finally释放。必须TLS或强制STARTTLS，保持证书校验，禁日志及自动IDLE，只读打开邮箱，不修改邮件状态。文件夹按服务器delimiter构建层级，支持不可选择的父节点和多选并集。消息以文件夹+UIDVALIDITY+UID标识，读取前验证UIDVALIDITY，使用INTERNALDATE表示收件时间；IMAP搜索日期放宽边界后按本机实际时刻过滤。摘要不取全文；正文按实际接收位置分段读取，服务器报告大小只写入诊断，不再与实际字节数一票否决。整封超过2MB时先保存摘要，再单独读取文字正文（单次上限10MB，用户继续时最多25MB），附件只保留元数据；解析异常与传输中断分别记录，异常时从头复读并比对哈希。不执行HTML、不自动加载远程图片或内嵌图片。没有通用IMAP网页地址，因此不伪造webUrl。

两侧复用邮件服务和AI草稿流程，邮件身份哈希与确认事务防重；服务在调用模型前再次核对连接身份与用户展示的AI目标。服务器范围由用户明确填写，无品牌选择／域名路由。库的clientInfo仅为协议识别信息，与微软注册clientID不同，不在产品流程暴露。


## 多邮箱本地工作台与应用偏好（当前实现）

window.mailWorkspace通过mail-workspace:* IPC提供多绑定、本地导入、分析历史和确认保存，与其他IPC使用相同窗口、主frame与精确URL校验。旧MailAPI保留兼容，当前界面使用MailWorkspacePanel。MailAccountManager为每个绑定创建独立Auth/Reader，注册表mail-accounts.json只存UUID与方式；旧根目录凭据通过legacy标识引用，不移动也不暴露明文。

MailWorkspaceStore使用独立mail.sqlite：local_mail以bindingId+accountIdentity+remoteId哈希去重；mail_analyses保存用户要求、邮件集合、模型目标、草稿、说明与保存状态。列表不传正文，按页读取，getLocal离线读取全文。默认增量导入跳过已缓存正文；主动重导比对内容指纹，变化清除最新分析标记，独立saved_at保留已有待办。旧legacy确认收据在导入时保留。正文为明文SQLite，不保存附件，不加入待办JSON备份。

AI只读本地缓存。用户主动选择邮件与要求后，核对展示的服务／模型，分段调用现有兼容接口。邮件是不可信内容，用户指令单独写入系统要求；每段返回校验，全部成功且最终配置和正文指纹未变才提交分析事务。分析最多200封、24万字符、100项草稿；长文分段无完整跨段推理。确认使用mail-analysis:<UUID>稳定令牌写TaskRepository，然后标分析已保存；若两库之间失败，重试由确认收据恢复，避免重复待办。现有JSON不包含邮件分析库，完整data备份可保留它。

window.preferences独立提供get/save，application-preferences.json保存launchAtLogin/widgetOnLaunch/closeBehavior。只有用户主动更改自启动才调用Windows注册；开发运行不注册。主窗关闭默认hide保留编辑状态，quit模式请求app.quit，退出取消重置quitting；托盘明确退出始终请求结束全应用。服务只在will-quit释放。默认开机启动关闭、悬浮窗启动关闭、主窗关闭到托盘。


## 后台任务、会话与独立导入收据（2026-10-31，当前界面入口）

当前邮箱与AI整理通过window.mailOperations调用主进程MailOperations，旧同步API保留兼容。mail-operations:*复用窗口对象、mainFrame及精确本地URL校验。创建任务立即返回，串行后台队列与renderer存活分离；queued/running/pausing/paused/completed/failed状态及检查点保存在mail-operations.sqlite。退出中止请求并保存暂停状态，重启需主动继续，所有未完成任务均保留入口；列表只传公开进度，不暴露内部计划或凭据。

导入检查点保存摘要定位及完成游标，不保留正文preview副本；正文成功入库才建立收据。Graph／IMAP支持cancelPending，后台同时对等待做abort race，原生网络不响应中止也不阻塞暂停。Graph还用epoch阻止暂停前的授权等待结束后再发邮件请求；不存在强制终止用户进程的处理。

mail.sqlite增加mail_receipts、mail_known_senders、mail_presets、mail_local_folders及folder_id。旧local_mail事务回填收据与发件人。删除正文前标记收据deleted，真正删除local_mail行；仅新导入及强制重导均跳过deleted收据。以binding/account/remote稳定ID哈希识别，避免弱标题匹配导致误合并。secure_delete用于数据库删除行；原有备份及独立AI产出不属于物理擦除范围。显示状态仅analyzed_at决定未分析／已分析，saved_at仅内部兼容收据，不作为第三状态。

AI初始计划将邮件切成约4500字符片段、请求约7000字符以内；计划只有ID／范围／原文指纹，回复作为完成检查点。上下文或输出长度失败自动二分，限流及网络有限重试；JSON模式不受支持可降级，格式和树校验始终执行。DeepSeek官方域名使用非思考模式，单次等待120秒、输出6000token、响应1MiB上限。每任务1000封／12MiB正文／2000段，单批100草稿。所有分段成功且原文指纹未变，原子saveAnalyses；每批稳定分析ID确保提交重试不冲突。草稿确认仍复用原有确认令牌机制。

会话保存用户输入、AI答复、草稿引用及时间；新文字对话没有邮件来源也是合法分析。每轮回复先持久化chatResult，再提交分析和会话，跨步骤失败后不重复调用模型。长历史生成摘要并保存覆盖位置，原始turns不删；后续使用摘要与未覆盖历史，属于单会话上下文而非模型训练。删除会话删除对应任务／日志，独立分析产出与已保存待办保留。当前任务完成用底角入口与可用的原生Notification提示，系统通知故障不会改变任务成功状态。

## 悬浮窗真实图案命中

widget-hit-test读取本地PNG alpha，反算缩放／二维变换、object-fit与飞行动画帧；SVG叶子按填充和描边命中。纸签、菜单、纸笺及Canvas阅读层是实心交互面。通过仅widget可调用的setPointerPassthrough映射原生setIgnoreMouseEvents(ignore,{forward:true})，空白透传、移动转发以重新进入图案，拖动期间锁定交互。窗口固定尺寸规则沿用，关闭按钮按图案hover显现。原生API说明见[Electron BrowserWindow](https://www.electronjs.org/docs/latest/api/browser-window#winsetignoremouseeventsignore-options)；兼容请求参数见[DeepSeek Chat Completions](https://api-docs.deepseek.com/api/create-chat-completion/)。
