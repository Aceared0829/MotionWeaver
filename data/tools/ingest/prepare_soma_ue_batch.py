"""为 UE 导入准备 SOMA 源轨道，不在外部做目标骨骼重定向。"""
import argparse
import csv
import hashlib
import json
from pathlib import Path

import numpy as np
from data.runtime.bvh_reader import read_bvh
from data.runtime.unreal_dataset import file_sha256
from scipy.spatial.transform import Rotation


def compatible_offsets(bvh, reference, normalize=False):
    if bvh.names != reference.names or bvh.parents != reference.parents:
        raise ValueError('源骨骼名称或父子层级不匹配')
    if not normalize and np.allclose(bvh.offsets, reference.offsets, atol=1e-4):
        return 0.0
    hips=bvh.names.index('Hips')
    if not normalize or bvh.parents[hips]!=0:
        raise ValueError('源骨骼参考偏移不匹配')
    if bvh.channels[hips][:3]!=['Xposition','Yposition','Zposition']:
        raise ValueError('参考归一化需要绝对 Hips 平移')
    if any(any(c.endswith('position') for c in ch) for i,ch in enumerate(bvh.channels) if i not in (0,hips)):
        raise ValueError('参考归一化不支持身体骨骼平移通道')
    delta=np.linalg.norm(bvh.offsets-reference.offsets,axis=1)
    delta[hips]=0
    maximum=float(delta.max())
    if maximum>0.25:
        raise ValueError('参考偏移差超过 0.25 cm，必须单独适配骨架')
    return maximum


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', default='D:/BONES-SEED')
    parser.add_argument('--work', required=True)
    parser.add_argument('--selection', help='数据库批次选择 JSON，不再固定试验动作')
    parser.add_argument('--normalize-reference-offsets',action='store_true',help='仅结构一致且身体偏移差不超过 0.25 cm 时归一化源参考骨架；保留绝对 Hips 平移和旋转')
    args = parser.parse_args()
    source = Path(args.source).resolve()
    work = Path(args.work).resolve()
    work.mkdir(parents=True, exist_ok=True)
    selected = {}
    if args.selection:
        rows = json.loads(Path(args.selection).read_text(encoding='utf-8'))
        selected = {row['filename']: row for row in rows}
        if len(selected) != len(rows):
            raise ValueError('批次含重复动作')
    else:
        with (source/'metadata/seed_metadata_v004.csv').open(encoding='utf-8-sig', newline='') as stream:
            for row in csv.DictReader(stream):
                if row['package'] not in selected and row['is_mirror']=='False' and 240 <= int(row['move_duration_frames']) <= 1200:
                    selected[row['package']] = row
    if len(selected) > 32:
        raise ValueError('UE 微批不得超过 32 条')
    clips = []
    # USD 导入器将源 Y-up 右手空间转换为 UE Z-up 左手空间。
    basis = np.array([[1.,0,0],[0,0,1],[0,1,0]])
    reference = read_bvh(source/'soma_shapes/soma_base_rig/soma_base_skel_minimal.bvh')
    for row in selected.values():
        path = (source/row['move_soma_uniform_path']).resolve()
        if not path.is_relative_to(source/'soma_uniform'):
            raise ValueError('源文件越界')
        bvh = read_bvh(path)
        if len(bvh.values)>18000:
            raise ValueError('长动作需无损分段，禁止截断')
        correction=compatible_offsets(bvh,reference,args.normalize_reference_offsets)
        if len(bvh.values) != int(row['move_duration_frames']) or abs(1/bvh.frame_time-120)>0.01:
            raise ValueError('源帧数或采样率不匹配')
        hips = bvh.names.index('Hips')
        if bvh.parents[hips] != 0 or bvh.channels[hips][:3] != ['Xposition','Yposition','Zposition']:
            raise ValueError('源 Hips 布局不支持')
        p,r = bvh.forward()
        # BONES-SEED Hips position 通道是绝对平移，不能再加 OFFSET。
        p[:,hips:] -= np.einsum('tij,j->ti',r[:,0],bvh.offsets[hips])[:,None]
        p = p @ basis.T
        r = basis @ r @ basis.T
        local_p,local_r = p.copy(),r.copy()
        for i,parent in enumerate(bvh.parents):
            if parent >= 0:
                inverse = r[:,parent].transpose(0,2,1)
                local_p[:,i] = np.einsum('tij,tj->ti',inverse,p[:,i]-p[:,parent])
                local_r[:,i] = inverse @ r[:,i]
        if args.normalize_reference_offsets:
            # Hips 是绝对运动，不能替换为静态参考高度；身体旋转不变，只统一细微的静态骨长差。
            for i in range(len(bvh.names)):
                if i not in (0,hips):
                    local_p[:,i]=reference.offsets[i] @ basis.T
        q = Rotation.from_matrix(local_r.reshape(-1,3,3)).as_quat().reshape(len(p),len(bvh.names),4)
        tracks = np.concatenate([local_p,q],axis=-1)
        clips.append({'name': row['filename'],'metadata': row,'source': str(path),'sha256': file_sha256(path),'fps': 120,
                          'source_reference_normalized': args.normalize_reference_offsets,'max_reference_correction_cm': correction,
                          'tracks': tracks.transpose(1,0,2).tolist()})
    policy='names_v3_reference_normalized:' if args.normalize_reference_offsets else 'names_v2:'
    batch_id = 'B_' + hashlib.sha256((policy+''.join(c['sha256'] for c in clips)).encode()).hexdigest()[:16]
    request = {'batch_id': batch_id,'names': reference.names,'clips': clips,'training_ready': False}
    (work/'soma_batch_request.json').write_text(json.dumps(request),encoding='utf-8')
    print(json.dumps({'clips': len(clips),'work': str(work)}))


if __name__=='__main__':
    main()
