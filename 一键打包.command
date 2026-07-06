#!/bin/zsh
set -u

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
VENV_DIR="$SCRIPT_DIR/.venv"
PYTHON_BIN="$(command -v python3 || true)"

pause_on_error() {
  local exit_code="$1"
  if [ "$exit_code" -ne 0 ]; then
    echo
    echo "打包失败，按回车键关闭窗口..."
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

PYTHON_IN_VENV="$VENV_DIR/bin/python"
PIP_IN_VENV="$VENV_DIR/bin/pip"
APP_VERSION="$("$PYTHON_BIN" - "$SCRIPT_DIR/mobileperflab.py" <<'PY'
import re
import sys
from pathlib import Path

text = Path(sys.argv[1]).read_text(encoding="utf-8")
match = re.search(r'^APP_VERSION\s*=\s*"([^"]+)"', text, re.MULTILINE)
print(match.group(1) if match else "0.0.0")
PY
)"

if [ ! -x "$PYTHON_IN_VENV" ]; then
  echo "虚拟环境损坏，正在重建..."
  rm -rf "$VENV_DIR"
  "$PYTHON_BIN" -m venv "$VENV_DIR" || pause_on_error 1
fi

echo "正在安装打包依赖..."
"$PIP_IN_VENV" install --upgrade pip >/dev/null || pause_on_error 1
"$PIP_IN_VENV" install -r "$SCRIPT_DIR/requirements-build.txt" || pause_on_error 1

echo "正在执行 PyInstaller 打包..."
cd "$SCRIPT_DIR" || pause_on_error 1
"$PYTHON_IN_VENV" -m PyInstaller \
  --noconfirm \
  --clean \
  --windowed \
  --name MobilePerfLab \
  "$SCRIPT_DIR/mobileperflab.py" || pause_on_error 1

PLIST="$SCRIPT_DIR/dist/MobilePerfLab.app/Contents/Info.plist"
if [ -f "$PLIST" ]; then
  /usr/libexec/PlistBuddy -c "Set :CFBundleShortVersionString $APP_VERSION" "$PLIST" >/dev/null 2>&1 || \
    /usr/libexec/PlistBuddy -c "Add :CFBundleShortVersionString string $APP_VERSION" "$PLIST" >/dev/null 2>&1
  /usr/libexec/PlistBuddy -c "Set :CFBundleVersion $APP_VERSION" "$PLIST" >/dev/null 2>&1 || \
    /usr/libexec/PlistBuddy -c "Add :CFBundleVersion string $APP_VERSION" "$PLIST" >/dev/null 2>&1
  /usr/bin/codesign --force --deep --sign - "$SCRIPT_DIR/dist/MobilePerfLab.app" >/dev/null 2>&1 || pause_on_error 1
fi

echo
echo "打包完成：$SCRIPT_DIR/dist/MobilePerfLab.app · version $APP_VERSION"
open "$SCRIPT_DIR/dist" >/dev/null 2>&1
