"""北通 BTP-A1S2 社区震动工具箱：普通用户启动入口。"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import signal
import sys
import threading
import time
from urllib.request import urlopen
import webbrowser

from app_version import APPLICATION_ID, resource_root, version
from controller_backend import ControllerService, NativeSDL
from server import LocalHTTPServer, make_handler
from sdl_runtime import resolve_sdl


def self_test(rumble=False):
    runtime = resolve_sdl()
    root = resource_root()
    required = ["web/index.html", "web/game.js", "web/input.js", "web/physics.js", "web/styles.css", "VERSION"]
    missing = [name for name in required if not (root / name).is_file()]
    if missing:
        raise RuntimeError("发行包资源不完整：" + ", ".join(missing))
    driver = NativeSDL()
    devices = []
    try:
        driver.update()
        for identifier in driver.enumerate():
            joystick = driver.open(identifier)
            try:
                device = driver.describe(joystick)
                driver.sample(joystick, device)
                if rumble:
                    device["rumble_test"] = driver.rumble(joystick, 0.18, 0.18, 150)
                    time.sleep(0.16)
                devices.append(device)
            finally:
                if rumble:
                    driver.rumble(joystick, 0, 0, 0)
                driver.close(joystick)
        slots = driver.xinput_slots()
    finally:
        driver.quit()
    return {"ok": True, "application": APPLICATION_ID, "version": version(),
            "runtime": {"name": runtime.name, "sha256": hashlib.sha256(runtime.read_bytes()).hexdigest(),
                        "bundled": runtime.is_relative_to(root)},
            "resources": "complete", "devices": devices, "xinput_slots": slots,
            "note": "未接入硬件时只验证启动和资源；震感需人工确认。"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=18765)
    parser.add_argument("--headless", action="store_true", help="不打开窗口或浏览器")
    parser.add_argument("--lifetime", type=float, default=0, help="自动退出秒数，供发行验证")
    parser.add_argument("--ready-file", type=Path)
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--rumble", action="store_true", help="自检时发送一次低强度短震动")
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    if not 1 <= args.port <= 65535 or args.lifetime < 0:
        parser.error("端口或运行时长无效")
    if args.self_test:
        try:
            report, result = self_test(args.rumble), 0
        except Exception as error:
            report, result = {"ok": False, "version": version(), "error": str(error)}, 1
        payload = json.dumps(report, ensure_ascii=False, indent=2)
        target = args.report
        if target is None and not sys.stdout:
            target = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "BeitongRumbleToolbox" / "self-test.json"
        if target:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(payload, encoding="utf-8")
        if sys.stdout:
            print(payload)
        return result

    url = f"http://127.0.0.1:{args.port}"
    controller = ControllerService()
    try:
        server = LocalHTTPServer(("127.0.0.1", args.port), make_handler(controller, args.port))
    except OSError:
        with urlopen(url + "/api/state", timeout=2) as response:
            existing = json.load(response)
        if existing.get("application") != APPLICATION_ID:
            raise RuntimeError("端口被其他程序占用，请使用 --port 更换端口")
        if not args.headless:
            webbrowser.open(url)
        return 0
    controller.start()
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.1}, daemon=True)
    thread.start()
    stop = threading.Event()
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    deadline = time.monotonic() + args.lifetime if args.lifetime else None
    if args.ready_file:
        args.ready_file.parent.mkdir(parents=True, exist_ok=True)
        args.ready_file.write_text(json.dumps({"url": url, "pid": os.getpid(), "version": version()}), encoding="utf-8")
    try:
        if args.headless:
            while not stop.wait(0.1):
                if deadline and time.monotonic() >= deadline:
                    break
        else:
            import tkinter as tk
            from tkinter import ttk
            window = tk.Tk()
            window.title(f"北通震动工具箱 · 社区版 {version()}")
            window.geometry("620x490")
            window.minsize(600, 470)
            window.configure(background="#f1f5f9")
            frame = ttk.Frame(window, padding=26)
            frame.pack(fill="both", expand=True)
            ttk.Label(frame, text="北通 BTP-A1S2 震动工具箱", font=("Microsoft YaHei UI", 18, "bold")).pack(anchor="w")
            ttk.Label(frame, text=f"社区便携版 {version()} · Windows 10/11 x64", padding=(0, 6)).pack(anchor="w")
            status = tk.StringVar(value="正在检测手柄…")
            ttk.Label(frame, textvariable=status, font=("Microsoft YaHei UI", 11), wraplength=560, padding=(0, 14)).pack(anchor="w")
            ttk.Label(frame, text="已验证机型：BTP-A1S2 纯有线版\n外观：LOGO / TURBO / SHIFT 与 1～4 号指示灯\n\n若显示 BFM：保持 USB 连接，长按 LOGO，\n灯停止闪烁时松开，循环至 1 号灯单独常亮（PC360）。\n\n进入验证页面后，先测试左右马达，再试驾赛车。\n本工具不安装系统驱动，也不自动切换硬件模式。", wraplength=560, justify="left").pack(anchor="w", pady=8)
            ttk.Button(frame, text="打开诊断与赛车验证", command=lambda: webbrowser.open(url)).pack(fill="x", pady=(16, 8))
            ttk.Button(frame, text="停止震动并退出工具", command=window.destroy).pack(fill="x")
            ttk.Label(frame, text="非北通官方软件。请仅连接待测手柄，避免多设备误选。", foreground="#64748b").pack(anchor="w", pady=(14, 0))

            def refresh():
                current = controller.snapshot(heartbeat=False)
                device = current.get("device") or {}
                if current.get("error"):
                    text = "输入服务异常：" + current["error"]
                elif current["connected"]:
                    text = device.get("name", "手柄") + ("\n震动接口可用，请到验证页面确认实际震感。" if device.get("rumble") else "\n当前模式不支持标准震动，请检查 PC360 模式。")
                else:
                    text = "尚未检测到手柄。请接入 USB 后稍候。"
                status.set(text)
                if stop.is_set() or deadline and time.monotonic() >= deadline:
                    window.destroy()
                else:
                    window.after(500, refresh)
            refresh()
            window.after(400, lambda: webbrowser.open(url))
            window.mainloop()
    finally:
        controller.request("stop")
        server.shutdown()
        server.server_close()
        controller.close()
        thread.join(timeout=2)
    return 0


if __name__ == "__main__":
    if sys.stdout:
        sys.stdout.reconfigure(encoding="utf-8")
    try:
        raise SystemExit(main())
    except Exception as error:
        if sys.stderr:
            print(str(error), file=sys.stderr)
        elif "--headless" not in sys.argv and "--self-test" not in sys.argv:
            import tkinter.messagebox
            tkinter.messagebox.showerror("北通震动工具箱", str(error))
        raise SystemExit(1)
