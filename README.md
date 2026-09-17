# NotionHub Runner

这个仓库由 NotionHub 自动创建，用于运行已配置同步服务的 GitHub Actions。

- 新仓库默认创建为 public；已有私有仓库不会被自动公开。
- 敏感信息只写入 Repository Secrets，不会出现在仓库文件中。
- 仅为已启用且配置完整的付费或免费服务生成 workflow。
- 免费开源 workflow 运行时不请求 NotionHub Worker。
- 支持媒体的服务会在同一次 workflow 中先同步数据，再下载上传图片和大文件。
- workflow 由 NotionHub 自动更新，手动修改可能会在下次同步时被覆盖。

## 独立微信读书同步

`.github/workflows/weread-direct.yml` 是不使用 NotionHub 运行程序或额度的独立任务。每周一 09:17（Asia/Shanghai）运行，也可手动选择 `incremental`、`full` 或 dry run。源码在 `scripts/weread_sync.py`，仅用 Python 标准库；同步到已有 Notion 阅读中心，不创建第二套模板。

在仓库 Settings → Secrets and variables → Actions 中设置两个独立密钥：

- `WEREAD_API_KEY`：微信读书 skill 网关 API Key。
- `NOTION_API_KEY`：Notion integration 的 API Key。请把书架、笔记、划线、日/周/月/年统计数据源分享给该 integration。

旧的 `weread.yml` 由 NotionHub 生成，仅保留手动回退入口。若 NotionHub 重新生成它，检查并移除它的 `schedule`，以免重复同步。原有 `WEREAD_CONFIG` 不会被独立脚本读取。

同步以 `BookId`、`bookmarkId`、`reviewId` 和统计日期去重；重复键会跳过并在运行摘要提示，不自动删改旧数据。新划线/笔记保留完整正文于页面正文。增量模式以笔记本 `sort` 游标跳过未变的书；`full` 重扫所有笔记并回补历史年度统计。年度日统计来自微信读书的 `dailyReadTimes`，时长单位为秒；没有日明细时不会从月汇总伪造历史日数据。书签只有数量、没有可导出的内容，因此不导入书签明细。专辑及文章收藏入口计入运行摘要，但目前不会伪装成电子书页面。运行失败会显示明确阶段错误，支持安全重试。
