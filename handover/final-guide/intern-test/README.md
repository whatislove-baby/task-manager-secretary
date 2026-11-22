# 合成实习示例快照

校园职业中心、周宁、阿乔、邮件、报名数据与说明均为新创作的虚构材料；使用全新独立会话运行真实 API，没有导入旧业务资料。

`final-state/` 只保存业务 SQLite 与登记文件；来源、逐文件哈希和最终对象 ID 在 `snapshot-manifest.json`。没有模型设置、邮箱账号、vault、密钥或缓存。所有结果未发送。

复现时用 `clone-guide-profile.py` 将此目录复制到空临时目录，再用 `TM_TEST_DATA_DIR` 启动交付应用；不要直接改证据快照。模型原始结果和独立核验见[合成实习报告](../../public-demo-intern/README.md)。
