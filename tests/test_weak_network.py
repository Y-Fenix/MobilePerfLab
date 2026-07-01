import unittest

from mobileperflab import DeviceInfo, WeakProxyDeviceRegistry


class FakeAndroid:
    def __init__(self) -> None:
        self.cleared: list[str] = []

    def clear_http_proxy(self, device: DeviceInfo) -> tuple[bool, str]:
        self.cleared.append(device.serial)
        return True, ""


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


if __name__ == "__main__":
    unittest.main()
