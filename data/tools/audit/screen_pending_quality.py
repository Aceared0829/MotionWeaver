"""对待验收库做只读完整性与几何初筛；不发布、不改处理状态、不删除文件。"""
import argparse
import json
import os
import time
from pathlib import Path

import numpy as np
from data.runtime.motion_catalog import MotionCatalog
from data.runtime.unreal_dataset import file_sha256


def screen(folder, motion_ids=None, state='retargeted_pending_quality', output_name='pending_quality_screen.json'):
    if Path(output_name).name!=output_name:
        raise ValueError('初筛报告文件名越界')
    catalog=MotionCatalog(folder)
    reports=[]
    try:
        query='SELECT id,name,output_path,output_sha256,metadata_json FROM motions WHERE state=?'
        parameters=[state]
        if motion_ids is not None:
            if not motion_ids:raise ValueError('初筛选择为空')
            query+=' AND id IN ('+','.join('?' for _ in motion_ids)+')'
            parameters.extend(motion_ids)
        rows=catalog.db.execute(query,parameters).fetchall()
        if motion_ids is not None and len(rows)!=len(motion_ids):
            raise ValueError('初筛选择状态或数量变化')
        for mid,name,filename,digest,metadata in rows:
            errors=[]
            flags=[]
            path=Path(filename).resolve()
            try:
                if not path.is_relative_to(catalog.folder):
                    raise ValueError('output_outside_library')
                if file_sha256(path)!=digest:
                    raise ValueError('output_hash_mismatch')
                bones=json.loads((path.parent/'skeleton.json').read_text())['bones']
                with np.load(path,allow_pickle=False) as archive:
                    frames=archive['frames']
                    roots=archive['root_frames']
                    fps=float(archive['fps'])
                    if frames.shape!=(int(json.loads(metadata)['move_duration_frames']),len(bones),7) or roots.shape!=(len(frames),7):
                        raise ValueError('frame_shape_mismatch')
                    if fps!=120 or not np.isfinite(frames).all() or not np.isfinite(roots).all():
                        raise ValueError('invalid_samples')
                    if not np.allclose(np.linalg.norm(frames[:,:,3:],axis=-1),1,atol=1e-3) or not np.allclose(np.linalg.norm(roots[:,3:],axis=-1),1,atol=1e-3):
                        raise ValueError('invalid_quaternion')
                audits=json.loads((path.parent/'audit.json').read_text())
                matches=[item for item in audits if item.get('output_sha256')==digest]
                if len(matches)!=1:
                    raise ValueError('audit_binding_not_unique')
                audit=matches[0]
                if audit['max_bone_length_error_cm']>0.1:
                    flags.append('bone_length_deviation')
                if min(audit['foot_min_z_cm'].values()) < -2:
                    flags.append('possible_ground_penetration')
                # 几何初筛不能证明视觉质量、脚接触、根朝向和训练划分正确。
                flags.extend(['heading_not_validated','contacts_not_validated','visual_quality_not_validated'])
            except (OSError,ValueError,KeyError,TypeError) as exc:
                errors.append(str(exc))
            reports.append({'id': mid,'name': name,'integrity_errors': errors,'review_flags': flags,'training_ready': False})
        counts={}
        for report in reports:
            for flag in report['review_flags']+report['integrity_errors']:
                counts[flag]=counts.get(flag,0)+1
        result={'checked': len(reports),'counts': counts,'reports': reports,'source_deleted': False}
        output=catalog.folder/'reports'/output_name
        # Windows 上本机查看器或防病毒软件可能短暂占用旧报告。使用进程唯一的临时
        # 文件，并在短暂重试后才将该批判为失败，避免并发扫描覆盖彼此的临时文件。
        temp=output.with_name(output.name+'.partial.'+str(os.getpid()))
        temp.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
        for attempt in range(20):
            try:
                os.replace(temp,output)
                break
            except PermissionError:
                if attempt==19:
                    raise
                time.sleep(0.5)
        print(json.dumps({'checked': len(reports),'counts': counts,'report': str(output)}))
    finally:
        catalog.close()


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--library',default='D:/MotionDataLibrary')
    args=parser.parse_args()
    screen(args.library)
