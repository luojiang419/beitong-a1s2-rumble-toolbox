"""解析补丁版本，并通过 Draft → 上传/复核 → 正式 Release 完成发布。"""
import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess
import tempfile


def next_version(latest, minimum, sha):
    def parse(value):
        if not isinstance(value, str) or not re.fullmatch(r"v?\d+\.\d+\.\d+", value):
            raise ValueError("只接受稳定 MAJOR.MINOR.PATCH 版本")
        return tuple(map(int, value.removeprefix("v").split(".")))
    floor = parse(minimum)
    if latest is None:
        value, skip = floor, False
    else:
        previous = parse(latest["tag_name"])
        skip = latest.get("target_commitish") == sha
        value = previous if skip else max(floor, (previous[0], previous[1], previous[2] + 1))
    return {"version": ".".join(map(str, value)), "skip": skip}


def gh(*args, missing=False):
    result = subprocess.run(["gh", *args], capture_output=True, text=True, encoding="utf-8")
    if result.returncode:
        if missing and "404" in result.stderr:
            return None
        raise RuntimeError(result.stderr.strip())
    return json.loads(result.stdout) if result.stdout.strip() else None


def api_body(endpoint, body, method):
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "body.json"
        path.write_text(json.dumps(body, ensure_ascii=False), encoding="utf-8")
        return gh("api", endpoint, "--method", method, "--input", str(path))


def verify_assets(release, paths):
    actual = {asset["name"]: asset for asset in release["assets"]}
    if len(actual) != len(release["assets"]) or set(actual) != {p.name for p in paths}:
        raise ValueError("远端资产清单不一致")
    for path in paths:
        asset = actual[path.name]
        digest = "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()
        if asset["state"] != "uploaded" or asset["size"] != path.stat().st_size or asset.get("digest") != digest:
            raise ValueError("远端资产状态、大小或哈希不一致：" + path.name)


def publish(repo, version, sha, directory):
    tag = "v" + version
    base = f"repos/{repo}/releases"
    paths = [directory / f"Beitong-A1S2-Rumble-Toolbox-{tag}-windows-x64.zip", directory / "SHA256SUMS.txt"]
    if not all(path.is_file() for path in paths):
        raise FileNotFoundError("缺少待发布资产")
    existing = gh("api", base + "/tags/" + tag, missing=True)
    if existing:
        if existing["target_commitish"] == sha and not existing["draft"] and not existing["prerelease"]:
            verify_assets(existing, paths)
            print(existing["html_url"])
            return
        raise ValueError("标签已对应其他发布状态；未覆盖既有版本")
    body = (f"北通 BTP-A1S2 纯有线版社区震动诊断与赛车验证工具。\n\n"
            f"下载 ZIP，完整解压并运行 BeitongRumbleToolbox.exe。Windows 10/11 x64；不需要 Python 或 Steam。\n\n"
            f"该工具指导切换 PC360 并验证标准震动，不是官方底层驱动，不刷固件。已验证样机与初始赛车震感；不承诺所有批次或 Steam 游戏效果。\n\n"
            f"本版未代码签名。请用 SHA256SUMS.txt 校验下载。托管 CI 不连接实体手柄，硬件验收范围见 README。\n\n"
            f"版本：{version}；源码提交：{sha}。")
    draft = api_body(base, {"tag_name": tag, "target_commitish": sha, "name": "北通震动工具箱 " + tag,
                            "body": body, "draft": True, "prerelease": False}, "POST")
    published = False
    try:
        subprocess.run(["gh", "release", "upload", tag, *map(str, paths), "--repo", repo], check=True)
        remote = gh("api", base + "/" + str(draft["id"]))
        verify_assets(remote, paths)
        with tempfile.TemporaryDirectory() as temporary:
            subprocess.run(["gh", "release", "download", tag, "--repo", repo, "--pattern", "SHA256SUMS.txt", "--dir", temporary], check=True)
            if (Path(temporary) / "SHA256SUMS.txt").read_bytes() != paths[1].read_bytes():
                raise ValueError("远端校验文件下载内容不一致")
        api_body(base + "/" + str(draft["id"]), {"draft": False, "make_latest": "true"}, "PATCH")
        published = True
        latest = gh("api", base + "/latest")
        if latest["tag_name"] != tag or latest["draft"] or latest["prerelease"] or latest["target_commitish"] != sha:
            raise ValueError("Latest Release 验收失败")
        verify_assets(latest, paths)
        print(latest["html_url"])
    except Exception:
        if not published:
            current = gh("api", base + "/" + str(draft["id"]))
            if current["draft"]:
                gh("api", base + "/" + str(draft["id"]), "--method", "DELETE")
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["resolve", "publish"])
    parser.add_argument("--repo", required=True)
    parser.add_argument("--sha", required=True)
    parser.add_argument("--version")
    parser.add_argument("--minimum", default=Path("VERSION").read_text().strip())
    parser.add_argument("--directory", type=Path, default=Path("dist"))
    args = parser.parse_args()
    if args.action == "resolve":
        result = next_version(gh("api", f"repos/{args.repo}/releases/latest", missing=True), args.minimum, args.sha)
        import os
        if os.environ.get("GITHUB_OUTPUT"):
            with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as output:
                output.write(f"version={result['version']}\nskip={str(result['skip']).lower()}\n")
        print(json.dumps(result))
    else:
        if not args.version or not re.fullmatch(r"\d+\.\d+\.\d+", args.version):
            parser.error("publish 需要 --version MAJOR.MINOR.PATCH")
        publish(args.repo, args.version, args.sha, args.directory)


if __name__ == "__main__":
    main()
