#!/bin/zsh
set -u

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
VENV_DIR="$SCRIPT_DIR/.venv"
PYTHON_BIN="$(command -v python3 || true)"

pause_on_error() {
  local exit_code="$1"
  if [ "$exit_code" -ne 0 ]; then
    echo
    echo "安装失败，按回车键关闭窗口..."
    read
  fi
  exit "$exit_code"
}

if [ -z "$PYTHON_BIN" ]; then
  echo "未找到 python3，请先安装 Python 3。"
  pause_on_error 1
fi

if [ ! -d "$VENV_DIR" ]; then
  echo "正在创建虚拟环境..."
  "$PYTHON_BIN" -m venv "$VENV_DIR" || pause_on_error 1
fi

echo "正在安装 iOS 采集依赖 pymobiledevice3..."
"$VENV_DIR/bin/pip" install --upgrade pip >/dev/null || pause_on_error 1
"$VENV_DIR/bin/pip" install pymobiledevice3 || pause_on_error 1

echo
echo "安装完成。按回车键关闭窗口..."
read

