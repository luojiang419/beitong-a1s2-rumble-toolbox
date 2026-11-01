"""发行包优先使用固定版本的 SDL，不依赖 Steam，也不分发 Steam DLL。"""
from pathlib import Path
import winreg

from app_version import resource_root


def resolve_sdl(explicit=None):
    if explicit:
        candidate = Path(explicit).resolve()
        if not candidate.is_file():
            raise FileNotFoundError(f"找不到指定 SDL3.dll：{candidate}")
        return candidate
    root = resource_root()
    for candidate in (root / "vendor" / "SDL3.dll", root / "SDL3.dll"):
        if candidate.is_file():
            return candidate
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam") as key:
            steam = Path(winreg.QueryValueEx(key, "SteamPath")[0]) / "SDL3.dll"
        if steam.is_file():
            return steam
    except OSError:
        pass
    raise FileNotFoundError("缺少 SDL3.dll。请下载完整发行包；源码运行请先执行 python scripts/fetch_sdl.py。")
