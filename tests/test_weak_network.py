import socket
import threading
import unittest

from mobileperflab import (
    AndroidAdapter,
    DeviceInfo,
    ProxyTrafficSnapshot,
    WeakNetworkProxy,
    WeakProxyDeviceRegistry,
    build_weak_network_diagnostics,
    format_proxy_traffic_snapshot,
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

    def test_internal_health_check_does_not_count_as_real_proxy_traffic(self) -> None:
        proxy = WeakNetworkProxy(lambda _text: None)
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
        sock.close()

        proxy.configure(port, 0, 0, 0.0, 0.0, 0.0)
        proxy.start()
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=2.0) as client:
                client.sendall(b"GET /__mobileperflab_health HTTP/1.1\r\nHost: 127.0.0.1\r\n\r\n")
                response = client.recv(4096)

            self.assertIn(b"mobileperflab-ok", response)
            self.assertEqual(proxy.traffic_snapshot().total_connections, 0)
        finally:
            proxy.stop()


class WeakNetworkProxyTrafficTest(unittest.TestCase):
    def test_tracks_real_proxy_bytes_and_recent_rates(self) -> None:
        proxy = WeakNetworkProxy(lambda _text: None)
        proxy.reset_traffic(now=10.0)

        proxy._record_connection_open(now=10.0)
        proxy._record_transfer("up", 2048, now=10.5)
        proxy._record_transfer("down", 4096, now=11.0)

        snapshot = proxy.traffic_snapshot(now=11.0)

        self.assertEqual(snapshot.up_bytes, 2048)
        self.assertEqual(snapshot.down_bytes, 4096)
        self.assertAlmostEqual(snapshot.up_kbps, 2.0)
        self.assertAlmostEqual(snapshot.down_kbps, 4.0)
        self.assertEqual(snapshot.active_connections, 1)
        self.assertEqual(snapshot.total_connections, 1)

        proxy._record_connection_close()
        self.assertEqual(proxy.traffic_snapshot(now=12.0).active_connections, 0)

    def test_proxy_rates_decay_to_zero_when_traffic_stops(self) -> None:
        proxy = WeakNetworkProxy(lambda _text: None)
        proxy.reset_traffic(now=20.0)

        proxy._record_transfer("down", 1024, now=21.0)

        self.assertAlmostEqual(proxy.traffic_snapshot(now=21.0).down_kbps, 1.0)
        self.assertEqual(proxy.traffic_snapshot(now=30.0).down_kbps, 0.0)

    def test_tracks_dropped_proxy_connections(self) -> None:
        proxy = WeakNetworkProxy(lambda _text: None)
        proxy.reset_traffic(now=1.0)

        proxy._record_connection_open(now=1.0)
        proxy._record_dropped_connection(now=1.2)
        proxy._record_connection_close()

        snapshot = proxy.traffic_snapshot(now=2.0)

        self.assertEqual(snapshot.total_connections, 1)
        self.assertEqual(snapshot.dropped_connections, 1)
        self.assertEqual(snapshot.active_connections, 0)


class ProxyTrafficFormattingTest(unittest.TestCase):
    def test_formats_proxy_traffic_for_live_panel(self) -> None:
        values = format_proxy_traffic_snapshot(
            ProxyTrafficSnapshot(
                up_bytes=1536,
                down_bytes=2 * 1024 * 1024,
                up_kbps=12.345,
                down_kbps=456.789,
                active_connections=2,
                total_connections=9,
                dropped_connections=1,
                last_activity_age=1.4,
            )
        )

        self.assertEqual(values["down_rate"], "456.8 KB/s")
        self.assertEqual(values["up_rate"], "12.3 KB/s")
        self.assertEqual(values["down_total"], "2.0 MB")
        self.assertEqual(values["up_total"], "1.5 KB")
        self.assertEqual(values["connections"], "2 活跃 / 9 总计")
        self.assertEqual(values["drops"], "1")
        self.assertEqual(values["activity"], "1.4s 前")

    def test_formats_proxy_traffic_without_activity(self) -> None:
        values = format_proxy_traffic_snapshot(ProxyTrafficSnapshot())

        self.assertEqual(values["down_total"], "0 B")
        self.assertEqual(values["up_total"], "0 B")
        self.assertEqual(values["activity"], "无")


class WeakNetworkProxyIntegrationTest(unittest.TestCase):
    @staticmethod
    def _free_port() -> int:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
        sock.close()
        return port

    def test_plain_http_proxy_request_counts_real_up_and_down_traffic(self) -> None:
        target = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        target.bind(("127.0.0.1", 0))
        target.listen(1)
        target_port = target.getsockname()[1]
        received: list[bytes] = []
        server_done = threading.Event()

        def serve_once() -> None:
            try:
                conn, _address = target.accept()
                with conn:
                    received.append(conn.recv(4096))
                    conn.sendall(b"HTTP/1.1 200 OK\r\nContent-Length: 5\r\nConnection: close\r\n\r\nhello")
            finally:
                target.close()
                server_done.set()

        thread = threading.Thread(target=serve_once, daemon=True)
        thread.start()

        proxy = WeakNetworkProxy(lambda _text: None)
        proxy_port = self._free_port()
        proxy.configure(proxy_port, 0, 0, 0.0, 0.0, 0.0)
        proxy.start()
        try:
            request = (
                f"GET http://127.0.0.1:{target_port}/ping HTTP/1.1\r\n"
                f"Host: 127.0.0.1:{target_port}\r\n"
                "Connection: close\r\n\r\n"
            ).encode("ascii")
            response = b""
            with socket.create_connection(("127.0.0.1", proxy_port), timeout=2.0) as client:
                client.sendall(request)
                while True:
                    chunk = client.recv(4096)
                    if not chunk:
                        break
                    response += chunk

            self.assertTrue(server_done.wait(1.0))
            self.assertIn(b"hello", response)
            self.assertTrue(received)
            snapshot = proxy.traffic_snapshot()
            self.assertEqual(snapshot.total_connections, 1)
            self.assertGreaterEqual(snapshot.up_bytes, len(received[0]))
            self.assertGreaterEqual(snapshot.down_bytes, len(response))
        finally:
            proxy.stop()


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
