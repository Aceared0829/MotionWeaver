"""将固定名单人工结论与当前输出身份绑定存档；不发布或删除。"""
import argparse
import hashlib
import json
import sqlite3
from pathlib import Path

from data.runtime.unreal_dataset import file_sha256


def record(library, cohort_path):
    library=Path(library).resolve()
    cohort_path=Path(cohort_path).resolve()
    if cohort_path.parent != library/'reviews':
        raise ValueError('名单不在 reviews 目录')
    cohort=json.loads(cohort_path.read_text(encoding='utf-8'))
    screen=library/'reports/pending_quality_screen.json'
    if file_sha256(screen)!=cohort['screen_sha256']:
        raise ValueError('初筛报告已变化，需要重新核对名单')
    with sqlite3.connect(f'file:{library / "reviews/decisions.sqlite3"}?mode=ro',uri=True) as reviews:
        decisions={mid:{'status': status,'note': note,'updated': updated} for mid,status,note,updated in reviews.execute('SELECT id,status,note,updated FROM decisions WHERE cohort=?',(cohort_path.name,))}
    entries=[]
    with sqlite3.connect(f'file:{library / "catalog.sqlite3"}?mode=ro',uri=True) as db:
        for entry in cohort['entries']:
            mid=entry['id']
            decision=decisions.get(mid,{'status': 'unreviewed','note': ''})
            row=db.execute('SELECT output_path,output_sha256,state FROM motions WHERE id=?',(mid,)).fetchone()
            if not row or row[2]!='retargeted_pending_quality':
                raise ValueError('动作状态已变化：'+mid)
            output=Path(row[0]).resolve()
            if not output.is_relative_to(library) or file_sha256(output)!=row[1]:
                raise ValueError('输出身份已变化：'+mid)
            audits=json.loads((output.parent/'audit.json').read_text(encoding='utf-8'))
            if sum(item.get('output_sha256')==row[1] for item in audits)!=1:
                raise ValueError('缺少唯一 UE 审计回执：'+mid)
            entries.append({'id': mid,'group': entry['group'],'decision': decision,'output_sha256': row[1],'skeleton_sha256': file_sha256(output.parent/'skeleton.json')})
    result={'cohort': cohort_path.name,'cohort_sha256': file_sha256(cohort_path),'entries': entries,
                'all_marked': all(e['decision']['status']!='unreviewed' for e in entries),
                'all_approved': bool(entries) and all(e['decision']['status']=='approved' for e in entries),
                'training_ready': False,'source_deleted': False,
                'remaining_gates': ['root_heading_contract','contact_quality','final_training_loader_roundtrip','durable_publication','recoverable_cleanup']}
    payload=json.dumps(result,ensure_ascii=False,indent=2)
    digest=hashlib.sha256(payload.encode()).hexdigest()
    dest=library/'reviews'/('receipt_'+digest[:16]+'.json')
    if not dest.exists():
        with dest.open('x',encoding='utf-8') as stream:
            stream.write(payload)
    print(json.dumps({'receipt': str(dest),'count': len(entries),'all_approved': result['all_approved'],'training_ready': False}))
    return result


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--library',default='D:/MotionDataLibrary')
    parser.add_argument('--cohort',required=True)
    args=parser.parse_args()
    record(args.library,args.cohort)
