#!/bin/zsh
set -u

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
if [ -x "$SCRIPT_DIR/.venv/bin/python" ]; then
  PYTHON_BIN="$SCRIPT_DIR/.venv/bin/python"
else
  PYTHON_BIN="$(command -v python3 || true)"
fi

if [ -z "$PYTHON_BIN" ]; then
  echo "未找到 python3，请先安装 Python 3。"
  echo
  echo "按回车键关闭窗口..."
  read
  exit 1
fi

cd "$SCRIPT_DIR" || exit 1
"$PYTHON_BIN" "$SCRIPT_DIR/mobileperflab.py"
