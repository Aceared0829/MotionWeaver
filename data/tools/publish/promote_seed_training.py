"""将已入库批次的可读训练快照移入最终目录；同盘移动，不复制、不覆盖。"""
import argparse
import json
from pathlib import Path

from data.runtime.unreal_dataset import UnrealMotionDataset, file_sha256

from data.tools.publish.close_seed_batch import write_json


def promote(plan_path):
    plan_path=Path(plan_path).resolve()
    library=Path('D:/MotionDataLibrary').resolve()
    if plan_path.parent!=library/'reports':raise ValueError('计划越界')
    plan=json.loads(plan_path.read_text(encoding='utf-8'))
    if not plan['published']:raise ValueError('未发布')
    target=Path(plan['target']).resolve()
    if target.parent!=library/'motions' or target.name not in {'seed_'+plan['batch'],'seed_stage_'+plan['batch']}:raise ValueError('目标目录无效')
    source=Path(plan['training_snapshot']).resolve()
    destination=(target/'training').resolve()
    if not destination.is_relative_to(target):raise ValueError('训练目录越界')
    if source!=destination and (source.parent!=library/'work' or not source.name.startswith('training_check_')):
        raise ValueError('训练源目录无效')
    check=source if source.exists() else destination
    report=json.loads((check/'training_validation.json').read_text(encoding='utf-8'))
    if file_sha256(check/'dataset.json')!=report['manifest_sha256']:raise ValueError('清单变化')
    for item in report['bindings']:
        for key,digest in [('file','sha256'),('raw_file','raw_sha256')]:
            path=(check/item[key]).resolve()
            if not path.is_relative_to(check) or file_sha256(path)!=item[digest]:raise ValueError('训练文件变化')
    if len(UnrealMotionDataset(check))!=plan['count']:raise ValueError('训练读取数量错误')
    if source!=destination and source.exists():
        if destination.exists():raise ValueError('目标已存在，拒绝覆盖')
        source.rename(destination)
    if len(UnrealMotionDataset(destination))!=plan['count']:raise ValueError('移动后读取失败')
    plan['training_snapshot']=str(destination)
    write_json(plan_path,plan)
    manifest_path=target/'manifest.json'
    manifest=json.loads(manifest_path.read_text(encoding='utf-8'))
    manifest['training_snapshot']=str(destination)
    manifest['training_statistics_scope']='batch_only; derive global statistics and splits before cross-batch training'
    write_json(manifest_path,manifest)
    print(json.dumps({'count': plan['count'],'training': str(destination)}),flush=True)


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--plan',required=True)
    promote(parser.parse_args().plan)
