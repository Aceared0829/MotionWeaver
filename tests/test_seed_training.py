import json
import tempfile
import unittest
from pathlib import Path

import numpy as np
from data.runtime.unreal_dataset import UnrealMotionDataset, file_sha256
from test_unreal_dataset import pose_only_fixture

from data.tools.ingest.prepare_unreal_dataset import prepare
from data.tools.audit.validate_seed_training import load_audited_clip


class SeedTrainingTests(unittest.TestCase):
    def test_shared_npz_matches_json_training_features(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)
            clip=pose_only_fixture()
            clip['sampling_policy']='source_data_keys'
            clip['duration_seconds']=(len(clip['frames'])-1)/clip['fps']
            (root/'clip.json').write_text(json.dumps(clip),encoding='utf-8')
            manifest={'schema_version': 2,'training_contract': 'pose_only_root_authoritative','clips': ['clip.json']}
            (root/'manifest.json').write_text(json.dumps(manifest),encoding='utf-8')
            contract={key:clip[key] for key in ['coordinate_system','root_policy','pose_policy','root_bone','pelvis_bone','roles','bones']}
            (root/'skeleton.json').write_text(json.dumps(contract),encoding='utf-8')
            np.savez_compressed(root/'clip.npz',frames=np.array(clip['frames']),root_frames=np.array(clip['root_frames']),fps=clip['fps'])
            digest=file_sha256(root/'clip.npz')
            (root/'audit.json').write_text(json.dumps([{'asset': clip['asset'],'output_sha256': digest}]),encoding='utf-8')
            row=('id','name',str(root/'clip.npz'),digest)
            prepare(root,root/'json_output')
            prepare(root,root/'npz_output',input_manifest=manifest,clip_loader=lambda _:load_audited_clip(root,row))
            a=UnrealMotionDataset(root/'json_output')[0]['motion'].numpy()
            b=UnrealMotionDataset(root/'npz_output')[0]['motion'].numpy()
            np.testing.assert_array_equal(a,b)
            with self.assertRaises(ValueError):
                load_audited_clip(root,('id','name',str(root/'clip.npz'),'wrong_hash'))

    def test_path_escape_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            with self.assertRaises(ValueError):
                load_audited_clip(Path(temp)/'library',('id','name',str(Path(temp)/'outside.npz'),'hash'))
