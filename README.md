# 理化检测平台

局域网内通过浏览器录入、计算、查询，并回写既有 Excel 工作簿。Microsoft Excel 和 WPS 用于查看、核对或下载后的使用；日常平台录入不需要安装客户端。

项目目录：`C:\labwork`。当前完成调研与方案，尚未实现或启动业务服务。

- [当前方案 v2](docs/理化检测平台方案-v2.md)
- [原方案 v1](docs/理化检测平台方案-v1.md)
- [工作簿功能清单](docs/工作簿功能清单.csv)
- [GitHub 调研](docs/GitHub项目调研-2026-10-09.md)

`main` 保存当前方案；原 v1 位于分支 `archive/plan-v1`，独立工作区为 `C:\labwork\.worktrees\plan-v1`。方案版本使用 `plan/v1`、`plan/v2` 标签，尚无应用发布版本。后续功能在各自分支和 worktree 内开发，正式运行使用独立发布目录。

`tmp/`、`.worktrees/`、`releases/`、`runtime/`、虚拟环境、密钥和业务工作簿不进入 Git。开发、测试、正式环境的数据库、配置和 Excel 写入范围必须分别隔离，具体规则见 v2 方案。
