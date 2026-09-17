# 微信读书独立同步

定时任务 `.github/workflows/weread-direct.yml` 每周一 09:17（Asia/Shanghai）运行，可手动选择 `incremental`、`metadata_backfill`、`full`、`dry_run`。它使用 `scripts/weread_sync.py` 和 Python 标准库，不经过 NotionHub 运行程序及额度。

仓库 Actions Secrets 需要 `WEREAD_API_KEY` 和 `NOTION_API_KEY`。Notion integration 必须能访问书架、作者、分类、划线、笔记及日/周/月/年统计数据源。不要把密钥写入仓库或日志。旧 `WEREAD_CONFIG` 只供 NotionHub 生成的 workflow 使用，独立任务不会读取它。

旧 `.github/workflows/weread.yml` 会被 NotionHub 自动覆盖，包括恢复自己的每日定时。因此它在 GitHub 中设为 `disabled_manually`。如需回退到旧服务，应先禁用独立 workflow，再显式启用旧 workflow，避免双写；恢复独立模式时反向操作。

同步用 `BookId`、`bookmarkId`、`reviewId` 和统计日期去重。重复键会跳过并写入运行摘要，不自动删除。划线和想法保留正文；书签 API 只有数量，没有可导出的书签内容。书籍同步会从书架填入作者、分类和封面；阅读时长使用 `/book/getprogress` 的秒数，进度从微信读书的 0–100 换算为 Notion 的 0–1 百分比。`metadata_backfill` 只回填这些书籍字段，不导入笔记、划线或统计；适合修复旧记录。增量模式以笔记本 `sort` 跳过未变化的明细；`full` 可重新扫描并回补有日明细的历史年度。运行失败后按 ID 安全重试，先看 Actions 的失败日志和摘要。

首次执行记录：[2026-09-17 运行](https://github.com/SueBwj/notionhub-runner/actions/runs/35177943631)。后续核验要分别查看书籍、划线、想法及统计计数；绿色状态不代表重复键或未提供的日明细已自动补齐。
