"""直接流式读取共享骨架 NPZ，经现有预处理和训练加载器验证；不发布或删除。"""
import argparse
import json
import os
import shutil
import sqlite3
import uuid
from pathlib import Path

import numpy as np
import torch
from data.runtime.unreal_dataset import (
    UnrealMotionDataset,
    convert_clip,
    convert_root_track,
    file_sha256,
)

from data.tools.ingest.prepare_unreal_dataset import prepare


def load_audited_clip(library, row):
    mid,_name,filename,digest=row
    library=Path(library).resolve()
    path=Path(filename).resolve()
    if not path.is_relative_to(library) or file_sha256(path)!=digest:
        raise ValueError('待验收输出身份无效：'+mid)
    skeleton=(path.parent/'skeleton.json').resolve()
    audit_path=(path.parent/'audit.json').resolve()
    if not skeleton.is_relative_to(library) or not audit_path.is_relative_to(library):
        raise ValueError('共享骨架或审计路径越界')
    contract=json.loads(skeleton.read_text(encoding='utf-8'))
    audits=json.loads(audit_path.read_text(encoding='utf-8'))
    matching=[item for item in audits if item.get('output_sha256')==digest]
    if len(matching)!=1:
        raise ValueError('UE 导出审计身份不唯一')
    with np.load(path,allow_pickle=False) as archive:
        frames=archive['frames'].copy()
        roots=archive['root_frames'].copy()
        fps=float(archive['fps'])
    return dict(contract,schema_version=2,asset=matching[0]['asset'],frames=frames,root_frames=roots,fps=fps,
                duration_seconds=(len(frames)-1)/fps,sampling_policy='source_data_keys')


def validate(library,limit=None):
    library=Path(library).resolve()
    torch.set_num_threads(2)
    with sqlite3.connect(f'file:{library / "catalog.sqlite3"}?mode=ro',uri=True) as db:
        rows=db.execute("SELECT id,name,output_path,output_sha256 FROM motions WHERE dataset='bones-seed' AND state='retargeted_pending_quality' ORDER BY id").fetchall()
    if limit is not None:
        rows=rows[:limit]
    if not rows:
        raise ValueError('没有待验证动作')
    mapping={row[0]:row for row in rows}
    output=library/'work'/('training_check_'+uuid.uuid4().hex)
    manifest={'schema_version': 2,'training_contract': 'pose_only_root_authoritative','clips': list(mapping)}
    def loader(mid):
        if shutil.disk_usage(library).free < 44*1024**3:
            raise RuntimeError('D 盘低于 44 GiB，停止训练格式验证；保留未完成快照供检查')
        return load_audited_clip(library,mapping[mid])
    # 不跳过不合格项、不补帧、不重采样；这里只生成隔离验证数据，不改变生产状态。
    prepared=prepare(library,output,input_manifest=manifest,clip_loader=loader)
    dataset=UnrealMotionDataset(output)
    if len(dataset)!=len(rows):
        raise ValueError('训练回读数量不匹配')
    bindings=[]
    for index,item in enumerate(prepared['clips']):
        sample=dataset[index]
        if not torch.isfinite(sample['motion']).all():
            raise ValueError('训练张量非有限值')
        clip=loader(item['source_file'])
        positions,rotations,_=convert_clip(clip)
        roots=convert_root_track(clip)
        with np.load(output/item['raw_file'],allow_pickle=False) as raw:
            if not all(np.array_equal(raw[key],value) for key,value in [('positions',positions),('rotations',rotations),('root_track',roots)]):
                raise ValueError('原始轨道转换回读不一致')
        bindings.append({'id': item['source_file'],'source_output_sha256': mapping[item['source_file']][3],
                             'file': item['file'],'sha256': file_sha256(output/item['file']),
                             'raw_file': item['raw_file'],'raw_sha256': file_sha256(output/item['raw_file'])})
    report={'count': len(dataset),'snapshot': str(output),'feature_dim': prepared['feature_dim'],'fps': prepared['fps'],
                'manifest_sha256': file_sha256(output/'dataset.json'),'training_signature': prepared['training_signature'],
                'validation': 'existing_preprocessor_dataset_getitem_and_exact_raw_roundtrip','bindings': bindings,
                'training_ready': False,'source_deleted': False,'remaining_gates': ['semantic_quality','production_split_and_statistics','durable_publication','recoverable_cleanup']}
    dest=output/'training_validation.json'
    with dest.open('x',encoding='utf-8') as stream:
        json.dump(report,stream,ensure_ascii=False,indent=2)
        stream.flush()
        os.fsync(stream.fileno())
    print(json.dumps({'count': len(dataset),'report': str(dest),'feature_dim': prepared['feature_dim'],'training_ready': False}),flush=True)
    return report


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--library',default='D:/MotionDataLibrary')
    parser.add_argument('--limit',type=int)
    args=parser.parse_args()
    if args.limit is not None and args.limit<1:
        parser.error('limit 必须为正数')
    validate(args.library,args.limit)
