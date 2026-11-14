"""为已发布清单补充哈希匹配的 UE JSON 导出文件；不删除文件。"""
import argparse
import json
from pathlib import Path

from data.runtime.unreal_dataset import file_sha256

from data.tools.publish.close_seed_batch import write_json


def extend(plan_path):
    plan_path=Path(plan_path).resolve()
    if plan_path.parent!=Path('D:/MotionDataLibrary/reports'):raise ValueError('计划越界')
    plan=json.loads(plan_path.read_text(encoding='utf-8'))
    if not plan['published']:raise ValueError('尚未发布')
    expected={item['ue_evidence']['audit']['asset']:item['ue_evidence']['audit']['ue_export_sha256'] for item in plan['items']}
    existing={c['path']:c for c in plan['cleanup']}
    root=Path('D:/GameAnimationSample/Saved/AILocomotionDataset').resolve()
    for manifest in root.glob('*/manifest.json'):
        entries=json.loads(manifest.read_text(encoding='utf-8'))['clips']
        matched=[]
        for name in entries:
            path=(manifest.parent/name).resolve()
            if not path.is_relative_to(manifest.parent.resolve()):raise ValueError('导出路径越界')
            if not path.exists():continue
            with path.open(encoding='utf-8') as stream:
                head=''.join(next(stream,'') for _ in range(4))
            import re
            match=re.search(r'"asset"\s*:\s*("[^"]+")',head)
            asset=json.loads(match[1]) if match else None
            if asset not in expected:continue
            digest=file_sha256(path)
            if digest!=expected[asset]:continue
            matched.append({'path': str(path),'sha256': digest,'bytes': path.stat().st_size,'kind': 'ue_export','asset': asset})
        for entry in matched:existing[entry['path']]=entry
    plan['cleanup']=list(existing.values())
    plan['cleanup_bytes']=sum(e['bytes'] for e in existing.values())
    write_json(plan_path,plan)
    print(json.dumps({'files': len(existing),'gib': plan['cleanup_bytes']/1024**3}))


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--plan',required=True)
    extend(parser.parse_args().plan)
