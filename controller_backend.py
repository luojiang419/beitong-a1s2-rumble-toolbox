"""本地赛车的 SDL 手柄输入与事件震动后端；所有 SDL 调用归属同一线程。"""
from collections import deque
import copy
import ctypes as C
from ctypes import wintypes as W
import json
import math
from pathlib import Path
import queue
import re
import threading
import time
from sdl_runtime import resolve_sdl

EVENTS = {"collision": 4, "test": 5, "gravel": 2, "skid": 2, "engine": 1}


def validate_rumble(data):
    if not isinstance(data, dict) or not isinstance(data.get("event"), str) or data["event"] not in EVENTS:
        raise ValueError("未知震动事件")
    result = {"event": data["event"]}
    for key in ("low", "high"):
        value = data.get(key)
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not 0 <= value <= 1:
            raise ValueError(f"{key} 必须是 0 到 1 的有限数值")
        result[key] = float(value)
    duration = data.get("duration_ms")
    if isinstance(duration, bool) or not isinstance(duration, int) or not 1 <= duration <= 1000:
        raise ValueError("duration_ms 必须为 1 到 1000 的整数")
    result["duration_ms"] = duration
    return result


class NativeSDL:
    def __init__(self):
        self.path = resolve_sdl()
        self.s = C.CDLL(str(self.path))
        signatures = {
            "SDL_SetHint": (C.c_bool, [C.c_char_p, C.c_char_p]),
            "SDL_Init": (C.c_bool, [C.c_uint32]),
            "SDL_GetError": (C.c_char_p, []),
            "SDL_UpdateJoysticks": (None, []),
            "SDL_GetJoysticks": (C.POINTER(C.c_uint32), [C.POINTER(C.c_int)]),
            "SDL_OpenJoystick": (C.c_void_p, [C.c_uint32]),
            "SDL_JoystickConnected": (C.c_bool, [C.c_void_p]),
            "SDL_GetJoystickProperties": (C.c_uint32, [C.c_void_p]),
            "SDL_GetBooleanProperty": (C.c_bool, [C.c_uint32, C.c_char_p, C.c_bool]),
            "SDL_GetJoystickAxis": (C.c_int16, [C.c_void_p, C.c_int]),
            "SDL_GetJoystickButton": (C.c_bool, [C.c_void_p, C.c_int]),
            "SDL_GetJoystickHat": (C.c_uint8, [C.c_void_p, C.c_int]),
            "SDL_RumbleJoystick": (C.c_bool, [C.c_void_p, C.c_uint16, C.c_uint16, C.c_uint32]),
            "SDL_CloseJoystick": (None, [C.c_void_p]),
            "SDL_free": (None, [C.c_void_p]),
            "SDL_Quit": (None, []),
        }
        for suffix in ("Name", "Path"):
            signatures["SDL_GetJoystick" + suffix] = (C.c_char_p, [C.c_void_p])
        for suffix in ("Vendor", "Product"):
            signatures["SDL_GetJoystick" + suffix] = (C.c_uint16, [C.c_void_p])
        for suffix in ("Axes", "Buttons", "Hats"):
            signatures["SDL_GetNumJoystick" + suffix] = (C.c_int, [C.c_void_p])
        for name, (result, arguments) in signatures.items():
            function = getattr(self.s, name)
            function.restype, function.argtypes = result, arguments
        self.s.SDL_SetHint(b"SDL_JOYSTICK_ALLOW_BACKGROUND_EVENTS", b"1")
        if not self.s.SDL_Init(0x2200):
            raise RuntimeError(self.error())
        self.xinput = C.WinDLL("xinput1_4.dll")
        self.xinput.XInputGetState.argtypes = [W.DWORD, C.c_void_p]
        self.xinput.XInputGetState.restype = W.DWORD

    def error(self):
        return (self.s.SDL_GetError() or b"").decode("utf-8", errors="replace")

    def enumerate(self):
        count = C.c_int()
        ids = self.s.SDL_GetJoysticks(C.byref(count))
        try:
            return [ids[index] for index in range(count.value)] if ids else []
        finally:
            if ids:
                self.s.SDL_free(ids)

    def open(self, instance_id):
        joystick = self.s.SDL_OpenJoystick(instance_id)
        if not joystick:
            raise RuntimeError(self.error())
        return joystick

    def describe(self, joystick):
        props = self.s.SDL_GetJoystickProperties(joystick)
        return {
            "name": (self.s.SDL_GetJoystickName(joystick) or b"").decode("utf-8", errors="replace"),
            "vid": f"{self.s.SDL_GetJoystickVendor(joystick):04X}",
            "pid": f"{self.s.SDL_GetJoystickProduct(joystick):04X}",
            "rumble": self.s.SDL_GetBooleanProperty(props, b"SDL.joystick.cap.rumble", False),
            "trigger_rumble": self.s.SDL_GetBooleanProperty(props, b"SDL.joystick.cap.trigger_rumble", False),
            "axes_count": max(0, self.s.SDL_GetNumJoystickAxes(joystick)),
            "buttons_count": max(0, self.s.SDL_GetNumJoystickButtons(joystick)),
            "hats_count": max(0, self.s.SDL_GetNumJoystickHats(joystick)),
        }

    def sample(self, joystick, device):
        axes = [max(-1, self.s.SDL_GetJoystickAxis(joystick, index) / 32767)
                for index in range(device["axes_count"])]
        buttons = [int(self.s.SDL_GetJoystickButton(joystick, index))
                   for index in range(device["buttons_count"])]
        hats = [int(self.s.SDL_GetJoystickHat(joystick, index))
                for index in range(device["hats_count"])]
        return axes, buttons, hats

    def xinput_slots(self):
        slots = []
        for slot in range(4):
            state = C.create_string_buffer(16)
            if self.xinput.XInputGetState(slot, state) == 0:
                slots.append(slot)
        return slots

    def triggers(self, joystick):
        # XInput 设备使用真实扳机值，避免猜测不同 SDL 后端的原始轴顺序。
        path = (self.s.SDL_GetJoystickPath(joystick) or b"").decode("ascii", errors="replace")
        match = re.fullmatch(r"XInput#([0-3])", path)
        if not match:
            return None
        state = C.create_string_buffer(16)
        if self.xinput.XInputGetState(int(match[1]), state) != 0:
            return None
        return {"left": state.raw[6] / 255, "right": state.raw[7] / 255}

    def update(self):
        self.s.SDL_UpdateJoysticks()

    def connected(self, joystick):
        return self.s.SDL_JoystickConnected(joystick)

    def rumble(self, joystick, low, high, duration_ms):
        ok = self.s.SDL_RumbleJoystick(joystick, round(low * 65535), round(high * 65535), duration_ms)
        return {"ok": bool(ok), "error": None if ok else self.error()}

    def close(self, joystick):
        self.s.SDL_CloseJoystick(joystick)

    def quit(self):
        self.s.SDL_Quit()


class ControllerService:
    def __init__(self, driver_factory=NativeSDL):
        self.driver_factory = driver_factory
        self.lock = threading.Lock()
        self.commands = queue.Queue(maxsize=32)
        self.shutdown = threading.Event()
        self.ready = threading.Event()
        self.reader_time = time.monotonic()
        self.state = {"connected": False, "device": None, "axes": [], "buttons": [], "hats": [],
                      "timestamp": time.time(), "xinput_slots": [], "triggers": None, "error": None,
                      "vibration": {"supported": False, "last": None, "count": 0}, "events": []}
        self.events = deque(maxlen=30)
        self.thread = threading.Thread(target=self._run, name="controller", daemon=True)

    def start(self):
        self.thread.start()
        self.ready.wait(5)

    def snapshot(self, heartbeat=True):
        with self.lock:
            if heartbeat:
                self.reader_time = time.monotonic()
            return copy.deepcopy(self.state)

    def request(self, command, data=None, timeout=0.8):
        receipt = {"done": threading.Event()}
        try:
            self.commands.put_nowait((command, data, receipt))
        except queue.Full:
            return {"ok": False, "error": "手柄请求过于频繁"}
        if not receipt["done"].wait(timeout):
            # 过期震动不能在客户端认为失败后才补发。
            receipt["cancelled"] = True
            return {"ok": False, "error": "手柄后端响应超时"}
        return receipt["result"]

    def close(self):
        self.shutdown.set()
        self.thread.join(timeout=3)

    def _publish(self, **values):
        with self.lock:
            self.state.update(values)
            self.state["timestamp"] = time.time()
            self.state["events"] = list(self.events)

    def _run(self):
        driver = joystick = device = None
        active_until = 0
        active_priority = 0
        next_scan = next_xinput = 0
        last_command = 0
        try:
            driver = self.driver_factory()
            self.ready.set()
            while not self.shutdown.is_set():
                now = time.monotonic()
                driver.update()
                if joystick and not driver.connected(joystick):
                    driver.rumble(joystick, 0, 0, 0)
                    driver.close(joystick)
                    joystick = device = None
                    active_until = active_priority = 0
                    self._publish(connected=False, device=None, axes=[], buttons=[], hats=[], triggers=None,
                                  vibration={"supported": False, "last": None, "count": self.state["vibration"]["count"]})
                    next_scan = 0
                if not joystick and now >= next_scan:
                    next_scan = now + 1
                    available = driver.enumerate()
                    if available:
                        joystick = driver.open(available[0])
                        device = driver.describe(joystick)
                        self._publish(connected=True, device=device, error=None,
                                      vibration={"supported": device["rumble"], "last": None,
                                                 "count": self.state["vibration"]["count"]})
                if now >= next_xinput:
                    next_xinput = now + 1
                    self._publish(xinput_slots=driver.xinput_slots())
                if joystick:
                    axes, buttons, hats = driver.sample(joystick, device)
                    triggers = driver.triggers(joystick) if hasattr(driver, "triggers") else None
                    self._publish(axes=axes, buttons=buttons, hats=hats, triggers=triggers)
                while True:
                    try:
                        command, data, receipt = self.commands.get_nowait()
                    except queue.Empty:
                        break
                    if receipt.get("cancelled"):
                        receipt["result"] = {"ok": False, "error": "请求已过期"}
                        receipt["done"].set()
                        continue
                    if command == "stop":
                        result = driver.rumble(joystick, 0, 0, 0) if joystick else {"ok": True, "error": None}
                        active_until = active_priority = 0
                    elif not joystick:
                        result = {"ok": False, "error": "未连接手柄"}
                    else:
                        priority = EVENTS[data["event"]]
                        if active_until > now and priority < active_priority:
                            result = {"ok": False, "error": "更高优先级震动播放中", "suppressed": True}
                        elif now - last_command < 0.04 and data["event"] != "test":
                            result = {"ok": False, "error": "震动事件已合并", "suppressed": True}
                        else:
                            last_command = now
                            result = driver.rumble(joystick, data["low"], data["high"], data["duration_ms"])
                            record = {**data, **result, "time": time.time()}
                            self.events.appendleft(record)
                            self._publish(vibration={"supported": device["rumble"], "last": record,
                                                      "count": self.state["vibration"]["count"] + 1})
                            if result["ok"]:
                                active_until = now + data["duration_ms"] / 1000
                                active_priority = priority
                    receipt["result"] = result
                    receipt["done"].set()
                # 网页关闭/浏览器崩溃时也必须停止，不能依赖网页 unload 回调。
                if joystick and active_until and (now >= active_until or now - self.reader_time > 0.8):
                    driver.rumble(joystick, 0, 0, 0)
                    active_until = active_priority = 0
                self.shutdown.wait(0.01)
        except Exception as error:
            self._publish(connected=False, device=None, axes=[], buttons=[], hats=[], triggers=None, error=str(error),
                          vibration={"supported": False, "last": None, "count": self.state["vibration"]["count"]})
            self.ready.set()
        finally:
            if driver:
                try:
                    if joystick:
                        driver.rumble(joystick, 0, 0, 0)
                        driver.close(joystick)
                finally:
                    driver.quit()
