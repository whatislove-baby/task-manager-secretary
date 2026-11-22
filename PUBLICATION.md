# Public source export

This repository started as a source export of the Mac application, whose runtime implementation was copied byte for byte. It now includes maintained Windows adaptations: relocatable Python, bundled LibreOffice, current-user Task Scheduler integration, and packaged-app acceptance checks. See `docs/WINDOWS.md` and the Windows CI results for the current platform scope. Existing package metadata and attribution are retained. No API credentials, user profile, real mailbox, database, application bundle, dependency installation or original customer workbook is included.

## Export differences

- Source, pinned dependencies, renderer/backend tests, build tools and small synthetic examples are included using an explicit allowlist.
- The customer-specific Skill package and its provenance are excluded. Three private-case builders/acceptance scripts (`build-customer-po-skill.py`, `verify-real-template-skill.py`, `verify-imported-skill-workflow.py`) and `backend/tests/test_imported_skill_harness.py` are excluded. Other live-account and historical project-specific harnesses are outside the allowlist.
- `backend/tests/test_workbook_files.py` retains all generic synthetic tests; only `SOURCE`, `PO`, `BASELINE` and the two optional private-workbook tests were removed.
- The rejected-path fixture in `test_deepagents_backend.py` uses `/Users/example/` instead of a developer username. The tested path-rejection behavior is unchanged.
- `.gitignore` adds local release/database/test-output exclusions. Installed runtimes, `node_modules`, build outputs, Git history and the older platform-specific CI workflow are not copied.
- Public documentation and synthetic demonstration material are exported separately after their final review.

## Reproducibility

Create `.venv` with Python 3.12, install the platform requirements, run `npm ci`, then use the commands in README. Windows build and native acceptance commands are in `docs/WINDOWS.md`; Windows preview releases include their generated test evidence. `npm run test:python` runs the exported backend suite. PDF/Word rendering uses LibreOffice; Docker Skill integration tests require the prepared local runtime and may skip when unavailable. No live-model quality claim follows from these offline tests.

`PUBLICATION-MANIFEST.json` lists the allowlisted source files and their hashes. Export verification is recorded separately from product data.


## 文档导出版

公开文档副本匿名化了本机绝对路径，保留输入、模型原话、结果与文件哈希。HTML内嵌截图拆为相同字节的相对PNG资源，请连同文件夹一起阅读。本机数据库、原始私人案例和大型Mac应用未发布；离线数据库演示入口改为运行说明。历史报告所述原测试范围保留，不能用仓库导出副本替代原本机数据库审计。
