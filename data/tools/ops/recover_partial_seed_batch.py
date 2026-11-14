"""恢复完整批次中已成功导出的动作；缺失项保留为待分段，不删除任何源数据。"""
import argparse
import hashlib
import json
from pathlib import Path

from data.runtime.motion_catalog import MotionCatalog
from data.runtime.unreal_dataset import file_sha256, read_json


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run',required=True)
    parser.add_argument('--export',required=True)
    args=parser.parse_args()
    run=Path(args.run).resolve()
    exported=Path(args.export).resolve()
    request=read_json(run/'soma_batch_request.json')
    checks=read_json(run/'export_audit/audit.json')
    batch=read_json(exported/'batch_report.json')
    manifest=read_json(exported/'manifest.json')
    targets={r['asset'] for r in read_json(run/'soma_batch_retarget_report.json')}
    successful={r['asset'] for r in checks}
    missing={r['asset'] for r in batch['skipped_clips']}
    if successful & missing or successful | missing != targets or len(checks)!=len(manifest['clips']):
        raise ValueError('导出身份集合不完整')
    clips={c['sha256'][:24]:c for c in request['clips']}
    catalog=MotionCatalog('D:/MotionDataLibrary')
    try:
        with catalog.db:
            for check, filename in zip(checks,manifest['clips']):
                key=check['asset'].split('.')[-1].removeprefix('UEFN_').removesuffix('x')
                if key not in clips:
                    # 身份不能精确还原的条目保持失败，禁止依赖返回顺序猜测来源。
                    continue
                clip=clips[key]
                output=run/'export_audit'/(Path(filename).stem+'.npz')
                if file_sha256(output)!=check['output_sha256'] or read_json(exported/filename)['asset']!=check['asset']:
                    raise ValueError('输出哈希或身份不符')
                mid=hashlib.sha256(('bones-seed:'+clip['name']).encode()).hexdigest()
                result=catalog.db.execute("UPDATE motions SET state='retargeted_pending_quality',error=NULL,output_path=?,output_sha256=? WHERE id=? AND state='retarget_failed'",(str(output),check['output_sha256'],mid))
                if result.rowcount!=1:
                    raise ValueError('恢复对象状态变化')
                catalog.db.execute('INSERT INTO events(motion_id,state,detail) VALUES(?,?,?)',(mid,'retargeted_pending_quality',json.dumps({'run': str(run),'audit': check,'source_sha256': clip['sha256']})))
            for item in batch['skipped_clips']:
                if '250000' not in item['error']:
                    raise ValueError('未支持的跳过原因')
                clip=clips[item['asset'].split('.')[-1].removeprefix('UEFN_')]
                mid=hashlib.sha256(('bones-seed:'+clip['name']).encode()).hexdigest()
                catalog.db.execute("UPDATE motions SET state='needs_segmentation',error=? WHERE id=? AND state='retarget_failed'",(item['error'],mid))
        print(json.dumps(catalog.status()))
    finally:
        catalog.close()


if __name__=='__main__':
    main()
