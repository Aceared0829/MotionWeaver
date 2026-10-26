"""导出清单的内容身份：旧批次兼容、声明后强制校验、篡改必须拒绝。"""

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from data.tools.prepare_unreal_dataset import prepare, verify_clip_identity


def write_batch(root, identity_mode):
    """写一个最小导出批次；identity_mode 控制 manifest 的内容身份形态。"""
    root.mkdir(parents=True, exist_ok=True)
    payload = b'{"schema_version": 2}'
    (root / "clip_00000.json").write_bytes(payload)
    manifest = {"schema_version": 2, "training_contract": "pose_only_root_authoritative",
                "clips": ["clip_00000.json"]}
    if identity_mode == "valid":
        manifest.update({
            "hash_algorithm": "sha1",
            "engine_version": "5.8.2-12345678+++UE5+Release-5.8",
            "plugin_version": "0.2.0",
            "clip_identity": [{"file": "clip_00000.json", "asset": "A.B",
                               "sha1": hashlib.sha1(payload).hexdigest()}],
        })
    elif identity_mode == "tampered":
        manifest.update({
            "hash_algorithm": "sha1",
            "clip_identity": [{"file": "clip_00000.json", "asset": "A.B",
                               "sha1": hashlib.sha1(b"other").hexdigest()}],
        })
    elif identity_mode == "unsupported_algorithm":
        manifest.update({
            "hash_algorithm": "md5",
            "clip_identity": [{"file": "clip_00000.json", "asset": "A.B",
                               "sha1": hashlib.sha1(payload).hexdigest()}],
        })
    elif identity_mode == "incomplete":
        manifest.update({"hash_algorithm": "sha1", "clip_identity": []})
    return manifest


class ClipIdentityTests(unittest.TestCase):
    def test_legacy_batch_without_identity_is_accepted(self):
        """插件升级前的批次没有 clip_identity，必须继续可用。"""
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            manifest = write_batch(root, "legacy")
            result = verify_clip_identity(root, manifest)
            self.assertFalse(result["declared"])
            self.assertEqual(result["verified"], 0)

    def test_declared_identity_is_verified(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            manifest = write_batch(root, "valid")
            result = verify_clip_identity(root, manifest)
            self.assertTrue(result["declared"])
            self.assertEqual(result["verified"], 1)
            self.assertEqual(result["algorithm"], "sha1")
            self.assertEqual(result["plugin_version"], "0.2.0")

    def test_tampered_clip_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            manifest = write_batch(root, "tampered")
            with self.assertRaisesRegex(ValueError, "内容与导出清单不一致"):
                verify_clip_identity(root, manifest)

    def test_unsupported_algorithm_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            manifest = write_batch(root, "unsupported_algorithm")
            with self.assertRaisesRegex(ValueError, "不支持的导出内容摘要算法"):
                verify_clip_identity(root, manifest)

    def test_incomplete_identity_is_rejected(self):
        """声明了身份却不覆盖全部片段，会让部分文件绕过校验，必须拒绝。"""
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            manifest = write_batch(root, "incomplete")
            with self.assertRaisesRegex(ValueError, "缺少内容身份"):
                verify_clip_identity(root, manifest)

    def test_prepare_rejects_tampered_before_creating_output(self):
        """校验失败时不得留下半成品目录。"""
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "batch"
            manifest = write_batch(root, "tampered")
            output = Path(temporary) / "out"
            with self.assertRaises(ValueError):
                prepare(root, output, input_manifest=manifest)
            self.assertFalse(output.exists())


if __name__ == "__main__":
    unittest.main()
