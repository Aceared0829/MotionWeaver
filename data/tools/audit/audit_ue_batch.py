"""审计 UE 导出，保存共享骨骼及隔离动画数组；不授予训练发布或源删除资格。"""
import argparse
import json
from pathlib import Path

import numpy as np
from data.runtime.unreal_dataset import (
    file_sha256,
    read_json,
    training_bones,
    validate_clip,
)
from scipy.spatial.transform import Rotation


def audit(source, output):
    source, output = Path(source).resolve(), Path(output).resolve()
    output.mkdir(parents=True, exist_ok=True)
    shared = None
    reports = []
    for filename in read_json(source/'manifest.json')['clips']:
        path = (source/filename).resolve()
        if not path.is_relative_to(source):
            raise ValueError('导出路径越界')
        clip = read_json(path)
        frames, _, _ = validate_clip(clip)
        bones = training_bones(clip['bones'])
        contract = {key: clip[key] for key in ['coordinate_system','root_policy','pose_policy','root_bone','pelvis_bone','roles']}
        contract['bones'] = bones
        if shared is None:
            shared = contract
            (output/'skeleton.json').write_text(json.dumps(shared),encoding='utf-8')
        elif shared != contract:
            raise ValueError('批次中骨骼契约不同，不能共享')
        keep = [i for i,b in enumerate(clip['bones']) if not b['name'].startswith('VB ')]
        frames = frames[:,keep]
        root = np.asarray(clip['root_frames'])
        rotation = Rotation.from_quat(root[:,3:]).as_matrix()
        world = np.einsum('tij,tkj->tki',rotation,frames[:,:,:3])+root[:,None,:3]
        names = [b['name'] for b in bones]
        refs = np.asarray([b['position'] for b in bones])
        errors = []
        for i,bone in enumerate(bones):
            if bone['parent'] >= 0:
                parent = bone['parent']
                errors.append(np.abs(np.linalg.norm(frames[:,i,:3]-frames[:,parent,:3],axis=-1)-np.linalg.norm(refs[i]-refs[parent])).max())
        report = {'asset': clip['asset'],'fps': clip['fps'],'frames': len(frames),'bones': len(bones),'max_bone_length_error_cm': float(max(errors)),
                      'root_range_cm': np.ptp(root[:,:3],axis=0).tolist(),'pelvis_relative_range_cm': np.ptp(frames[:,names.index('pelvis'),:3],axis=0).tolist(),
                      'foot_min_z_cm': {name:float(world[:,names.index(name),2].min()) for name in ['foot_l','foot_r','ball_l','ball_r']},
                      'ue_export_sha256': file_sha256(path),'training_ready': False,'source_deleted': False}
        dest = output/(Path(filename).stem+'.npz')
        np.savez_compressed(dest,frames=frames.astype(np.float32),root_frames=root.astype(np.float32),fps=clip['fps'])
        with np.load(dest,allow_pickle=False) as saved:
            if not np.array_equal(saved['frames'],frames.astype(np.float32)) or not np.array_equal(saved['root_frames'],root.astype(np.float32)):
                raise ValueError('压缩数组回读失败')
        report['output_sha256'] = file_sha256(dest)
        reports.append(report)
    (output/'audit.json').write_text(json.dumps(reports,indent=2),encoding='utf-8')
    print(json.dumps(reports))


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input',required=True)
    parser.add_argument('--output',required=True)
    args=parser.parse_args()
    audit(args.input,args.output)
