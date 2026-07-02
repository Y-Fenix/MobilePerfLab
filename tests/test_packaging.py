import re
import unittest
from pathlib import Path

from mobileperflab import APP_VERSION


class PackagingScriptTest(unittest.TestCase):
    def test_startup_command_launches_mobileperflab_script(self) -> None:
        text = Path("一键启动.command").read_text(encoding="utf-8")

        self.assertIn('SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"', text)
        self.assertIn('GUI_PYTHON_BIN="$(command -v python3 || true)"', text)
        self.assertIn('Resources/Python.app', text)
        self.assertIn('PYTHON_APP_EXEC="$PYTHON_APP/Contents/MacOS/Python"', text)
        self.assertIn('"$PYTHON_APP_EXEC" "$SCRIPT_DIR/mobileperflab.py"', text)
        self.assertNotIn('open -n -a "$PYTHON_APP" --args "$SCRIPT_DIR/mobileperflab.py"', text)
        self.assertIn('"$PYTHON_BIN" "$SCRIPT_DIR/mobileperflab.py"', text)

    def test_packaging_script_syncs_app_bundle_version_from_source(self) -> None:
        script = Path("一键打包.command").read_text(encoding="utf-8")

        self.assertIn("APP_VERSION", script)
        self.assertIn("CFBundleShortVersionString", script)
        self.assertIn("CFBundleVersion", script)
        self.assertIn('APP_VERSION="$("$PYTHON_BIN" - "$SCRIPT_DIR/mobileperflab.py"', script)
        self.assertIn("Path(sys.argv[1]).read_text", script)
        self.assertIn(APP_VERSION, Path("mobileperflab.py").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
