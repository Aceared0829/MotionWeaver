"""验证不同窗口下的时间对齐、已播放结果冻结及残差权重边界。"""
import unittest

import numpy as np

from inference.profiling.evaluate_unreal_streaming import simulate


class StreamingComparisonTests(unittest.TestCase):
    def setUp(self):
        self.source = np.zeros((80, 1, 7))
        self.source[:, 0, 0] = np.arange(80) * 20
        self.source[:, 0, 6] = 1

    def windows(self, width):
        return {end: self.source[end-width+1:end+1].copy() for end in range(width-1, 80, 4)}

    def test_fast_source_motion_is_not_damped(self):
        for width in (16, 24, 32):
            for fusion in ("temporal", "source_residual"):
                result = simulate(self.source, self.windows(width), 8, width, fusion)
                self.assertIn(40, result)
                for index, value in result.items():
                    np.testing.assert_allclose(value, self.source[index], atol=1.e-8)

    def test_future_windows_cannot_rewrite_played_endpoints(self):
        for width in (16, 24):
            original = simulate(self.source, self.windows(width), 8, width)
            changed = self.windows(width)
            for end, value in changed.items():
                if end >= 43:
                    value[:, 0, 0] += 1000
            revised = simulate(self.source, changed, 8, width)
            np.testing.assert_allclose(revised[33], original[33])

    def test_residual_weight_reduces_corrupt_window_influence(self):
        windows = self.windows(24)
        windows[43][:, 0, 0] += 100
        temporal = simulate(self.source, windows, 8, 24)
        residual = simulate(self.source, windows, 8, 24, "source_residual")
        self.assertLess(abs(residual[36][0, 0] - self.source[36, 0, 0]),
                        abs(temporal[36][0, 0] - self.source[36, 0, 0]))


if __name__ == "__main__":
    unittest.main()
