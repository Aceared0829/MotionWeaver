import importlib.util
import unittest
from pathlib import Path

spec=importlib.util.spec_from_file_location('gate',Path(__file__).resolve().parents[1] / "data/tools/ops/motion_batch_gate.py")
assert spec is not None and spec.loader is not None
gate = importlib.util.module_from_spec(spec)
assert spec is not None
spec.loader.exec_module(gate)


class GateTests(unittest.TestCase):
    def test_never_crosses_review_boundary(self):
        for pending in range(501):
            self.assertLessEqual(pending+gate.batch_capacity(pending),500)
        self.assertEqual(gate.batch_capacity(499),1)
        self.assertEqual(gate.batch_capacity(500),0)
        self.assertEqual(gate.batch_capacity(582),0)

    def test_normal_microbatch(self):
        self.assertEqual(gate.batch_capacity(0),8)
        with self.assertRaises(ValueError):
            gate.batch_capacity(-1)
