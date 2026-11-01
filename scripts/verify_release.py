"""解压实际发行包，验证 PE 版本、独立运行、页面/API 与退出。"""
import argparse
import hashlib
import json
from pathlib import Path
import socket
import subprocess
import tempfile
import time
from urllib.error import HTTPError
from urllib.request import Request, urlopen
import zipfile

import pefile
from build_windows import ROOT, artifact_name


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", required=True)
    parser.add_argument("--hardware-rumble", action="store_true")
    args = parser.parse_args()
    package = ROOT / "dist" / artifact_name(args.version)
    digest = hashlib.sha256(package.read_bytes()).hexdigest()
    expected = (ROOT / "dist" / "SHA256SUMS.txt").read_text().strip()
    assert expected == f"{digest}  {package.name}", "发行包校验文件不一致"
    with tempfile.TemporaryDirectory(prefix="beitong-verify-") as directory:
        root = Path(directory) / "中文 空格路径"
        root.mkdir()
        with zipfile.ZipFile(package) as archive:
            names = set(archive.namelist())
            required = {"BeitongRumbleToolbox.exe", "使用说明.txt", "LICENSE", "THIRD_PARTY_NOTICES.md",
                        "licenses/SDL-LICENSE.txt", "licenses/Python-LICENSE.txt", "licenses/Tcl-LICENSE.txt", "licenses/Tk-LICENSE.txt"}
            assert names == required, "发行包文件清单不符"
            for name in names:
                assert not Path(name).is_absolute() and ".." not in Path(name).parts
            archive.extractall(root)
        exe = root / "BeitongRumbleToolbox.exe"
        with pefile.PE(str(exe)) as pe:
            assert pe.FILE_HEADER.Machine == 0x8664, "不是 Windows x64 程序"
            assert pe.OPTIONAL_HEADER.Subsystem == 2, "不是图形启动程序"
            info = {}
            for section in pe.FileInfo:
                for entry in section:
                    for table in getattr(entry, "StringTable", []):
                        info.update(table.entries)
            assert info[b"ProductVersion"].decode() == args.version
            assert pe.OPTIONAL_HEADER.DATA_DIRECTORY[4].Size == 0, "签名状态改变，需要更新发布契约"
        report_path = root / "selftest.json"
        subprocess.run([str(exe), "--self-test", "--report", str(report_path)], check=True, timeout=60,
                       creationflags=subprocess.CREATE_NO_WINDOW)
        report = json.loads(report_path.read_text(encoding="utf-8"))
        assert report["ok"] and report["version"] == args.version and report["runtime"]["bundled"]
        assert report["resources"] == "complete"
        with socket.socket() as reservation:
            reservation.bind(("127.0.0.1", 0))
            port = reservation.getsockname()[1]
        ready = root / "ready.json"
        child = subprocess.Popen([str(exe), "--headless", "--port", str(port), "--lifetime", "7", "--ready-file", str(ready)],
                                 creationflags=subprocess.CREATE_NO_WINDOW)
        deadline = time.monotonic() + 45
        while not ready.exists() and time.monotonic() < deadline and child.poll() is None:
            time.sleep(0.1)
        assert ready.exists(), "打包程序未启动本地服务"
        url = json.loads(ready.read_text())["url"]
        for path in ("/", "/game.js", "/input.js", "/physics.js", "/styles.css"):
            with urlopen(url + path, timeout=5) as response:
                assert response.status == 200 and len(response.read()) > 100
        with urlopen(url + "/api/state", timeout=5) as response:
            state = json.load(response)
        assert state["application"] == "beitong-a1s2-rumble-toolbox" and state["version"] == args.version

        def post(path, data):
            request = Request(url + path, json.dumps(data).encode(), {"Content-Type": "application/json"})
            with urlopen(request, timeout=5) as response:
                return json.load(response)

        try:
            post("/api/rumble", {"event": "test", "low": 2, "high": 0, "duration_ms": 200})
            raise AssertionError("无效强度未被拒绝")
        except HTTPError as error:
            assert error.code == 400
        hardware = "not-run-on-this-machine"
        if args.hardware_rumble:
            assert state["connected"] and state["device"]["rumble"], "未发现支持震动的实机"
            for low, high in ((0.18, 0), (0, 0.18), (0.15, 0.15)):
                assert post("/api/rumble", {"event": "test", "low": low, "high": high, "duration_ms": 120})["ok"]
                time.sleep(0.16)
            hardware = "three-nonzero-requests-accepted-physical-feel-not-asserted"
        assert post("/api/stop", {})["ok"]
        assert child.wait(timeout=20) == 0, "程序未按计划正常退出"
    result = {"version": args.version, "artifact": package.name, "sha256": digest,
              "bytes": package.stat().st_size, "portable_smoke": "passed", "hardware": hardware, "signed": False}
    (ROOT / ".cache" / "release-verification.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps(result))


if __name__ == "__main__":
    main()
