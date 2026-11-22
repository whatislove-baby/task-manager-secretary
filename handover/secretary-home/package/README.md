# 秘书版 Mac 应用打包验收

应用：`release-secretary-home/mac-arm64/Task Manager.app`，macOS arm64。旧 `release-document-repair` 应用保留。本次复用既有 Python 与 LibreOffice 运行时，未打开生产资料目录或联系真实邮箱。

当前构建使用独立的秘书与后台任务执行通道；文档渲染以 PDFium 全局互斥避免线程冲突。加入固定秘书主页、收件整理卡、复杂任务独立子对话、父子跳转与任务状态。只在输入确实成功分派后清除父会话草稿；失败保留用户文字和附件。任务卡直接查询当前运行状态并显示查询时间，同一任务共用轮询；原回答文字不改写。

## 已完成

- `npm run build` 与 Mac 本地签名打包通过。
- 174 份冻结源码无变化；71 份后端与资源文件和包内字节一致。见 `prebuild-source-hashes.json`、`build-integrity.json`。
- `verify-python-package-mac.cjs` 通过：真实 IPC、前端字节、依赖、搬迁后 Python / LibreOffice、官方 Agent 工具流与 checkpoint、SQLite、后台失败回报与严格签名；新增秘书稳定 ID、禁止删除、未知简报和非法父任务拒绝检查。未使用模型网络。
- 包内工具转换 2 页富格式合成 Word，与原件独立导出 PDF 每页像素一致；原件不变，草稿实际包含 Word / PDF 双附件，SMTP 发送 0。见 `conversion-verification.json`。
- 转换后严格签名验证通过，见 `signature.log`（退出 0，正常为空）；24 张真实 UI 截图完成后 `signature-after-ui.log` 再次通过。

最终真实 API 运行记录的 71 份源码与包内文件一一相同，见 `live-build-comparison.json`；这只证明版本对应，不替代业务结果审计。

真实页面验收及截图在 `../../final-guide/`，真实 API 场景由本目录上一层分别记录，不能用离线包验证代替模型业务验收。每份截图清单包含实际 ASAR 哈希。

## 复现命令

```sh
npm run build
TM_MAC_OUTPUT=release-secretary-home node scripts/package-app-mac.cjs dir
TM_MAC_OUTPUT=release-secretary-home TM_PACKAGE_EVIDENCE_OUTPUT=handover/secretary-home/package node scripts/verify-python-package-mac.cjs
'release-secretary-home/mac-arm64/Task Manager.app/Contents/Resources/python/bin/python3' -B -I handover/secretary-home/package/verify-conversion.py
codesign --verify --deep --strict 'release-secretary-home/mac-arm64/Task Manager.app'
```

ASAR SHA-256：`7ede36141c538e1dba24694a70c34e20dd6f1c550015b3480cfae8d792d5da02`。后端资源位于 ASAR 外，以另外 71 份文件哈希核验。本地签名为 ad-hoc，未申请苹果公证。
