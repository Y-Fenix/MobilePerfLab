import unittest

from mobileperflab import build_environment_checks, format_environment_checks


class EnvironmentCheckTest(unittest.TestCase):
    def test_marks_android_adb_as_required_for_real_android_sampling(self) -> None:
        checks = build_environment_checks(
            {
                "python": "/usr/bin/python3",
                "adb": "",
                "pymobiledevice3": "",
                "xcrun": "",
            }
        )

        adb = next(check for check in checks if check.key == "adb")

        self.assertEqual(adb.state, "missing")
        self.assertEqual(adb.level, "required")
        self.assertIn("Android 真机", adb.detail)
        self.assertIn("Platform-Tools", adb.action)

    def test_reports_ios_toolchain_as_ready_when_pymobiledevice3_exists(self) -> None:
        checks = build_environment_checks(
            {
                "python": "/usr/bin/python3",
                "adb": "/opt/android/adb",
                "pymobiledevice3": "/tmp/pymobiledevice3",
                "xcrun": "/usr/bin/xcrun",
            }
        )

        ios = next(check for check in checks if check.key == "pymobiledevice3")

        self.assertEqual(ios.state, "ok")
        self.assertEqual(ios.level, "optional")
        self.assertIn("iOS", ios.detail)

    def test_formats_environment_checks_for_sidebar_and_logs(self) -> None:
        checks = build_environment_checks(
            {
                "python": "/usr/bin/python3",
                "adb": "",
                "pymobiledevice3": "/tmp/pymobiledevice3",
                "xcrun": "",
            }
        )

        text = format_environment_checks(checks)

        self.assertIn("Python 3：可用", text)
        self.assertIn("Android ADB：缺失", text)
        self.assertIn("iOS pymobiledevice3：可用", text)
        self.assertIn("Xcode xcrun：缺失", text)


if __name__ == "__main__":
    unittest.main()
