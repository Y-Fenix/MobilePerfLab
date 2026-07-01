# MobilePerfLab

MobilePerfLab 是一款原创桌面移动端性能测试工具，工作流参考常见的移动性能分析产品：连接设备、选择 App、实时采集、曲线观察、事件标记、截图和报告导出。

## 当前能力

- Android 设备发现：通过 `adb devices -l` 自动识别真机。
- Android 应用选择：读取第三方包名列表，或自动识别前台应用。
- Android 实时采样：FPS/Jank、CPU、内存、电量、温度、估算功耗、网络速率；前台应用识别会组合 `dumpsys window`、`dumpsys activity` 等多路结果，CPU 会汇总同一包名的多个 PID，网络会优先读取 per-UID 统计，失败时使用设备级上下行兜底并在日志中标注。
- Android 弱网工具：内置本机 HTTP/HTTPS 弱网代理，可一键设置/清除 Android 系统代理，支持延迟、抖动、丢包、上下行限速，并提供电梯、地铁、高速、隧道等场景预设。
- 弱网链路诊断：工具会读回 Android 当前系统代理，并从设备侧探测本机代理端口；若设备缺少 `nc`，会使用内置 HTTP 健康检查端点兜底，弱网页签会显示本机代理、Android 设备、设备代理和端口连通状态。
- 弱网安全清理：工具会记录自己写入过代理的 Android 设备，退出时自动尝试清理系统代理；弱网页签也可刷新当前代理状态。
- 低端机友好曲线：实时窗口默认开启展示层平滑，短暂缺采不会把曲线直接拉到 0；导出的 CSV/JSON/HTML 仍保存原始采样值。
- 采集健康状态：性能页会标出 FPS、CPU、内存、电量、温度、功耗、上下行网络的“正常 / 等待 / 无流量 / 异常”状态，并实时显示网络来源、异常样本占比和设备级网络兜底占比。
- 曲线质量标识：实时曲线和 HTML 报告会标出异常样本与设备级网络兜底样本；橙色圆环表示网络数据来自设备级兜底，红色三角表示该采样点存在采集异常说明，浅橙/浅红背景表示连续兜底或异常区间。
- iOS 设备发现：优先使用 `pymobiledevice3 usbmux` 识别真实在线设备，再用 Xcode `devicectl`/`xctrace` 补充状态。
- iOS 基础采样：通过 `pymobiledevice3 diagnostics battery` 读取电量、温度、估算功耗。
- iOS 进程采样：启动 tunnel 后通过 `pymobiledevice3 developer dvt sysmon` 读取 CPU/内存。
- iOS FPS 采样：启动 tunnel 后通过 `pymobiledevice3 developer dvt graphics` 读取 `CoreAnimationFramesPerSecond`，设备必须能被 `pymobiledevice3 usbmux list` 识别。
- iOS Jank：当前按 FPS 相对 60 FPS 的掉帧比例估算，用于趋势参考。
- 演示模式：没有真机时可预览完整 UI、实时曲线、标记和导出流程。
- 报告导出：一次导出 CSV、JSON、HTML 三份结果；HTML 报告包含采集质量摘要、异常/兜底样本占比、异常区间摘要和网络数据来源，设备级网络兜底会明确标注“非目标 App 独占流量”。

## 运行

双击 `一键启动.command`，或在终端执行：

```bash
python3 mobileperflab.py
```

## 测试

```bash
python3 -m unittest discover -s tests
python3 -m py_compile mobileperflab.py
```

`tests/` 目录建议一起上传到 GitHub，用于回归验证曲线稳定、采集健康状态、Android 前台应用识别、多进程 CPU 采样、上下行网络解析和报告质量摘要。

## 打包

双击 `一键打包.command`。打包完成后生成：

```text
dist/MobilePerfLab.app
```

## Android 环境

推荐安装 Android SDK Platform-Tools，并确保 `adb` 在 `PATH` 中。也可以把 `platform-tools/adb` 放在项目目录或同级 `AndroidTools/platform-tools/adb` 下，应用会自动尝试识别。

设备需开启 USB 调试并授权当前电脑。

## 弱网工具

主工作区包含“性能采集”和“弱网工具”两个页签。“弱网工具”提供常用网络预设，也可以手动配置端口、延迟、抖动、丢包率、下行和上行限速。

Android 使用方式：

1. 选择 Android 设备。
2. 切到“弱网工具”页签，选择预设或填写参数。
3. 点击“启动代理”。
4. 点击“应用到 Android”，工具会通过 adb 写入系统 HTTP 代理。
5. 查看“链路诊断”，确认“设备代理”为“已确认”，“端口连通”为“可达”。
6. 测试结束后点击“清除代理”，避免设备继续走代理。

链路诊断含义：

- 本机代理：桌面端弱网代理是否正在监听。
- Android 设备：当前是否选择了 Android 设备。
- 设备代理：adb 写入后再读回的 Android 系统代理是否等于当前代理地址。
- 端口连通：Android 设备是否能连到电脑上的弱网代理端口；工具会先尝试 `toybox nc` / `nc`，再尝试访问 `http://<host>:<port>/__mobileperflab_health`。

当前弱网为本机 HTTP/HTTPS 代理模式，不需要 Root，适合大部分走系统代理的接口请求。若目标 App 使用 UDP、QUIC，或主动绕过系统代理，则需要后续接入 VPN/tun 模式才能完全覆盖。

如果忘记清除代理，工具退出时会自动尝试清理本次由工具写入过代理的 Android 设备。建议测试结束仍手动点击“清除 Android 代理”，并用“刷新状态”确认设备当前代理状态。

## iOS 环境

iOS 真机需要先信任电脑。基础识别可使用 Xcode 命令行工具；更完整的设备信息建议安装：

双击 `安装iOS依赖.command` 安装 `pymobiledevice3`。

如果要采集 iOS CPU/内存/FPS，双击 `启动iOS采集服务.command`，输入本机登录密码，并保持该窗口打开。FPS 来自 DVT graphics 的 `CoreAnimationFramesPerSecond`；如果日志提示 `pymobiledevice3 当前未识别设备`，请重新插拔、信任设备，或切换到 `pymobiledevice3 usbmux list` 可见的设备。

## 说明

本项目不使用 PerfDog 的品牌、图标、界面素材或私有协议；只复刻移动性能测试工具的通用工作流和信息架构。
