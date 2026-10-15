# labwork

当前 Rust + SQLite 版本为 v0.0.9：在页面浏览服务器目录并选择文件夹，搜索、勾选需要关联的工作簿，跳过无法识别或不需要的文件；原表预览可直接编辑常量，生成修改预览并确认后写入源文件。保留公式、原表行高列宽、冻结窗格及样式，沿用空白月份模板、紧凑页面、全部连续历史行、手机布局和登录保持。

- [Rust版安装、原文件关联与回退](rust-preview/README.md)
- [v0.0.9 更新与验证](docs/版本更新-v0.0.9.md)
- [项目及安装包命名](docs/命名规范.md)

以下保留v0.0.1实现记录，供版本回退与方案参考。

## 理化检测平台 v0.0.1

局域网浏览器录入、计算和查询，后台串行写入 Excel。服务端采用 Django、PostgreSQL、Waitress 和 Caddy；无需独立客户端 App。

- [安装、使用、备份和回退](docs/v0.0.1-安装与维护.md)
- [本版范围与验证记录](docs/v0.0.1-验证记录.md)
- [方案 v2](docs/理化检测平台方案-v2.md)
- [工作簿分析](docs/工作簿功能清单.csv)

本版先实现 D006/Q 共 14 个检测方法的试用流程，12 项目统一登记。其他项目可上传、浏览和下载，方法计算与历史结构化导入待后续扩展。真实方法需核定，Excel/WPS 需副本验收；原始网盘文件没有被开发程序修改。

## 版本隔离

`main` 保留方案基线，应用位于 `release/v0.0.1` 分支与 `.worktrees/v0.0.1`，应用标签 `v0.0.1`。安装后程序在独立 `releases/0.0.1`，数据在 `ProgramData/Labwork`；Docker 使用持久命名卷。历史方案标签不能当成应用回退版本。

## 开发与检查

Python 3.12，依赖固定在 `requirements.lock`：

```powershell
uv venv --python 3.12
uv pip sync requirements.lock
$env:APP_ENV='test'
.venv/Scripts/python.exe manage.py test records.tests
.venv/Scripts/python.exe manage.py makemigrations --check --dry-run
```

开发启动使用独立运行目录，管理员密码从交互输入传入，不写入源码：

```powershell
$env:APP_ENV='dev'
$env:WRITEBACK_ENABLED='true'
.venv/Scripts/python.exe -c "import getpass,os,sys; os.environ['ADMIN_PASSWORD']=getpass.getpass('Initial admin password (12+ chars): '); sys.argv=['runtime.py','initialize']; import runtime; runtime.main()"
.venv/Scripts/python.exe manage.py runserver 127.0.0.1:18811
```

另一个终端使用同一开发环境执行 `python runtime.py worker`；默认开发目录为当前工作区 `runtime/dev`，只能回写其中副本。正式环境不使用 SQLite 或 runserver。

## 构建

Windows：准备官方 PostgreSQL 17.11 Windows 二进制、Caddy 2.11.7、WinSW 2.12.0 和可移动 Python 3.12.13；执行 `packaging/build_windows.py --python <Python目录> --downloads <下载目录>`，再用 Inno Setup 6 `ISCC.exe packaging/labwork.iss`。下载文件名见构建脚本，组件来源和 SHA256 随安装负载记录。

Docker：

```powershell
docker build -t labwork:0.0.1 .
docker build -t labwork-postgres:17.11 -f packaging/docker/Dockerfile.postgres packaging/docker
docker build -t labwork-gateway:0.0.1 -f packaging/docker/Dockerfile.gateway packaging/docker
```

发布文件位于 `C:\labwork\releases\v0.0.1`；运行数据、密钥、虚拟环境和工作簿不纳入 Git 或发布源码。
