"""来源分段时间保留及未知对齐状态。"""

import unittest

from data.tools.build.enrich_soma_observation import temporal_reading


class SomaSemanticsTests(unittest.TestCase):
    def test_parameterized_source_timing(self):
        for case in ("gaps_and_overrun_preserved", "overlap_rejected"):
            with self.subTest(case=case):
                events = [{"start_time": .15, "end_time": 1., "description": "source text"},
                          {"start_time": 1.1, "end_time": 2.1, "description": "source text 2"}]
                if case == "overlap_rejected":
                    events[1]["start_time"] = .9
                    with self.assertRaisesRegex(ValueError, "时序无效或重叠"):
                        temporal_reading({"events": events, "num_events": 2}, 2)
                else:
                    result = temporal_reading({"events": events, "num_events": 2}, 2)
                    self.assertEqual(result["events"], events)
                    self.assertEqual(result["gaps_s"], [[0, .15], [1, 1.1]])
                    self.assertAlmostEqual(result["ends_beyond_motion"][0]["end_beyond_motion_s"], .1)
                    self.assertEqual(result["clock_status"], "source_seconds_alignment_unverified")
                    self.assertEqual(result["contact_truth"], "unknown")


if __name__ == "__main__":
    unittest.main()
