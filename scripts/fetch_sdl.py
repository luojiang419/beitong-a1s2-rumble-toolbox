"""获取已固定版本/哈希的官方 SDL x64 运行库。"""
import hashlib
import io
import json
from pathlib import Path
from urllib.request import Request, urlopen
import zipfile

ROOT = Path(__file__).resolve().parents[1]


def fetch():
    dependencies = json.loads((ROOT / "dependencies.json").read_text(encoding="utf-8"))
    dependency = dependencies["sdl"]
    cached = ROOT / ".cache" / "sdl" / f"SDL3-{dependency['version']}-win32-x64.zip"
    if not cached.is_file():
        cached.parent.mkdir(parents=True, exist_ok=True)
        with urlopen(Request(dependency["url"], headers={"User-Agent": "Beitong-Community-Toolbox"}), timeout=60) as response:
            payload = response.read(20 * 1024 * 1024 + 1)
        if len(payload) > 20 * 1024 * 1024:
            raise ValueError("SDL 下载超出预期大小")
    else:
        payload = cached.read_bytes()
    if hashlib.sha256(payload).hexdigest() != dependency["sha256"]:
        raise ValueError("SDL 压缩包 SHA-256 不匹配，未提取或加载")
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        dll, license_text = archive.read("SDL3.dll"), archive.read("LICENSE.txt")
    destination = ROOT / "vendor"
    destination.mkdir(exist_ok=True)
    (destination / "SDL3.dll").write_bytes(dll)
    (destination / "SDL-LICENSE.txt").write_bytes(license_text)
    license_info = dependencies["tcl_license"]
    license_path = ROOT / ".cache" / "licenses" / "Tcl-LICENSE.txt"
    if license_path.is_file():
        tcl_license = license_path.read_bytes()
    else:
        with urlopen(license_info["url"], timeout=30) as response:
            tcl_license = response.read(1024 * 1024)
    if hashlib.sha256(tcl_license).hexdigest() != license_info["sha256"]:
        raise ValueError("Tcl 许可证哈希不匹配")
    (destination / "Tcl-LICENSE.txt").write_bytes(tcl_license)
    license_path.parent.mkdir(parents=True, exist_ok=True)
    license_path.write_bytes(tcl_license)
    cached.write_bytes(payload)
    return destination / "SDL3.dll"


if __name__ == "__main__":
    print(fetch())
