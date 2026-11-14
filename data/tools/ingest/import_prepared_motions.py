"""验证既有训练数据，复制到独立数据库目录并逐文件校验；不删除源数据。"""
import argparse
import hashlib
import json
import os
import shutil
from pathlib import Path

import numpy as np
from data.runtime.motion_catalog import MotionCatalog
from data.runtime.unreal_dataset import (
    UnrealMotionDataset,
    file_sha256,
    read_json,
    skeleton_signature,
)


def import_dataset(source, library):
    source = Path(source).resolve()
    dataset = UnrealMotionDataset(source)
    skeleton = read_json(source / 'skeleton.json')
    manifest = dataset.manifest
    if manifest.get('schema_version') != 2 or skeleton.get('root_bone') != 'root' or skeleton.get('pelvis_bone') != 'pelvis':
        raise ValueError('仅接收独立 Root 的 v2 训练数据')
    if manifest['skeleton_signature'] != skeleton_signature(skeleton):
        raise ValueError('骨骼签名不一致')
    identity = file_sha256(source / 'dataset.json')
    catalog = MotionCatalog(library)
    try:
        target = catalog.folder / 'motions' / ('prepared_' + identity[:16])
        target.mkdir(exist_ok=True)
        hashes = {}
        # 保留完整快照，包括原始数组、统计量、标签与划分；拒绝符号链接逃逸。
        for path in source.rglob('*'):
            if path.is_dir():
                continue
            if not path.resolve().is_relative_to(source):
                raise ValueError('源文件越界')
            relative = path.relative_to(source)
            dest = target / relative
            dest.parent.mkdir(parents=True, exist_ok=True)
            digest = file_sha256(path)
            if dest.exists():
                if file_sha256(dest) != digest:
                    raise ValueError(f'目标已有冲突文件：{dest}')
            else:
                temp = dest.with_name(dest.name + '.partial')
                with path.open('rb') as inp, temp.open('wb') as out:
                    shutil.copyfileobj(inp, out, 1024 * 1024)
                    out.flush()
                    os.fsync(out.fileno())
                if file_sha256(temp) != digest:
                    raise ValueError(f'复制校验失败：{relative}')
                temp.replace(dest)
            hashes[str(relative)] = digest
        checked = UnrealMotionDataset(target)
        for item in manifest['clips']:
            raw = target / item['raw_file']
            if not raw.resolve().is_relative_to(target):
                raise ValueError('raw_file 越界')
            with np.load(raw, allow_pickle=False) as archive:
                for key in archive.files:
                    values = archive[key]
                    if np.issubdtype(values.dtype, np.number) and not np.isfinite(values).all():
                        raise ValueError(f'原始数组非有限值：{raw}')
            motion_id = hashlib.sha256((identity + ':' + item['asset']).encode()).hexdigest()
            metadata = dict(item, training_signature=manifest['training_signature'], snapshot=str(target), validation='existing_training_loader_and_file_hashes')
            with catalog.db:
                catalog.db.execute('INSERT OR IGNORE INTO motions(id,dataset,name,metadata_json,state,output_path,output_sha256) VALUES(?,?,?,?,?,?,?)',
                                   (motion_id, 'uefn-prepared-' + identity, item['asset'], json.dumps(metadata, ensure_ascii=False), 'prepared_imported', str(target / item['file']), hashes[item['file']]))
                catalog.db.execute('INSERT OR IGNORE INTO sources VALUES(?,?,?)', (motion_id, 'prepared', str(source / item['file'])))
        report = {'imported': len(checked), 'snapshot': str(target), 'manifest_sha256': identity, 'file_hashes': hashes, 'source_deleted': False}
        report_path = catalog.folder / 'reports' / ('import_' + identity[:16] + '.json')
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
        return {'imported': len(checked), 'snapshot': str(target), 'report': str(report_path), 'states': catalog.status()}
    finally:
        catalog.close()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', required=True)
    parser.add_argument('--library', default='D:/MotionDataLibrary')
    args = parser.parse_args()
    print(json.dumps(import_dataset(args.source, args.library), ensure_ascii=False))
