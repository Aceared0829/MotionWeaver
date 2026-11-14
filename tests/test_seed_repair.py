import copy
import unittest
from types import SimpleNamespace

import numpy as np

from data.tools.ingest.prepare_soma_ue_batch import compatible_offsets


class RepairCompatibilityTests(unittest.TestCase):
    def fixture(self):
        return SimpleNamespace(names=['Root','Hips','Spine'],parents=[-1,0,1],
                               offsets=np.array([[0.,0,0],[0,90,0],[0,10,0]]),
                               channels=[['Xposition','Yposition','Zposition'],['Xposition','Yposition','Zposition'],['Zrotation']])

    def test_small_body_difference_and_absolute_hips_offset_can_be_normalized(self):
        reference=self.fixture()
        source=copy.deepcopy(reference)
        source.offsets[1,1]=2
        source.offsets[2,1]+=0.15
        self.assertAlmostEqual(compatible_offsets(source,reference,True),0.15)
        with self.assertRaises(ValueError):compatible_offsets(source,reference,False)
        self.assertEqual(source.offsets[1,1],2)

    def test_large_difference_or_structural_difference_cannot_pass(self):
        reference=self.fixture()
        source=copy.deepcopy(reference)
        source.offsets[2,1]+=1
        with self.assertRaises(ValueError):compatible_offsets(source,reference,True)
        source=copy.deepcopy(reference)
        source.parents[2]=0
        with self.assertRaises(ValueError):compatible_offsets(source,reference,True)

    def test_dynamic_body_translation_cannot_be_silently_replaced(self):
        reference=self.fixture()
        source=copy.deepcopy(reference)
        source.offsets[2,1]+=0.1
        source.channels[2]=['Xposition']
        with self.assertRaises(ValueError):compatible_offsets(source,reference,True)
        source.offsets=reference.offsets.copy()
        with self.assertRaises(ValueError):compatible_offsets(source,reference,True)
