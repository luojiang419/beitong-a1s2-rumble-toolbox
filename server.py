"""午夜试车场：仅监听本机的静态网页与原生手柄事件服务。"""
import argparse
import json
import mimetypes
from pathlib import Path
import signal
import socket
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import unquote, urlsplit
from urllib.request import urlopen

from controller_backend import ControllerService, validate_rumble
from app_version import APPLICATION_ID, resource_root, version

ROOT = resource_root()


class LocalHTTPServer(ThreadingHTTPServer):
    # Windows 的 SO_REUSEADDR 会允许两个服务争用同一端口，必须独占绑定。
    allow_reuse_address = False

    def server_bind(self):
        if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        super().server_bind()


def make_handler(controller, port):
    hosts = {f"127.0.0.1:{port}", f"localhost:{port}"}
    origins = {f"http://{host}" for host in hosts}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def allowed(self):
            if self.headers.get("Host") not in hosts:
                self.send_json(403, {"ok": False, "error": "只允许本机地址"})
                return False
            if self.headers.get("Origin") not in origins | {None}:
                self.send_json(403, {"ok": False, "error": "不允许跨站控制手柄"})
                return False
            return True

        def send_json(self, status, value):
            body = json.dumps(value, ensure_ascii=False, allow_nan=False).encode("utf-8")
            self.send_body(status, body, "application/json; charset=utf-8")

        def send_body(self, status, body, content_type):
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; img-src 'self' data:; object-src 'none'; base-uri 'none'; frame-ancestors 'none'")
            self.end_headers()
            try:
                self.wfile.write(body)
            except (ConnectionAbortedError, ConnectionResetError, BrokenPipeError):
                pass

        def do_GET(self):
            if not self.allowed():
                return
            path = unquote(urlsplit(self.path).path)
            if path == "/api/state":
                self.send_json(200, {**controller.snapshot(), "application": APPLICATION_ID, "version": version()})
                return
            if path.startswith("/api/"):
                self.send_json(404, {"ok": False, "error": "接口不存在"})
                return
            candidate = (ROOT / "web" / ("index.html" if path == "/" else path.lstrip("/"))).resolve()
            if not candidate.is_relative_to((ROOT / "web").resolve()) or not candidate.is_file():
                self.send_json(404, {"ok": False, "error": "文件不存在"})
                return
            content_type = {".js": "text/javascript", ".css": "text/css", ".html": "text/html"}.get(candidate.suffix)
            content_type = content_type or mimetypes.guess_type(candidate.name)[0] or "application/octet-stream"
            self.send_body(200, candidate.read_bytes(), content_type + ("; charset=utf-8" if content_type.startswith("text/") else ""))

        def do_POST(self):
            if not self.allowed():
                return
            if self.headers.get("Content-Type", "").split(";")[0] != "application/json":
                self.send_json(415, {"ok": False, "error": "需要 JSON 请求"})
                return
            try:
                size = int(self.headers.get("Content-Length", "0"))
                if size < 0 or size > 4096:
                    raise ValueError("请求过大")
                data = json.loads(self.rfile.read(size) or b"{}")
                path = urlsplit(self.path).path
                if path == "/api/rumble":
                    result = controller.request("rumble", validate_rumble(data))
                elif path == "/api/stop":
                    result = controller.request("stop")
                else:
                    self.send_json(404, {"ok": False, "error": "接口不存在"})
                    return
                self.send_json(200, result)
            except (ValueError, UnicodeError) as error:
                self.send_json(400, {"ok": False, "error": str(error)})

    return Handler


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--open", action="store_true", help="服务就绪后打开浏览器")
    args = parser.parse_args()
    if not 1 <= args.port <= 65535:
        parser.error("端口必须在 1～65535 之间")
    controller = ControllerService()
    try:
        server = LocalHTTPServer(("127.0.0.1", args.port), make_handler(controller, args.port))
    except OSError:
        try:
            with urlopen(f"http://127.0.0.1:{args.port}/api/state", timeout=2) as response:
                existing = json.load(response)
            if not isinstance(existing, dict) or "vibration" not in existing or "axes" not in existing:
                raise ValueError("该端口不是赛车服务")
        except Exception:
            parser.error("端口已被其他程序占用，请用 --port 指定其他端口")
        print(f"赛车服务已在 http://127.0.0.1:{args.port} 运行", flush=True)
        if args.open:
            webbrowser.open(f"http://127.0.0.1:{args.port}")
        return
    controller.start()
    server.daemon_threads = True
    print(f"午夜试车场 http://127.0.0.1:{args.port}", flush=True)
    if args.open:
        webbrowser.open(f"http://127.0.0.1:{args.port}")

    def stop_server(*_):
        threading.Thread(target=server.shutdown, daemon=True).start()

    signal.signal(signal.SIGINT, stop_server)
    signal.signal(signal.SIGTERM, stop_server)
    try:
        server.serve_forever(poll_interval=0.2)
    finally:
        server.server_close()
        controller.close()


if __name__ == "__main__":
    import sys
    if sys.stdout:
        sys.stdout.reconfigure(encoding="utf-8")
    main()
