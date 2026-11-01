"""与 GitHub Windows Runner 共用的便携发行包构建入口。"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import zipfile

from fetch_sdl import fetch

ROOT = Path(__file__).resolve().parents[1]
PREFIX = "Beitong-A1S2-Rumble-Toolbox"


def artifact_name(version):
    return f"{PREFIX}-v{version}-windows-x64.zip"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", default=(ROOT / "VERSION").read_text().strip())
    args = parser.parse_args()
    if not re.fullmatch(r"\d+\.\d+\.\d+", args.version):
        parser.error("版本必须为 MAJOR.MINOR.PATCH")
    fetch()
    generated = ROOT / ".cache" / "packaging"
    generated.mkdir(parents=True, exist_ok=True)
    metadata = generated / "build_metadata.json"
    metadata.write_text(json.dumps({"version": args.version}), encoding="utf-8")
    parts = tuple(map(int, args.version.split("."))) + (0,)
    resource = generated / "file_version.txt"
    strings = {"CompanyName": "Community project", "FileDescription": "Beitong A1S2 Rumble Toolbox (Community)",
               "FileVersion": args.version, "ProductVersion": args.version,
               "ProductName": "Beitong A1S2 Rumble Toolbox", "OriginalFilename": "BeitongRumbleToolbox.exe"}
    resource.write_text("VSVersionInfo(ffi=FixedFileInfo(filevers=" + repr(parts) + ",prodvers=" + repr(parts)
                        + ",mask=0x3f,flags=0,OS=0x40004,fileType=1,subtype=0,date=(0,0)),kids=[StringFileInfo([StringTable('040904B0',["
                        + ",".join(f"StringStruct({key!r},{value!r})" for key, value in strings.items())
                        + "])]),VarFileInfo([VarStruct('Translation',[1033,1200])])])", encoding="utf-8")
    command = [sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--onefile", "--windowed",
               "--name", "BeitongRumbleToolbox", "--version-file", str(resource),
               "--distpath", str(ROOT / "build" / "binary"), "--workpath", str(ROOT / "build" / "pyinstaller"),
               "--specpath", str(generated)]
    for source, destination in [(ROOT / "vendor" / "SDL3.dll", "vendor")]:
        command += ["--add-binary", str(source) + os.pathsep + destination]
    data = [(ROOT / "web", "web"), (ROOT / "VERSION", "."), (metadata, "."),
            (ROOT / "LICENSE", "licenses"), (ROOT / "vendor" / "SDL-LICENSE.txt", "licenses")]
    python_home = Path(sys.base_prefix)
    for path, target in [(python_home / "LICENSE.txt", "licenses/Python"),
                         (ROOT / "vendor" / "Tcl-LICENSE.txt", "licenses/Tcl"),
                         (python_home / "tcl" / "tk8.6" / "license.terms", "licenses/Tk")]:
        if not path.is_file():
            raise FileNotFoundError(f"缺少运行时许可文件：{path}")
        data.append((path, target))
    for source, destination in data:
        command += ["--add-data", str(source) + os.pathsep + destination]
    command.append(str(ROOT / "toolkit.py"))
    subprocess.run(command, cwd=ROOT, check=True)
    release = ROOT / "dist"
    release.mkdir(exist_ok=True)
    exe = ROOT / "build" / "binary" / "BeitongRumbleToolbox.exe"
    archive = release / artifact_name(args.version)
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as output:
        output.write(exe, "BeitongRumbleToolbox.exe")
        for source, destination in [(ROOT / "packaging" / "使用说明.txt", "使用说明.txt"),
                                    (ROOT / "LICENSE", "LICENSE"),
                                    (ROOT / "THIRD_PARTY_NOTICES.md", "THIRD_PARTY_NOTICES.md"),
                                    (ROOT / "vendor" / "SDL-LICENSE.txt", "licenses/SDL-LICENSE.txt"),
                                    (python_home / "LICENSE.txt", "licenses/Python-LICENSE.txt"),
                                    (ROOT / "vendor" / "Tcl-LICENSE.txt", "licenses/Tcl-LICENSE.txt"),
                                    (python_home / "tcl" / "tk8.6" / "license.terms", "licenses/Tk-LICENSE.txt")]:
            output.write(source, destination)
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    (release / "SHA256SUMS.txt").write_text(f"{digest}  {archive.name}\n", encoding="ascii")
    print(json.dumps({"version": args.version, "artifact": str(archive), "sha256": digest,
                      "bytes": archive.stat().st_size, "signed": False}))


if __name__ == "__main__":
    main()
