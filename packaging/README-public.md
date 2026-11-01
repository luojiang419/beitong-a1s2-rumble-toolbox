# 北通 BTP-A1S2 震动工具箱（社区版）

帮助同款**纯有线手柄**排查“能操作游戏，却没有震动”：检测当前模式、指导切换 PC360、测试左右马达，再用小赛车确认碰撞、砂石路面和打滑反馈。

## 下载和使用

到 [Releases](https://github.com/luojiang419/beitong-a1s2-rumble-toolbox/releases/latest) 下载 `Beitong-A1S2-Rumble-Toolbox-v版本-windows-x64.zip`，完整解压后双击 `BeitongRumbleToolbox.exe`。

- 支持 Windows 10/11 **x64**。无需管理员权限、无需安装 Python 或 Steam。
- 启动器会打开本机验证页面；工具退出时停止震动并关闭服务。
- 本项目不是北通官方软件，不安装/替换系统驱动，不刷固件，不修改 Steam 设置，不自动切换硬件模式。
- 首版未进行代码签名。发行包附 SHA-256 校验文件，不承诺通过企业软件限制或 Windows 来源检查。

## 已验证的修复方法

验证机型：**BTP-A1S2 纯有线版**，外观有 LOGO、TURBO、SHIFT 和 1～4 号指示灯。先核对型号与外观，不要套用无线阿修罗 2S 的 PLAY/RGB/配对教程。

1. 保持 USB 连接，长按中央 LOGO 键。
2. 指示灯停止闪烁时迅速松开，按同样方法循环切换。
3. **1 号灯单独常亮是 PC360 模式。** 工具应识别 Xbox 360 Controller / XInput，并显示震动接口可用。
4. 点击左、右、双马达测试，实际握住手柄确认震感，然后试驾小赛车。

LOGO 可能同时唤起 Steam 大屏幕；这不代表切换失败，以工具里的设备变化为准。断电后若回到其他模式，重新检查编号灯。

| 状态 | 修复前 | 本次实机切换后 |
| --- | --- | --- |
| 产品 | BEITONG A1S2 BFM GAMEPAD | Xbox 360 Controller |
| VID:PID | 20BC:505B | 045E:028E |
| XInput | 未连接 | 槽位 0 已连接 |
| 标准震动 | 不支持，调用被拒绝 | 左/右/双马达非零请求成功 |

实机用户已确认“小赛车中的震动反馈完全准确”。这是**该样机与本工具**的验收；尚未逐款验证《极品飞车》《极限竞速：地平线》，也未验证所有同名硬件批次。Xbox 通用 VID/PID 本身不能证明一定是同款北通。

模式依据：[北通阿修罗 2有线版官方连接教程](https://www.yuque.com/fujuan-us0ly/zqoosl/rx7o7tilbts95k73)。官网未直接列完整 BTP-A1S2/505B 字符串，适用判断经过用户外观核对及实际模式切换验证。

## 赛车操作

- 左摇杆转向，RT 加速，LT 刹车，X 手刹；也支持 WASD/方向键/空格。
- 页面支持按键捕捉绑定和转向轴选择，便于处理其他布局。
- 撞橙色障碍、驶入砂石路肩、加速转向并手刹，分别触发事件反馈。
- 暂停、转入后台、断线和页面失联时停止输出。请仅连接待测手柄，避免多设备误选。

游戏内仍无震动时，保持 PC360，检查具体游戏的振动开关与 Steam Input 转发；不要叠加多层转换程序。普通双马达不具备 DualSense 自适应扳机或同等触觉细节。

完成 PC360 模式切换和验证后，可关闭本工具；支持 XInput 的游戏直接通过 Windows 控制手柄，不需要本工具常驻。

## 源码运行与构建

```powershell
python -m pip install -r requirements-build.txt
python scripts/fetch_sdl.py
python toolkit.py
python -m unittest discover -s tests -v
node --test web/physics.test.js web/input.test.js
python scripts/build_windows.py --version 1.0.0
python scripts/verify_release.py --version 1.0.0
```

程序使用随包的官方 SDL 运行库；固定版本、来源和压缩包 SHA-256 见 `dependencies.json`。构建器为 PyInstaller，生成自包含 Windows x64 便携包。CI 在托管 Windows Runner 验证启动、页面资源与 API；没有实体 USB 手柄时不声称硬件测试通过。

默认只监听 `127.0.0.1:18765`。启动器中的“停止震动并退出”关闭本次服务。需要自动化验证可用 `--headless --port 18766 --lifetime 10 --ready-file ready.json` 或 `--self-test --report result.json`；自检默认不驱动马达。

## 许可证与反馈

项目源码：MIT。SDL、Python、Tcl/Tk 等第三方许可随包保留，见 `THIRD_PARTY_NOTICES.md`。不包含厂商旧驱动、固件、Steam DLL 或用户日志。

提交 Issue 时说明背面型号、连接方式、编号灯、切换前后产品名、Windows 版本与马达测试结果即可；请不要上传完整 Steam 日志、账号信息或其他个人数据。
