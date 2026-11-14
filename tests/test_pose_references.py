"""验证参考帧数量、历史范围及非均匀关键姿态选取。"""
import unittest

import numpy as np

from inference.profiling.evaluate_pose_references import select_references
from inference.profiling.evaluate_targeted_references import problem_frame


class PoseReferenceTests(unittest.TestCase):
    def test_problem_region_and_translation_invariance(self):
        source = np.zeros((24, 3, 12))
        predicted = source.copy()
        predicted[9, 2, 1] = 30
        predicted[:, :, :3] += 50
        result = problem_frame(source, predicted, [0, 23], {'arm': [1], 'leg': [2]})
        self.assertEqual(result, (9, 'leg', 30.0))

    def test_problem_selection_excludes_existing_reference_neighborhood(self):
        source = np.zeros((24, 2, 12))
        predicted = source.copy()
        predicted[9, 1, 1] = 30
        predicted[10, 1, 1] = 25
        predicted[15, 1, 1] = 20
        result = problem_frame(source, predicted, [0, 9, 23], {'arm': [1]})
        self.assertEqual(result, (15, 'arm', 20.0))

    def test_noncentral_pose_extremum_is_selected(self):
        packed = np.zeros((24, 2, 12))
        packed[9, 0, 1] = 30
        selected = select_references(packed, 3, 'key', np.ones(2))
        self.assertEqual(selected, [0, 9, 23])

    def test_counts_bounds_and_spacing(self):
        packed = np.random.default_rng(17).normal(size=(24, 4, 12))
        for count in (2, 3, 4, 5):
            for strategy in ('uniform', 'key', 'shape'):
                selected = select_references(packed, count, strategy, np.ones(4))
                self.assertEqual(len(set(selected)), count)
                self.assertEqual(selected[0], 0)
                self.assertEqual(selected[-1], 23)
                self.assertTrue(np.all(np.diff(selected) >= 3))

    def test_stationary_ties_are_deterministic(self):
        packed = np.zeros((24, 2, 12))
        first = select_references(packed, 5, 'key', np.ones(2))
        self.assertEqual(first, select_references(packed, 5, 'key', np.ones(2)))

    def test_shape_ignores_whole_body_translation(self):
        packed = np.zeros((24, 2, 12))
        packed[9, 1, 1] = 30
        selected = select_references(packed, 3, 'shape', np.ones(2))
        self.assertEqual(selected, [0, 9, 23])
        packed[:, :, :3] += np.arange(24)[:, None, None] * 20
        self.assertEqual(selected, select_references(packed, 3, 'shape', np.ones(2)))


if __name__ == '__main__':
    unittest.main()
