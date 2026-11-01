"""Windows 手柄诊断：默认读取；--rumble 对选中的一只手柄测试 200 毫秒。

优先使用随包 SDL3.dll，不安装驱动、不修改 Steam 配置。
API 返回成功只表示命令被接受，实际震感需要人工确认。
"""

import argparse
import ctypes as C
from ctypes import wintypes as W
from datetime import datetime
import hashlib
import json
from pathlib import Path
import sys
import time
from sdl_runtime import resolve_sdl


class Gamepad(C.Structure):
    _fields_ = [("buttons", W.WORD), ("lt", W.BYTE), ("rt", W.BYTE),
                ("lx", C.c_short), ("ly", C.c_short),
                ("rx", C.c_short), ("ry", C.c_short)]


class State(C.Structure):
    _fields_ = [("packet", W.DWORD), ("gamepad", Gamepad)]


class HidCaps(C.Structure):
    _fields_ = [(key, W.USHORT) for key in (
        "Usage", "UsagePage", "InputReportByteLength",
        "OutputReportByteLength", "FeatureReportByteLength")]
    _fields_ += [("Reserved", W.USHORT * 17)]
    _fields_ += [(key, W.USHORT) for key in (
        "NumberLinkCollectionNodes", "NumberInputButtonCaps",
        "NumberInputValueCaps", "NumberInputDataIndices",
        "NumberOutputButtonCaps", "NumberOutputValueCaps",
        "NumberOutputDataIndices", "NumberFeatureButtonCaps",
        "NumberFeatureValueCaps", "NumberFeatureDataIndices")]


def hid_caps(path):
    if not path.lower().startswith("\\\\?\\hid#"):
        return None
    kernel = C.WinDLL("kernel32", use_last_error=True)
    hid = C.WinDLL("hid", use_last_error=True)
    kernel.CreateFileW.argtypes = [W.LPCWSTR, W.DWORD, W.DWORD, C.c_void_p,
                                  W.DWORD, W.DWORD, W.HANDLE]
    kernel.CreateFileW.restype = W.HANDLE
    kernel.CloseHandle.argtypes = [W.HANDLE]
    hid.HidD_GetPreparsedData.argtypes = [W.HANDLE, C.POINTER(C.c_void_p)]
    hid.HidD_GetPreparsedData.restype = C.c_ubyte
    hid.HidD_FreePreparsedData.argtypes = [C.c_void_p]
    hid.HidP_GetCaps.argtypes = [C.c_void_p, C.POINTER(HidCaps)]
    hid.HidP_GetCaps.restype = C.c_long
    handle = kernel.CreateFileW(path, 0, 3, None, 3, 0, None)
    if handle == C.c_void_p(-1).value:
        return {"open_error": C.get_last_error()}
    preparsed = C.c_void_p()
    try:
        if not hid.HidD_GetPreparsedData(handle, C.byref(preparsed)):
            return {"preparsed_error": C.get_last_error()}
        caps = HidCaps()
        status = hid.HidP_GetCaps(preparsed, C.byref(caps))
        if status != 0x110000:
            return {"status": hex(status)}
        return {"usage_page": hex(caps.UsagePage), "usage": hex(caps.Usage),
                "input_bytes": caps.InputReportByteLength,
                "output_bytes": caps.OutputReportByteLength,
                "feature_bytes": caps.FeatureReportByteLength}
    finally:
        if preparsed.value:
            hid.HidD_FreePreparsedData(preparsed)
        kernel.CloseHandle(handle)


def probe(args):
    result = {"time": datetime.now().astimezone().isoformat(),
              "rumble_requested": args.rumble, "xinput": [], "sdl": {}}
    xinput = C.WinDLL("xinput1_4.dll")
    xinput.XInputGetState.argtypes = [W.DWORD, C.POINTER(State)]
    xinput.XInputGetState.restype = W.DWORD
    for slot in range(4):
        state = State()
        rc = xinput.XInputGetState(slot, C.byref(state))
        result["xinput"].append({"slot": slot, "result": rc, "connected": rc == 0})

    dll_path = resolve_sdl(args.sdl)
    result["sdl"].update({"path": str(dll_path),
        "sha256": hashlib.sha256(dll_path.read_bytes()).hexdigest()})
    sdl = C.CDLL(str(dll_path))

    def bind(name, restype, argtypes):
        fn = getattr(sdl, name)
        fn.restype, fn.argtypes = restype, argtypes

    for name, restype, argtypes in [
        ("SDL_Init", C.c_bool, [C.c_uint32]),
        ("SDL_GetError", C.c_char_p, []),
        ("SDL_GetJoysticks", C.POINTER(C.c_uint32), [C.POINTER(C.c_int)]),
        ("SDL_OpenJoystick", C.c_void_p, [C.c_uint32]),
        ("SDL_GetJoystickProperties", C.c_uint32, [C.c_void_p]),
        ("SDL_GetBooleanProperty", C.c_bool, [C.c_uint32, C.c_char_p, C.c_bool]),
        ("SDL_IsGamepad", C.c_bool, [C.c_uint32]),
        ("SDL_RumbleJoystick", C.c_bool,
         [C.c_void_p, C.c_uint16, C.c_uint16, C.c_uint32]),
        ("SDL_UpdateJoysticks", None, []),
        ("SDL_CloseJoystick", None, [C.c_void_p]),
        ("SDL_free", None, [C.c_void_p]),
        ("SDL_Quit", None, []),
    ]:
        bind(name, restype, argtypes)
    for suffix in ("Name", "Path"):
        bind("SDL_GetJoystick" + suffix, C.c_char_p, [C.c_void_p])
    for suffix in ("Vendor", "Product"):
        bind("SDL_GetJoystick" + suffix, C.c_uint16, [C.c_void_p])
    for suffix in ("Axes", "Buttons", "Hats"):
        bind("SDL_GetNumJoystick" + suffix, C.c_int, [C.c_void_p])

    def error():
        return (sdl.SDL_GetError() or b"").decode("utf-8", errors="replace")

    ids = None
    try:
        if not sdl.SDL_Init(0x2200):
            result["sdl"]["error"] = error()
            return result
        count = C.c_int()
        ids = sdl.SDL_GetJoysticks(C.byref(count))
        device_ids = [ids[i] for i in range(count.value)] if ids else []
        target = args.device_id
        if args.rumble and target is None:
            if len(device_ids) == 1:
                target = device_ids[0]
            else:
                result["sdl"]["rumble_skipped"] = "请用 --device-id 选择一只手柄"
        if args.rumble and target is not None and target not in device_ids:
            result["sdl"]["rumble_skipped"] = "指定设备当前不存在"
        devices = []
        for instance_id in device_ids:
            joystick = sdl.SDL_OpenJoystick(instance_id)
            if not joystick:
                devices.append({"id": instance_id, "open_error": error()})
                continue
            tested = False
            try:
                props = sdl.SDL_GetJoystickProperties(joystick)
                path = (sdl.SDL_GetJoystickPath(joystick) or b"").decode(errors="replace")
                row = {"id": instance_id,
                    "name": (sdl.SDL_GetJoystickName(joystick) or b"").decode(errors="replace"),
                    "vid": hex(sdl.SDL_GetJoystickVendor(joystick)),
                    "pid": hex(sdl.SDL_GetJoystickProduct(joystick)),
                    "path": path, "is_gamepad": sdl.SDL_IsGamepad(instance_id),
                    "axes": sdl.SDL_GetNumJoystickAxes(joystick),
                    "buttons": sdl.SDL_GetNumJoystickButtons(joystick),
                    "hats": sdl.SDL_GetNumJoystickHats(joystick),
                    "hid_caps": hid_caps(path),
                    "rumble_capable": sdl.SDL_GetBooleanProperty(props, b"SDL.joystick.cap.rumble", False),
                    "trigger_rumble_capable": sdl.SDL_GetBooleanProperty(props, b"SDL.joystick.cap.trigger_rumble", False)}
                if args.rumble and instance_id == target:
                    tested = True
                    ok = sdl.SDL_RumbleJoystick(joystick, 10000, 10000, 200)
                    row["rumble_test"] = {"accepted": ok, "error": "" if ok else error(),
                                          "amplitude": 10000, "duration_ms": 200,
                                          "physical_response": "需人工确认" if ok else "未获 API 接受"}
                    if ok:
                        deadline = time.monotonic() + 0.25
                        while time.monotonic() < deadline:
                            sdl.SDL_UpdateJoysticks()
                            time.sleep(0.01)
                devices.append(row)
            finally:
                if tested:
                    sdl.SDL_RumbleJoystick(joystick, 0, 0, 0)
                sdl.SDL_CloseJoystick(joystick)
        result["sdl"]["devices"] = devices
        return result
    finally:
        if ids:
            sdl.SDL_free(ids)
        sdl.SDL_Quit()


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sdl", help="指定本机可信 SDL3.dll 的绝对路径")
    parser.add_argument("--rumble", action="store_true", help="低强度震动测试，结束时停止马达")
    parser.add_argument("--device-id", type=int, help="本次 SDL 枚举 ID，多手柄测试时必须指定")
    parser.add_argument("--output", type=Path, help="将结果保存为 JSON；拒绝覆盖现有文件")
    arguments = parser.parse_args()
    if arguments.output and arguments.output.exists():
        parser.error("输出文件已存在，请使用新文件名")
    payload = json.dumps(probe(arguments), ensure_ascii=False, indent=2)
    if arguments.output:
        with arguments.output.open("x", encoding="utf-8") as stream:
            stream.write(payload + "\n")
    print(payload)
