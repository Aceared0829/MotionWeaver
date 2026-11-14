"""被拒条目的验收批次组装：路径本地化、哈希校验与「不谎报审计结论」。"""

import csv
import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from data.tools.build_rejected_review_library import build
from data.tools.native_review_store import load_native_review_inputs


def fixture(folder, *, tamper=False):
    """造一份最小可用的「源批次 + 训练视图 rejected.jsonl」。"""
    source_root = folder / "source"
    clip_dir = "abc123"
    raw = source_root / "raw/soma_uniform/batch/clips" / clip_dir
    processed = source_root / "processed/soma_uniform/batch/clips" / clip_dir
    for target in (raw, processed):
        target.mkdir(parents=True)
        (target / "motion.json").write_text('{"kind": "x"}', encoding="utf-8")
        payload = b"arrays" if target is processed and not tamper else b"arrays-bytes"
        (target / "arrays.bin").write_bytes(payload)

    def digests(target, *, spoof=False):
        out = {name: hashlib.sha256((target / name).read_bytes()).hexdigest()
               for name in ("motion.json", "arrays.bin")}
        if spoof:
            # 记录一个与实际内容不符的哈希，模拟「源批次被改过 / 复制过程中损坏」。
            out["arrays.bin"] = "f" * 64
        return out

    report = source_root / "reports/soma_uniform/batch"
    report.mkdir(parents=True)
    record = {
        "id": clip_dir,
        "source_id": "soma_uniform/bvh/clip.bvh",
        "name": "clip",
        "status": "normalized",
        "frames": 69, "joints": 78, "fps": 120.00480019200768, "duration": 0.575,
        "raw": "E:\\MotionWeaverData\\raw\\soma_uniform\\batch\\clips\\" + clip_dir,
        "processed": "E:\\MotionWeaverData\\processed\\soma_uniform\\batch\\clips\\" + clip_dir,
        "raw_sha256": digests(raw),
        "processed_sha256": digests(processed, spoof=tamper),
        "observation": "E:\\MotionWeaverData\\reports\\soma_uniform\\batch\\observations\\clip.json",
        "observation_sha256": "0" * 64,
    }
    (report / "run.json").write_text(
        json.dumps({"batch": "batch", "results": [record]}, ensure_ascii=False), encoding="utf-8")

    dataset = folder / "dataset"
    dataset.mkdir()
    (dataset / "rejected.jsonl").write_text(
        json.dumps({"source_id": "soma_uniform/bvh/clip.bvh", "reason": "帧数 69 不足 72"},
                   ensure_ascii=False) + "\n", encoding="utf-8")
    return dataset, report, source_root


class BuildRejectedReviewLibraryTests(unittest.TestCase):
    def test_batch_is_loadable_by_review_server(self):
        with tempfile.TemporaryDirectory() as temporary:
            folder = Path(temporary)
            dataset, report, source_root = fixture(folder)
            library = folder / "library"

            result = build(dataset, report, source_root, library, "soma_uniform", "rejected")
            self.assertEqual(result["entries"], 1)
            self.assertEqual(result["skipped"], [])

            index, entries, _ = load_native_review_inputs(result["index"], library)
            self.assertEqual(len(entries), 1)
            # asset 列必须来自 source_id；写成空值会让验收服务抛 KeyError。
            with Path(result["index"]).open(encoding="utf-8-sig", newline="") as stream:
                row = next(csv.DictReader(stream))
            self.assertEqual(row["asset"], "soma_uniform/bvh/clip.bvh")
            # 路径必须落在库内，源机器的 E: 路径不能残留。
            self.assertTrue(Path(row["processed"]).is_relative_to(library))
            self.assertNotIn("E:", row["raw"])

            # 被拒条目只能通过 included_statuses 声明，不能谎报成 normalized 之外的东西。
            acceptance = json.loads((Path(result["index"]).parent / "acceptance.json")
                                    .read_text(encoding="utf-8"))
            self.assertEqual(acceptance["status_counts"], {"normalized": 1})
            self.assertEqual(acceptance["included_statuses"], ["normalized"])

    def test_tampered_copy_aborts(self):
        with tempfile.TemporaryDirectory() as temporary:
            folder = Path(temporary)
            dataset, report, source_root = fixture(folder, tamper=True)
            library = folder / "library"
            # 复制后逐文件校验：内容对不上就整条跳过；一条都留不下时直接拒绝建批次，
            # 绝不产出「看起来合格」的空索引。
            with self.assertRaises(RuntimeError):
                build(dataset, report, source_root, library, "soma_uniform", "rejected")


if __name__ == "__main__":
    unittest.main()
