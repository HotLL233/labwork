# v0.0.1 Windows 安装修订 r1

适用：安装到“正在安装”结束时提示“初始化失败”，日志定位到 `initdb.exe` 返回 1。

原安装器先将 ProgramData\Labwork 限制为 Administrators、SYSTEM、LocalService。PostgreSQL 的 Windows 初始化程序会重新启动自身并禁用 Administrators SID，因此在管理员安装场景失去目录访问权。原测试仅覆盖非管理员隔离运行，漏掉了这个差异；同时没有把原生子进程 stderr 写入安装日志。

修订内容：初始化和管理员备份期间，仅临时授予当前安装用户 SID 修改权限，结束或报错都恢复之前完整的 DACL；不向 Users/Everyone 授权。原生子进程 stdout/stderr 写入 setup.log，错误页面显示日志路径。

程序版本仍为 0.0.1，数据库结构仍为 schema=1。本修订仅更换 Windows 安装流程；原 v0.0.1 标签和发布包保留，修订源码位于 `fix/windows-initialize`。安装包名为 `Labwork-0.0.1-Windows-x64-Setup-r1.exe`。

退出旧的失败安装页面，运行修订包并选择原安装目录（本机日志显示 `C:\APP\Labwork`）。不删除 `C:\ProgramData\Labwork`；已有配置和数据保留。若发现没有配置但存在数据库目录，程序仍拒绝覆盖，需核查初始化状态，不能通过删除数据库解决。

检查：`python packaging/test_windows_setup.py` 覆盖成功/异常的 ACL 恢复及子进程错误日志。`packaging/verify_admin_setup.py` 在全新隔离目录重现旧权限错误，再检查修订流程、实际 PostgreSQL 连接、12 项目/14 方法和权限恢复；不注册系统服务，不修改已安装数据库。管理员检查报告保存在 `C:\labwork\tmp\admin-r1-check.json`。

已通过：2 项 Windows 安装回归测试、9 项 Django 测试、Python 编译检查、Inno Setup 编译，以及安装包 `/TESTMODE /CURRENTUSER` 解包检查（退出码 0，`control.py`、`acl.ps1` 与修订源码 SHA256 一致）。Windows 回归测试模拟管理员身份判断，但使用真实文件权限操作；不能替代管理员隔离验证。当前管理员隔离验证和正式服务安装尚待执行，不能据此认定本机安装已成功。
