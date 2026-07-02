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
PYTHON_APP=""
if [[ "$OSTYPE" == darwin* ]]; then
  GUI_PYTHON_BIN="$(command -v python3 || true)"
  if [ -n "$GUI_PYTHON_BIN" ]; then
    PYTHON_VERSION_DIR="$(dirname "$(dirname "$GUI_PYTHON_BIN")")"
    PYTHON_APP="$PYTHON_VERSION_DIR/Resources/Python.app"
    if [ ! -d "$PYTHON_APP" ]; then
      PYTHON_APP="$(dirname "$(dirname "$(dirname "$GUI_PYTHON_BIN")")")"
    fi
  fi
  if [ ! -d "$PYTHON_APP" ] || [[ "$PYTHON_APP" != *.app ]]; then
    PYTHON_APP=""
  fi
fi

if [ -n "$PYTHON_APP" ]; then
  open -n -a "$PYTHON_APP" --args "$SCRIPT_DIR/mobileperflab.py"
else
  "$PYTHON_BIN" "$SCRIPT_DIR/mobileperflab.py"
fi
