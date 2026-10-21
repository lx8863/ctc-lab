# BLE LE 1M 手机验证

本阶段在现有 SDR 发射符合 LE 1M 调制与 legacy advertising 组帧约束的测试广播，以安卓手机作为独立接收机。尚不实现连接、扫描响应或 BLE→Zigbee CTC 映射。

## 已完成的程序验证

- BLE CRC、白化和完整空口比特流，与 [Bluetooth Core 6.0 Vol 6 Part C, 4.2.1 官方样例](https://www.bluetooth.com/wp-content/uploads/Files/Specification/HTML/Core-60/out/en/low-energy-controller/sample-data.html) 逐位一致。
- GFSK 使用 LE 1M、BT=0.5、调制指数 h=0.5；连续同值比特对应 ±250 kHz 频偏，测试检查包络和频偏。
- 独立 BLE 软件接收器仅输入 IQ、采样率和信道，搜索采样相位及公开接入地址，进行解白化、长度和 CRC 检查。
- 37/38/39 信道分别通过含 42 kHz 频偏和噪声的解码测试；坏 CRC 和错误白化信道被拒绝。
- 加上第一阶段测试，全工程 24 项测试通过。

## 第一次真实射频运行

目录：`artifacts/20261020_144629_119811_ble_advertise/`。

8 MSPS，37 信道 2402 MHz，TX1/RX1，TX −25 dB / RX 20 dB，数字回环关闭。独立软件接收器从实际射频采样中收到 1 个与发送 PDU 完全匹配且 CRC 正确的包。

这说明本板的 BLE 波形收发与软件解析已经打通。自动生成报告默认 `phone_reception_confirmed=false`，不把同板接收当作手机接收；下述人工确认后才将对应报告标为通过。

## 安卓手机验证已通过

用户在 nRF Connect 中发现目标，并明确确认 Manufacturer Data 中测试数据为 `ABCDEF01`。对应发射轮次为 `artifacts/20261020_145220_306608_ble_advertise/`，39 信道 2480 MHz，TX −10 dB，地址 `C2:54:43:00:00:01`、名称 `SDR-BLE-TEST`。

手机确认记录保存在 `artifacts/phone_observations.json`，该轮 `report.json` 中 `phone_reception_confirmed=true`。来源为用户实际观察，不是自动读取手机屏幕。确认后已停止发射，最终 `status=stopped`。

## 独立商品 BLE 接收机验证已通过

首次手机反馈未发现目标。随后在 39 信道 2480 MHz、TX −10 dB 条件下，使用电脑内置 Intel 蓝牙适配器，通过 Windows BLE 广播接口独立接收：

| 测试载荷 | 扫描时长 | 目标广播回调数 | RSSI | 结果 |
|---|---|---|---|---|
| `12345678` | 20 秒 | 8 | −58～−64 dBm | 地址、名称、载荷均匹配 |
| `ABCDEF01` | 20 秒 | 7 | −60～−64 dBm | 更换载荷后正确更新 |

报告分别位于 `artifacts/20261020_145141_046178_pc_ble_scan/report.json`、`artifacts/20261020_145249_787733_pc_ble_scan/report.json`。这属于独立商品 BLE 芯片接收证据，不是 SDR 自发自收；两种载荷的切换进一步排除了仅显示缓存的问题。

回调数受 Windows 扫描窗口及重复过滤影响，不是空口包数，不能作为收包率。安卓手机随后也确认了目标和 `ABCDEF01` 载荷。第一次与后续测试同时改变了信道和增益，不能据此归因首次未发现的具体原因。

电脑扫描脚本只读取广播，不配对、不连接；只保存目标设备信息和其他设备的数量，不保存周边设备身份。安装可选依赖后运行：

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-pc-scan.txt
.\.venv\Scripts\python.exe scan_ble.py --seconds 20
```

## 手机应看到的内容

| 字段 | 预期 |
|---|---|
| Complete Local Name | `SDR-BLE-TEST` |
| Advertising address | `C2:54:43:00:00:01` |
| Manufacturer ID | `0xFFFF`，仅实验标记，不代表任何厂商 |
| Manufacturer Data 的测试数据 | `12 34 56 78` |
| 广播类型 | 不可连接、不可扫描，`ADV_NONCONN_IND` |

手机安装 nRF Connect，打开蓝牙并允许“附近设备”权限；系统要求时还需开启定位／定位权限。在 Scanner 点击 Scan，按名称搜索，展开设备查看 Manufacturer Data。原始字段有时包含 `FF FF` 两字节标记前缀；其后的四字节应为 `12 34 56 78`。

不需要配对，不点击连接。列表可能保留旧设备，观察最后接收时间或广播更新。停发后条目不立即消失是正常的。

## 运行与停止

全部命令在 `D:\项目\USRP\ctc_lab` 执行。`ble-generate` 只生成文件，不发射。

```powershell
.\.venv\Scripts\python.exe -m ctc_lab ble-generate
.\.venv\Scripts\python.exe -m ctc_lab devices
.\.venv\Scripts\python.exe -m ctc_lab ble-advertise --uri usb:1.5.5 --seconds 180 --verify-rf
```

发射持续时间默认 180 秒、最多 600 秒。命令会在结束或异常时销毁循环缓冲，停止发射。可以在另一个终端运行：

```powershell
.\.venv\Scripts\python.exe -m ctc_lab ble-stop
```

停止机制为本工程产物目录下的 STOP 文件，发射程序每 0.2 秒检查一次。不需要结束其他程序或修改设备固件。

确认第一次载荷后，可再次运行测试新数据，排除缓存：

```powershell
.\.venv\Scripts\python.exe -m ctc_lab ble-advertise --uri usb:1.5.5 --seconds 180 --verify-rf --marker ABCDEF01
```

同一时刻只能运行一个发射命令。若更换 USB 插口，先用 `devices` 确认 URI。手机未找到时先检查权限、是否前台扫描、天线、距离和发射是否已自动结束，再评估功率与信道。

## 结果解释

本原型只启用一个主广播信道，不进行三个信道的事件内轮转；生成的三个合法时长间隔在有限循环缓冲中重复，尚不是完整 GAP 广播控制器。手机扫描可能需要等待，不能用 App 显示的广播数量计算空口收包率。

每轮目录保存 `manifest.json`、TX IQ、可选 RF IQ 和最终 `report.json`；`artifacts/ble_active.json` 保存最近一轮状态。`status=transmitting` 表示程序记录的发射状态；运行中的 report 在结束时落盘。

人工手机观察应单独记录，包括名称、数据是否一致及载荷切换结果。未经实际确认，不将手机互操作标成通过。
