#!/bin/zsh
set -u

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PYMOBILE="$SCRIPT_DIR/.venv/bin/pymobiledevice3"

if [ ! -x "$PYMOBILE" ]; then
  echo "未找到 pymobiledevice3，请先双击“安装iOS依赖.command”。"
  echo
  echo "按回车键关闭窗口..."
  read
  exit 1
fi

echo "将启动 iOS DVT tunnel 服务。"
echo "系统会要求输入本机登录密码；窗口保持打开期间，MobilePerfLab 可读取 iOS CPU/内存指标。"
echo
sudo "$PYMOBILE" remote tunneld --protocol tcp

