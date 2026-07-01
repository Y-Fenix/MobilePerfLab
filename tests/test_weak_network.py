import unittest

from mobileperflab import (
    AndroidAdapter,
    DeviceInfo,
    WeakNetworkProxy,
    WeakProxyDeviceRegistry,
    build_weak_network_diagnostics,
    verify_android_proxy_state,
)


class FakeAndroid:
    def __init__(self) -> None:
        self.cleared: list[str] = []

    def clear_http_proxy(self, device: DeviceInfo) -> tuple[bool, str]:
        self.cleared.append(device.serial)
        return True, ""


class FakeProbeAndroid(AndroidAdapter):
    def __init__(self, responses: list[tuple[int, str]]) -> None:
        self.responses = responses
        self.commands: list[str] = []

    def _adb(self, serial: str, shell_args: list[str], timeout: float = 8.0) -> tuple[int, str]:
        self.commands.append(shell_args[-1])
        return self.responses.pop(0)


class WeakProxyDeviceRegistryTest(unittest.TestCase):
    def test_cleans_only_registered_android_devices_once(self) -> None:
        registry = WeakProxyDeviceRegistry()
        android = FakeAndroid()
        first = DeviceInfo("Android", "serial-1", "Phone", "13", "P", "ready")
        second = DeviceInfo("Android", "serial-2", "Phone2", "14", "P2", "ready")

        registry.mark_applied(first, "192.168.1.2:18888")
        registry.mark_applied(first, "192.168.1.2:18888")
        registry.mark_applied(second, "192.168.1.2:18888")

        self.assertEqual(registry.cleanup(android), ["serial-1", "serial-2"])
        self.assertEqual(android.cleared, ["serial-1", "serial-2"])
        self.assertEqual(registry.cleanup(android), [])


class AndroidProxyVerificationTest(unittest.TestCase):
    def test_confirms_proxy_when_device_state_matches_expected_endpoint(self) -> None:
        result = verify_android_proxy_state("192.168.1.2:18888", "192.168.1.2:18888")

        self.assertTrue(result.confirmed)
        self.assertEqual(result.status_text, "Android 代理已确认生效：192.168.1.2:18888")
        self.assertEqual(result.log_text, "Android 代理读回确认：192.168.1.2:18888")

    def test_reports_mismatch_when_device_keeps_different_proxy(self) -> None:
        result = verify_android_proxy_state("192.168.1.2:18888", "null")

        self.assertFalse(result.confirmed)
        self.assertEqual(
            result.status_text,
            "Android 代理写入后未确认：期望 192.168.1.2:18888，当前未设置",
        )
        self.assertEqual(
            result.log_text,
            "Android 代理写入后读回不一致：期望 192.168.1.2:18888，实际 未设置",
        )


class WeakNetworkDiagnosticsTest(unittest.TestCase):
    def test_reports_confirmed_proxy_when_running_device_and_readback_match(self) -> None:
        device = DeviceInfo("Android", "serial-1", "Pixel", "14", "Pixel", "ready")

        diagnostics = build_weak_network_diagnostics(
            proxy_running=True,
            endpoint="192.168.1.2:18888",
            device=device,
            current_proxy="192.168.1.2:18888",
            proxy_reachable=True,
        )

        self.assertEqual(diagnostics.overall_state, "ok")
        self.assertEqual(diagnostics.summary, "弱网代理已确认生效，端口可达")
        self.assertEqual(
            diagnostics.rows,
            [
                ("本机代理", "运行中", "192.168.1.2:18888"),
                ("Android 设备", "已选择", "Pixel"),
                ("设备代理", "已确认", "192.168.1.2:18888"),
                ("端口连通", "可达", "Android 可连接本机代理端口"),
            ],
        )

    def test_reports_actionable_states_when_proxy_is_not_ready(self) -> None:
        diagnostics = build_weak_network_diagnostics(
            proxy_running=False,
            endpoint="192.168.1.2:18888",
            device=None,
            current_proxy="",
            proxy_reachable=None,
        )

        self.assertEqual(diagnostics.overall_state, "warning")
        self.assertEqual(diagnostics.summary, "弱网代理未就绪")
        self.assertEqual(
            diagnostics.rows,
            [
                ("本机代理", "未启动", "先点击启动代理"),
                ("Android 设备", "未选择", "请选择 Android 设备"),
                ("设备代理", "未检查", "选择设备后刷新状态"),
                ("端口连通", "未检查", "启动代理并选择设备后检测"),
            ],
        )

    def test_warns_when_proxy_is_confirmed_but_port_is_unreachable(self) -> None:
        device = DeviceInfo("Android", "serial-1", "Pixel", "14", "Pixel", "ready")

        diagnostics = build_weak_network_diagnostics(
            proxy_running=True,
            endpoint="192.168.1.2:18888",
            device=device,
            current_proxy="192.168.1.2:18888",
            proxy_reachable=False,
        )

        self.assertEqual(diagnostics.overall_state, "warning")
        self.assertEqual(diagnostics.summary, "Android 已写入代理，但端口不可达")
        self.assertEqual(diagnostics.rows[-1], ("端口连通", "不可达", "检查手机和电脑是否同一网络/防火墙"))


class WeakNetworkProxyHealthTest(unittest.TestCase):
    def test_recognizes_internal_health_check_request(self) -> None:
        request = b"GET /__mobileperflab_health HTTP/1.1\r\nHost: 127.0.0.1\r\n\r\n"

        self.assertTrue(WeakNetworkProxy._is_health_check_request(request))


class AndroidConnectivityProbeTest(unittest.TestCase):
    def test_falls_back_to_http_health_check_when_nc_is_unavailable(self) -> None:
        device = DeviceInfo("Android", "serial-1", "Pixel", "14", "Pixel", "ready")
        android = FakeProbeAndroid(
            [
                (127, "toybox: nc: not found"),
                (127, "nc: not found"),
                (0, "mobileperflab-ok"),
            ]
        )

        ok, detail = android.probe_tcp_connectivity(device, "192.168.1.2", 18888)

        self.assertTrue(ok)
        self.assertEqual(detail, "192.168.1.2:18888")
        self.assertIn("/__mobileperflab_health", android.commands[-1])


if __name__ == "__main__":
    unittest.main()
