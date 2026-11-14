"""将已人工拒绝的异常源动作迁入 D 盘隔离区；只移动逐文件哈希已绑定的来源。"""
import argparse
import hashlib
import json
import shutil
import sqlite3
from datetime import datetime, timezone
from pathlib import Path


def sha256(path):
    digest=hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda:stream.read(1024*1024),b''):
            digest.update(block)
    return digest.hexdigest()


def rejected_entries(library, manifest):
    files=sorted((library/'reviews').glob(f'exception_triage_{manifest[:16]}_p*.json'))
    if not files:
        raise ValueError('未找到异常验收名单')
    with sqlite3.connect(library/'reviews/decisions.sqlite3') as db:
        decisions={(cohort,mid):status for cohort,mid,status in db.execute('SELECT cohort,id,status FROM decisions')}
    entries=[]
    for path in files:
        cohort=json.loads(path.read_text(encoding='utf-8'))
        if cohort.get('manifest_sha256')!=manifest:
            raise ValueError(f'名单摘要不匹配：{path.name}')
        for entry in cohort['entries']:
            if decisions.get((path.name,entry['id']))!='rejected':
                raise ValueError(f'存在未明确拒绝的动作：{entry["id"]}')
            entries.append(entry)
    ids=[entry['id'] for entry in entries]
    if len(ids)!=len(set(ids)):
        raise ValueError('验收名单含重复动作')
    return entries


def plan(library, source_root, quarantine_root, manifest):
    entries=rejected_entries(library,manifest)
    ids={entry['id'] for entry in entries}
    source_root=source_root.resolve()
    proportional_root=Path('C:/BONES-SEED').resolve()
    with sqlite3.connect(f'file:{library / "catalog.sqlite3"}?mode=ro',uri=True) as db:
        marks=','.join('?' for _ in ids)
        rows=db.execute(f'SELECT motion_id,variant,path FROM sources WHERE motion_id IN ({marks}) ORDER BY motion_id,variant',tuple(sorted(ids))).fetchall()
        outputs=db.execute(f'SELECT id,output_path FROM motions WHERE id IN ({marks}) AND output_path IS NOT NULL',tuple(sorted(ids))).fetchall()
    if len(rows)!=len(ids)*3:
        raise ValueError(f'来源文件关联不完整：期望 {len(ids)*3}，实际 {len(rows)}')
    files=[]
    seen=set()
    for motion_id,variant,raw_text in rows:
        raw=Path(raw_text)
        if not raw.is_relative_to(source_root):
            raise ValueError(f'来源逻辑路径越界：{raw}')
        physical=raw.resolve()
        if not physical.is_relative_to(source_root) and not physical.is_relative_to(proportional_root):
            raise ValueError(f'来源物理路径越界：{physical}')
        if not physical.is_file():
            raise FileNotFoundError(f'来源缺失：{physical}')
        destination=quarantine_root/raw.relative_to(source_root)
        key=str(raw).lower()
        if key not in seen:
            files.append({'source': str(raw),'physical_source': str(physical),'destination': str(destination),'sha256': sha256(physical),'bytes': physical.stat().st_size})
            seen.add(key)
    if outputs:
        raise ValueError('被拒绝异常项意外存在 UE 输出；拒绝扩大清理范围，请先人工核对')
    return {'version': 1,'created_at': datetime.now(timezone.utc).isoformat(),'kind': 'rejected_exception_quarantine','manifest_sha256': manifest,
                'motion_ids': sorted(ids),'source_root': str(source_root),'quarantine_root': str(quarantine_root),'files': files,
                'intermediate_cleanup': [],'notes': '仅移动已人工拒绝的来源文件；不删除隔离副本、不触碰共享骨骼或其他批次。'}


def execute(library, report):
    report_path=library/'reports'/f'quarantine_{report["manifest_sha256"][:16]}.json'
    journal_path=library/'reports'/f'quarantine_{report["manifest_sha256"][:16]}.journal.jsonl'
    report_path.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    for item in report['files']:
        source=Path(item['source'])
        physical=Path(item['physical_source'])
        destination=Path(item['destination'])
        destination.parent.mkdir(parents=True,exist_ok=True)
        if destination.exists():
            if sha256(destination)!=item['sha256']:
                raise ValueError(f'隔离目标哈希冲突：{destination}')
            if physical.exists():
                if sha256(physical)!=item['sha256']:
                    raise ValueError(f'原始来源在移动前已变化：{physical}')
                physical.unlink()
        else:
            if not physical.exists() or sha256(physical)!=item['sha256']:
                raise ValueError(f'原始来源在移动前已变化或缺失：{physical}')
            shutil.move(str(physical),str(destination))
            if sha256(destination)!=item['sha256']:
                raise ValueError(f'隔离后哈希不匹配：{destination}')
        with journal_path.open('a',encoding='utf-8') as journal:
            journal.write(json.dumps({'source': str(source),'physical_source': str(physical),'destination': str(destination),'sha256': item['sha256'],'status': 'moved_or_verified'},ensure_ascii=False)+'\n')
    with sqlite3.connect(library/'catalog.sqlite3') as db:
        with db:
            for motion_id in report['motion_ids']:
                db.execute("UPDATE motions SET state='rejected_quarantined',error=? WHERE id=?",('人工验收不通过；来源已迁入 D 盘隔离区',motion_id))
                db.execute('INSERT INTO events(motion_id,state,detail) VALUES(?,?,?)',(motion_id,'rejected_quarantined',str(report_path)))
            for item in report['files']:
                source=Path(item['source'])
                destination=Path(item['destination'])
                db.execute('UPDATE sources SET path=? WHERE path=?',(str(destination),str(source)))
    print(json.dumps({'report': str(report_path),'journal': str(journal_path),'motions': len(report['motion_ids']),'files': len(report['files']),'bytes': sum(item['bytes'] for item in report['files']),'intermediate_cleanup': 0},ensure_ascii=False))


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--library',default='D:/MotionDataLibrary')
    parser.add_argument('--source',default='D:/BONES-SEED')
    parser.add_argument('--manifest',required=True)
    parser.add_argument('--quarantine',default=None)
    parser.add_argument('--execute',action='store_true')
    args=parser.parse_args()
    library=Path(args.library).resolve()
    quarantine=Path(args.quarantine).resolve() if args.quarantine else library/'quarantine'/f'rejected_{args.manifest[:16]}'
    report=plan(library,Path(args.source),quarantine,args.manifest)
    if args.execute:
        execute(library,report)
    else:
        print(json.dumps({'motions': len(report['motion_ids']),'files': len(report['files']),'bytes': sum(item['bytes'] for item in report['files']),'quarantine': str(quarantine),'intermediate_cleanup': len(report['intermediate_cleanup'])},ensure_ascii=False))


if __name__=='__main__':
    main()
