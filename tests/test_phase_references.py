import unittest

import numpy as np

from inference.profiling.evaluate_phase_references import phase_candidates


class PhaseTests(unittest.TestCase):
    names = {'pelvis':0,'calf_l':1,'calf_r':2,'hand_l':3,'hand_r':4,'foot_l':5,'foot_r':6}

    def test_short_clips_keep_only_unique_boundaries(self):
        for n in range(1,9):
            phases=phase_candidates(np.zeros((n,7,12)),self.names)
            self.assertEqual([p['frame'] for p in phases],sorted({0,n-1}))
        with self.assertRaises(ValueError):phase_candidates(np.zeros((0,7,12)),self.names)

    def test_static_pose_does_not_invent_action_phases(self):
        phases = phase_candidates(np.zeros((40,7,12)),self.names)
        self.assertEqual([p['frame'] for p in phases],[0,39])

    def test_knee_peak_is_translation_invariant_and_semantics_unconfirmed(self):
        pose = np.zeros((80,7,12))
        pose[35,1,2]=50
        pose[15,3,0]=30
        phases = phase_candidates(pose,self.names)
        clearance = next(p for p in phases if p['phase']=='clearance_candidate')
        self.assertEqual(clearance['frame'],35)
        self.assertFalse(clearance['confirmed'])
        translated = pose.copy()
        translated[:,:,:3] += np.arange(80)[:,None,None]*3
        self.assertEqual(phases,phase_candidates(translated,self.names))
        self.assertEqual([p['frame'] for p in phases],sorted({p['frame'] for p in phases}))


if __name__=='__main__':unittest.main()
