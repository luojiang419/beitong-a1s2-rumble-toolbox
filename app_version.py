"""统一源码与打包程序的版本及资源目录。"""
import json
from pathlib import Path
import sys

APPLICATION_ID = "beitong-a1s2-rumble-toolbox"


def resource_root():
    return Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))


def version():
    root = resource_root()
    metadata = root / "build_metadata.json"
    if metadata.is_file():
        return json.loads(metadata.read_text(encoding="utf-8"))["version"]
    return (root / "VERSION").read_text(encoding="utf-8").strip()
