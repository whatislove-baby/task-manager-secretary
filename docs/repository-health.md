# 仓库检查修复 · 2026-11-23

## Excel 预览

文件卡片的“查看表格”现在支持 XLSX 工作表返回的数字、空单元格和布尔值。
`0` 保留为 `0`，布尔值显示 `TRUE` / `FALSE`，空值显示为空，编号 `0007`
保持前导零。公式只显示原文，不计算。CSV 继续要求字符串单元格；对象、数组
和非有限数字不会被转换成看似有效的内容。文件原件、分页和后端结构保持不变。

## 打包依赖

保留 electron-builder 26.18.0，仅通过 npm 的版本范围 override 将受
[GHSA-g3hv-75wg-m249](https://github.com/advisories/GHSA-g3hv-75wg-m249)
影响的 minimatch >=7.0.0 <11.0.1 替换为 11.0.1。旧版 v3/v5 调用者保留原
API，避免整个依赖树强制升级造成打包失败。请用 `npm ci` 安装锁定版本。

回归测试会实际调用打包文件过滤器，检查运行入口、对话导出模块与前端资源
被保留，私人数据和测试源码被排除；Mac 还验证 ASAR 生成和双架构合并规则。

## 自动测试

`Repository regression` 在向 `main` 推送、向 `main` 提交或更新合并请求、
以及手动运行时触发。Mac 使用 `macos-15`，Windows 使用 `windows-2025`。
[GitHub 已停用 macos-14](https://github.blog/changelog/2026-10-01-github-actions-macos-14-runner-image-retirement/)。

两平台分别安装 Node 24、Python 3.12、锁定依赖及 LibreOffice，执行：

```sh
npm audit --audit-level=moderate
npm run typecheck
npm run test:python:ci
npm test
npm run build
```

`test:python:ci` 在 Mac 上只将系统钥匙串的主密钥来源替换为当次进程的临时
随机密钥，实际加密、存储和业务测试仍运行。Windows 使用原生 DPAPI。
普通 `npm run test:python` 与生产凭据代码不变。任何检查失败会使对应任务失败；
该流程只有仓库读取权限，不发布安装包、不部署，也不使用真实邮箱或模型 Key。

Docker 的可选测试、Mac 系统钥匙串授权和签名成品验收仍由各自专用流程负责。
GitHub 自动测试不等于分支保护；是否禁止绕过失败检查合并由仓库管理员设置。

## 本机验证

- 前端 445 项通过、1 项按平台跳过；后端 685 项通过、11 项 Docker 测试跳过。
- 类型检查与构建通过；构建仍有原有前端资源大小提示。
- 独立空目录执行 `npm ci --ignore-scripts` 成功，审计为 0 项漏洞；安装脚本与
  平台运行情况以 GitHub 两平台任务为准。
- actionlint 校验通过；测试入口已验证成功返回 0、故意失败返回 1。
- 工作流运行结果请在仓库 Actions 的 `Repository regression` 中查看。
