"""执行已发布批次的逐文件清理清单；哈希预检与持久日志支持中断恢复。"""
import argparse
import json
from pathlib import Path

from data.runtime.motion_catalog import MotionCatalog
from data.runtime.unreal_dataset import UnrealMotionDataset, file_sha256


def cleanup(library,plan_path,execute=False):
    library=Path(library).resolve()
    plan_path=Path(plan_path).resolve()
    if plan_path.parent!=library/'reports':raise ValueError('清理计划越界')
    plan=json.loads(plan_path.read_text(encoding='utf-8'))
    if not plan.get('published'):raise ValueError('未发布计划不能清理')
    target=Path(plan['target']).resolve()
    if target.parent!=library/'motions' or target.name!='seed_'+plan['batch']:
        raise ValueError('最终库路径非法')
    catalog=MotionCatalog(library)
    try:
        catalog.db.execute('CREATE TABLE IF NOT EXISTS cleanup_journal(batch TEXT,path TEXT,sha256 TEXT,status TEXT,PRIMARY KEY(batch,path))')
        dataset=UnrealMotionDataset(plan['training_snapshot'])
        if len(dataset)!=plan['count']:raise ValueError('训练读取条数变化')
        allowed_sources={s['path']:s['sha256'] for item in plan['items'] for s in item['sources']}
        allowed_runs={Path(item['ue_evidence']['run']).resolve() for item in plan['items']}
        allowed_assets=set()
        for run in allowed_runs:
            request=run/'soma_batch_request.json'
            if request.exists():
                batch=json.loads(request.read_text(encoding='utf-8'))['batch_id']
                if batch.startswith('B_') and all(c in '0123456789abcdef' for c in batch[2:]):
                    allowed_assets.add((Path('D:/GameAnimationSample/Content/MotionPipeline')/batch).resolve())
        for item in plan['items']:
            row=catalog.db.execute('SELECT state,output_path,output_sha256 FROM motions WHERE id=?',(item['id'],)).fetchone()
            output=(target/item['file']).resolve()
            skeleton=(target/item['skeleton']).resolve()
            if not output.is_relative_to(target) or not skeleton.is_relative_to(target):raise ValueError('最终文件越界')
            if not row or row[0]!='accepted_imported' or Path(row[1]).resolve()!=output or row[2]!=item['sha256']:
                raise ValueError('最终数据库记录不匹配')
            if file_sha256(output)!=item['sha256'] or file_sha256(skeleton)!=item['skeleton_sha256']:
                raise ValueError('最终文件哈希不匹配')
        # 所有候选先校验，再开始删除；不使用递归删除，也不删除空目录或共享骨架资产。
        for entry in plan['cleanup']:
            path=Path(entry['path'])
            if path.resolve()!=path:raise ValueError('清理路径解析变化')
            kind=entry['kind']
            allowed=False
            if kind.startswith('source_'):
                roots=[Path('D:/BONES-SEED/soma_uniform'),Path('D:/BONES-SEED/g1'),Path('D:/BONES-SEED/soma_proportional')]
                allowed=allowed_sources.get(str(path))==entry['sha256'] and any(path.is_relative_to(r.resolve()) and path!=r.resolve() for r in roots)
            elif kind=='ue_work':
                allowed=any(run.is_relative_to(library/'work') and run.name.startswith('queue_') and path.is_relative_to(run) for run in allowed_runs)
            elif kind=='ue_asset':
                # 已开始清理后请求文件可能不存在；只允许之前已有持久清理意向的精确文件恢复。
                allowed=any(path.is_relative_to(root) for root in allowed_assets)
                if not allowed:
                    allowed=bool(catalog.db.execute('SELECT 1 FROM cleanup_journal WHERE batch=? AND path=? AND sha256=?',(plan['batch'],str(path),entry['sha256'])).fetchone())
            elif kind=='ue_export':
                expected={item['ue_evidence']['audit']['asset']:item['ue_evidence']['audit']['ue_export_sha256'] for item in plan['items']}
                allowed=path.is_relative_to(Path('D:/GameAnimationSample/Saved/AILocomotionDataset').resolve()) and expected.get(entry.get('asset'))==entry['sha256']
            if not allowed or path.is_relative_to(target):raise ValueError('清理边界不通过：'+str(path))
            if path.exists():
                if not path.is_file() or file_sha256(path)!=entry['sha256']:raise ValueError('候选哈希变化：'+str(path))
            elif not catalog.db.execute('SELECT 1 FROM cleanup_journal WHERE batch=? AND path=? AND sha256=?',(plan['batch'],str(path),entry['sha256'])).fetchone():
                raise ValueError('文件无日志却已缺失：'+str(path))
        if execute:
            with catalog.db:
                for entry in plan['cleanup']:
                    catalog.db.execute("INSERT OR IGNORE INTO cleanup_journal VALUES(?,?,?,'planned')",(plan['batch'],entry['path'],entry['sha256']))
            for entry in plan['cleanup']:
                path=Path(entry['path'])
                if path.exists():
                    if path.resolve()!=path or file_sha256(path)!=entry['sha256']:raise ValueError('删除前身份变化')
                    path.unlink()
                with catalog.db:
                    catalog.db.execute("UPDATE cleanup_journal SET status='deleted' WHERE batch=? AND path=?",(plan['batch'],str(path)))
            with catalog.db:
                for item in plan['items']:
                    detail=json.dumps({'batch': plan['batch'],'plan': str(plan_path)})
                    if not catalog.db.execute("SELECT 1 FROM events WHERE motion_id=? AND state='source_cleanup_complete' AND detail=?",(item['id'],detail)).fetchone():
                        catalog.db.execute('INSERT INTO events(motion_id,state,detail) VALUES(?,?,?)',(item['id'],'source_cleanup_complete',detail))
            catalog.db.execute('PRAGMA wal_checkpoint(FULL)')
        print(json.dumps({'executed': execute,'files': len(plan['cleanup']),'gib': plan['cleanup_bytes']/1024**3,'batch': plan['batch']}),flush=True)
    finally:catalog.close()


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--library',default='D:/MotionDataLibrary')
    parser.add_argument('--plan',required=True)
    parser.add_argument('--execute',action='store_true')
    args=parser.parse_args()
    cleanup(args.library,args.plan,args.execute)
