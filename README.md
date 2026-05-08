# MobilePerfLab

MobilePerfLab 是一款原创桌面移动端性能测试工具，工作流参考常见的移动性能分析产品：连接设备、选择 App、实时采集、曲线观察、事件标记、截图和报告导出。

## 当前能力

- Android 设备发现：通过 `adb devices -l` 自动识别真机。
- Android 应用选择：读取第三方包名列表，或自动识别前台应用。
- Android 实时采样：FPS/Jank、CPU、内存、电量、温度、估算功耗、网络速率。
- iOS 设备发现：优先使用 `pymobiledevice3 usbmux` 识别真实在线设备，再用 Xcode `devicectl`/`xctrace` 补充状态。
- iOS 基础采样：通过 `pymobiledevice3 diagnostics battery` 读取电量、温度、估算功耗。
- iOS 进程采样：启动 tunnel 后通过 `pymobiledevice3 developer dvt sysmon` 读取 CPU/内存。
- iOS FPS 采样：启动 tunnel 后通过 `pymobiledevice3 developer dvt graphics` 读取 `CoreAnimationFramesPerSecond`，设备必须能被 `pymobiledevice3 usbmux list` 识别。
- iOS Jank：当前按 FPS 相对 60 FPS 的掉帧比例估算，用于趋势参考。
- 演示模式：没有真机时可预览完整 UI、实时曲线、标记和导出流程。
- 报告导出：一次导出 CSV、JSON、HTML 三份结果。

## 运行

双击 `一键启动.command`，或在终端执行：

```bash
python3 mobileperflab.py
```

## 打包

双击 `一键打包.command`。打包完成后生成：

```text
dist/MobilePerfLab.app
```

## Android 环境

推荐安装 Android SDK Platform-Tools，并确保 `adb` 在 `PATH` 中。也可以把 `platform-tools/adb` 放在项目目录或同级 `AndroidTools/platform-tools/adb` 下，应用会自动尝试识别。

设备需开启 USB 调试并授权当前电脑。

## iOS 环境

iOS 真机需要先信任电脑。基础识别可使用 Xcode 命令行工具；更完整的设备信息建议安装：

双击 `安装iOS依赖.command` 安装 `pymobiledevice3`。

如果要采集 iOS CPU/内存/FPS，双击 `启动iOS采集服务.command`，输入本机登录密码，并保持该窗口打开。FPS 来自 DVT graphics 的 `CoreAnimationFramesPerSecond`；如果日志提示 `pymobiledevice3 当前未识别设备`，请重新插拔、信任设备，或切换到 `pymobiledevice3 usbmux list` 可见的设备。

## 说明

本项目不使用 PerfDog 的品牌、图标、界面素材或私有协议；只复刻移动性能测试工具的通用工作流和信息架构。
