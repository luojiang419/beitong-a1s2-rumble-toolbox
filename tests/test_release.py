import unittest
from unittest.mock import patch
from pathlib import Path
import tempfile
from scripts.release import next_version, verify_assets
from sdl_runtime import resolve_sdl


class ReleaseTests(unittest.TestCase):
    def test_first_floor_increment_and_idempotence(self):
        self.assertEqual(next_version(None, "1.0.0", "abc"), {"version": "1.0.0", "skip": False})
        self.assertEqual(next_version({"tag_name": "v0.1.0"}, "1.0.0", "abc")["version"], "1.0.0")
        self.assertEqual(next_version({"tag_name": "v1.0.2"}, "1.0.0", "abc")["version"], "1.0.3")
        self.assertEqual(next_version({"tag_name": "v1.0.2", "target_commitish": "abc"}, "1.0.0", "abc"), {"version": "1.0.2", "skip": True})

    def test_invalid_or_prerelease_version_rejected(self):
        for tag in ("main", "v1.2", "1.2.3-beta", ""):
            with self.subTest(tag=tag), self.assertRaises(ValueError):
                next_version({"tag_name": tag}, "1.0.0", "abc")

    def test_bundled_sdl_wins_without_steam_registry(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "vendor").mkdir()
            expected = root / "vendor/SDL3.dll"
            expected.write_bytes(b"fixture-not-executed")
            with patch("sdl_runtime.resource_root", return_value=root), patch("sdl_runtime.winreg.OpenKey", side_effect=AssertionError("不应读取 Steam")):
                self.assertEqual(resolve_sdl(), expected)

    def test_corrupt_remote_assets_rejected(self):
        import hashlib
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "asset.zip"
            path.write_bytes(b"payload")
            item = {"name": path.name, "state": "uploaded", "size": 7,
                    "digest": "sha256:" + hashlib.sha256(b"payload").hexdigest()}
            verify_assets({"assets": [item]}, [path])
            for key, bad in (("size", 6), ("digest", "sha256:bad"), ("state", "new")):
                with self.subTest(key=key), self.assertRaises(ValueError):
                    verify_assets({"assets": [{**item, key: bad}]}, [path])
