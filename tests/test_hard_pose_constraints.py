import unittest

import numpy as np
from scipy.spatial.transform import Rotation

from inference.profiling.hard_pose_constraints import constrain_poses, rotation_errors


class HardPoseTests(unittest.TestCase):
    def setUp(self):
        self.soft=np.zeros((30,2,7));self.soft[:,:,6]=1
        self.target=self.soft.copy();self.target[12,0,0]=30
        self.target[12,1,3:]=Rotation.from_euler('z',170,degrees=True).as_quat()

    def test_anchor_exact_and_outside_unchanged(self):
        out=constrain_poses(self.soft,self.target,list(range(30)),[12])
        np.testing.assert_allclose(out[12],self.target[12],atol=1e-12)
        np.testing.assert_allclose(out[:5],self.soft[:5],atol=1e-12)
        self.assertGreater(out[11,0,0],0)
        self.assertLess(abs(out[12,0,0]-out[11,0,0]),30)
        self.assertLess(rotation_errors(out[12],self.target[12]).max(),1e-5)

    def test_non_anchor_source_is_not_used(self):
        a=constrain_poses(self.soft,self.target,list(range(30)),[12])
        target=self.target.copy();target[:12,:,:3]=999
        b=constrain_poses(self.soft,target,list(range(30)),[12])
        np.testing.assert_allclose(a,b)

    def test_adjacent_and_boundary_anchors(self):
        ids=[0,12,13,29];out=constrain_poses(self.soft,self.target,list(range(30)),ids)
        np.testing.assert_allclose(out[ids],self.target[ids],atol=1e-12)
        np.testing.assert_allclose(np.linalg.norm(out[:,:,3:],axis=-1),1,atol=1e-12)

    def test_no_visible_anchor(self):
        np.testing.assert_array_equal(constrain_poses(self.soft,self.target,list(range(30)),[50]),self.soft)

    def test_invalid_timebase_and_parameters_are_rejected(self):
        for frames in ([0]*30,list(range(0,60,2))):
            with self.assertRaises(ValueError):constrain_poses(self.soft,self.target,frames,[12])
        for radius in (0, True):
            with self.assertRaises(ValueError):constrain_poses(self.soft,self.target,list(range(30)),[12],radius=radius)
        for smoothness in (-1, float('nan')):
            with self.assertRaises(ValueError):constrain_poses(self.soft,self.target,list(range(30)),[12],smoothness=smoothness)
        with self.assertRaises(ValueError):constrain_poses(self.soft,self.target,list(range(30)),[True])

    def test_invalid_anchor_quaternion_is_rejected(self):
        self.target[12,:,3:]=0
        with self.assertRaises(ValueError):constrain_poses(self.soft,self.target,list(range(30)),[12])


if __name__=='__main__':unittest.main()
