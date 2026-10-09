@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo 平台地址：http://127.0.0.1:18805
echo 请保留此窗口，停止时按 Ctrl+C。
"%~dp0labwork-preview.exe" --home "%~dp0data"
if errorlevel 1 (
  echo 服务器未启动或异常退出。若端口被占用，请先停止已有的 18805 验证程序。
  echo 请勿把其他数据目录正在运行的页面误认为本次安装的数据。
  pause
)
