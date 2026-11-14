"""用户批次验收后的持久发布和逐文件清理计划；默认只做计划。"""
import argparse
import json
import os
import shutil
import sqlite3
from pathlib import Path

import numpy as np
from data.runtime.motion_catalog import MotionCatalog
from data.runtime.unreal_dataset import UnrealMotionDataset, file_sha256


def write_json(path, value):
    temp=path.with_suffix(path.suffix+'.partial')
    with temp.open('w',encoding='utf-8') as stream:
        json.dump(value,stream,ensure_ascii=False,indent=2)
        stream.flush()
        os.fsync(stream.fileno())
    temp.replace(path)


def durable_copy(source,target,digest):
    if target.exists():
        if file_sha256(target)!=digest:
            raise ValueError('目标冲突：'+str(target))
        return
    target.parent.mkdir(parents=True,exist_ok=True)
    temp=target.with_suffix(target.suffix+'.partial')
    with source.open('rb') as inp,temp.open('wb') as out:
        shutil.copyfileobj(inp,out,1024*1024)
        out.flush()
        os.fsync(out.fileno())
    if file_sha256(temp)!=digest:
        raise ValueError('复制哈希失败')
    temp.replace(target)


def close(library,validation_path,cohort_path=None,commit=False,auto_stage=False,auto_accept=False):
    library=Path(library).resolve()
    validation_path=Path(validation_path).resolve()
    cohort_path=Path(cohort_path).resolve() if cohort_path else None
    if not validation_path.is_relative_to(library/'work') or (cohort_path and cohort_path.parent!=library/'reviews'):
        raise ValueError('回执路径越界')
    automatic=auto_stage or auto_accept
    if auto_stage and auto_accept:
        raise ValueError('自动暂存与自动正式入库不能同时启用')
    if automatic and cohort_path:
        raise ValueError('自动模式不接受人工名单')
    if not automatic and not cohort_path:
        raise ValueError('正式入库需要人工验收名单')
    report=json.loads(validation_path.read_text(encoding='utf-8'))
    screen_path=library/'reports/pending_quality_screen.json'
    screen=json.loads(screen_path.read_text(encoding='utf-8'))
    cohort=json.loads(cohort_path.read_text(encoding='utf-8')) if cohort_path else None
    if cohort and file_sha256(screen_path)!=cohort['screen_sha256']:
        raise ValueError('人工验收报告已变化')
    bindings={item['id']:item for item in report['bindings']}
    if len(bindings)!=report['count'] or set(bindings)!={r['id'] for r in screen['reports']}:
        raise ValueError('训练回执与整批初筛范围不一致')
    if any(r['integrity_errors'] for r in screen['reports']):
        raise ValueError('完整性错误不能被人工通过覆盖')
    unresolved={'heading_not_validated','contacts_not_validated','visual_quality_not_validated'}
    if auto_accept and any(set(r['review_flags'])-unresolved for r in screen['reports']):
        raise ValueError('几何异常必须进入二审，禁止自动入库和清理')
    decisions={}
    if cohort:
        assert cohort_path is not None
        with sqlite3.connect(f'file:{library / "reviews/decisions.sqlite3"}?mode=ro',uri=True) as db:
            decisions={mid:{'status': status,'note': note,'updated': updated} for mid,status,note,updated in db.execute('SELECT id,status,note,updated FROM decisions WHERE cohort=?',(cohort_path.name,))}
        if not cohort['entries'] or any(decisions.get(e['id'],{}).get('status')!='approved' for e in cohort['entries']):
            raise ValueError('人工验收尚未全部通过')
        unresolved={'heading_not_validated','contacts_not_validated','visual_quality_not_validated'}
        issues={r['id'] for r in screen['reports'] if set(r['review_flags'])-unresolved}
        if not issues.issubset({e['id'] for e in cohort['entries']}):
            raise ValueError('异常项未全部进入人工名单')
    identity=file_sha256(validation_path)[:16]
    target=library/'motions'/('seed_stage_'+identity if auto_stage else 'seed_'+identity)
    target_state='auto_validated_pending_review' if auto_stage else 'accepted_imported'
    if auto_stage:
        acceptance='automated_integrity_and_training_roundtrip; pending_user_review_before_source_cleanup'
    elif auto_accept:
        acceptance='automated_integrity_and_training_roundtrip; user_authorized_immediate_publication_and_cleanup'
    else:
        acceptance='user_approved_all_flags_and_fixed_5_percent_sample_then_authorized_batch_publication'
    snapshot=Path(report['snapshot']).resolve()
    if snapshot!=validation_path.parent or not snapshot.is_relative_to(library/'work'):
        raise ValueError('训练快照路径无效')
    if file_sha256(snapshot/'dataset.json')!=report['manifest_sha256']:
        raise ValueError('训练清单已变化')
    dataset=UnrealMotionDataset(snapshot)
    if len(dataset)!=len(bindings):
        raise ValueError('训练加载条数不一致')
    catalog=MotionCatalog(library)
    cleanup={}
    items=[]
    def candidate(path,kind,expected=None):
        path=Path(path).resolve()
        if not path.is_file():
            raise ValueError('清理候选缺失：'+str(path))
        digest=file_sha256(path)
        if expected and digest!=expected:
            raise ValueError('源身份变化：'+str(path))
        cleanup[str(path)]={'path': str(path),'sha256': digest,'bytes': path.stat().st_size,'kind': kind}
    try:
        if catalog.db.execute("SELECT COUNT(*) FROM motions WHERE state='retarget_running'").fetchone()[0]:
            raise ValueError('存在在途重定向')
        runs={}
        for mid,binding in bindings.items():
            row=catalog.db.execute('SELECT name,metadata_json,state,output_path,output_sha256 FROM motions WHERE id=?',(mid,)).fetchone()
            if not row or row[2]!='retargeted_pending_quality' or row[4]!=binding['source_output_sha256']:
                raise ValueError('数据库动作状态或输出已变化：'+mid)
            path=Path(row[3]).resolve()
            if not path.is_relative_to(library/'work') or file_sha256(path)!=row[4]:
                raise ValueError('UE 输出身份无效')
            event=catalog.db.execute("SELECT detail FROM events WHERE motion_id=? AND state='retargeted_pending_quality' ORDER BY id DESC LIMIT 1",(mid,)).fetchone()
            evidence=json.loads(event[0])
            skeleton=path.parent/'skeleton.json'
            sk_hash=file_sha256(skeleton)
            for field,hash_field in [('file','sha256'),('raw_file','raw_sha256')]:
                saved=(snapshot/binding[field]).resolve()
                if not saved.is_relative_to(snapshot) or file_sha256(saved)!=binding[hash_field]:
                    raise ValueError('训练快照数据哈希变化')
            with np.load(path,allow_pickle=False) as archive:
                if not np.isfinite(archive['frames']).all() or not np.isfinite(archive['root_frames']).all():
                    raise ValueError('无效最终轨道')
            sources=[]
            for variant,source in catalog.db.execute('SELECT variant,path FROM sources WHERE motion_id=?',(mid,)):
                source=Path(source).resolve()
                allowed={variant:Path('D:/BONES-SEED',variant).resolve() for variant in ('soma_uniform','soma_proportional','g1')}
                if variant not in allowed or not source.is_relative_to(allowed[variant]) or source==allowed[variant]:
                    raise ValueError('原始数据物理路径越界')
                candidate(source,'source_'+variant,evidence['source_sha256'] if variant=='soma_uniform' else None)
                sources.append(dict(variant=variant,**cleanup[str(source)]))
            item={'id': mid,'name': row[0],'metadata': json.loads(row[1]),'source_output': str(path),'sha256': row[4],
                      'file': 'animations/'+mid+'.npz','skeleton': 'skeletons/'+sk_hash+'.json','skeleton_sha256': sk_hash,
                      'sources': sources,'ue_evidence': evidence,'review': decisions.get(mid,{'status': 'batch_sample_accepted'})}
            items.append(item)
            run=Path(evidence['run']).resolve()
            runs.setdefault(run,set()).add(mid)
            if commit:
                durable_copy(path,target/item['file'],row[4])
                durable_copy(skeleton,target/item['skeleton'],sk_hash)
        # 仅清理成员全在已验收范围内的工作目录；拒绝混合成功/失败批次。
        for run,mids in runs.items():
            if not run.is_relative_to(library/'work') or not run.name.startswith('queue_'):
                continue
            request_path=run/'soma_batch_request.json'
            if not request_path.exists():continue
            request=json.loads(request_path.read_text(encoding='utf-8'))
            names={item['name'] for item in request['clips']}
            if names!={item['name'] for item in items if item['id'] in mids}:continue
            for file in run.rglob('*'):
                if file.is_file():
                    if not file.resolve().is_relative_to(run):raise ValueError('工作目录链接越界')
                    candidate(file,'ue_work')
            # 确认生成包是本批 B_<hash>，只清理该包的 Source/Target 文件。
            batch=request['batch_id']
            if not batch.startswith('B_') or any(c not in '0123456789abcdef' for c in batch[2:]):
                raise ValueError('UE 批次身份非法')
            assets=Path('D:/GameAnimationSample/Content/MotionPipeline')/batch
            if assets.exists():
                for file in assets.rglob('*'):
                    if file.is_file():
                        if not file.resolve().is_relative_to(assets.resolve()):raise ValueError('UE 资产链接越界')
                        candidate(file,'ue_asset')
        plan={'schema_version': 1,'batch': identity,'target': str(target),'count': len(items),
                  'acceptance': acceptance,
                  'known_contract': 'UE exported root orientation retained; no additional facing reinterpretation',
                  'training_snapshot': str(snapshot),'cohort': cohort,'decisions': decisions,'items': items,
                  'cleanup': list(cleanup.values()),'cleanup_bytes': sum(c['bytes'] for c in cleanup.values()),
                  'cleanup_deferred': auto_stage}
        plan_path=library/'reports'/('close_'+identity+'.json')
        if commit:
            # 保留可重新派生训练特征的无损 UE 轨道；批统计量快照不是跨批次模型统计量。
            review_state='pending' if auto_stage else ('user_authorized_automatic_acceptance' if auto_accept else 'approved')
            write_json(target/'manifest.json',{'schema_version': 1,'kind': 'uefn_motion_library','items': items,'acceptance': plan['acceptance'],'training_snapshot': str(snapshot),'human_review': review_state})
            for item in items:
                if file_sha256(target/item['file'])!=item['sha256'] or file_sha256(target/item['skeleton'])!=item['skeleton_sha256']:
                    raise ValueError('最终库回读失败')
            with catalog.db:
                for item in items:
                    catalog.db.execute("UPDATE motions SET state=?,output_path=?,output_sha256=? WHERE id=? AND state='retargeted_pending_quality'",(target_state,str(target/item['file']),item['sha256'],item['id']))
                    catalog.db.execute('INSERT INTO events(motion_id,state,detail) VALUES(?,?,?)',(item['id'],target_state,json.dumps({'batch': identity,'manifest': str(target/'manifest.json'),'acceptance': plan['acceptance']})))
            catalog.db.execute('PRAGMA wal_checkpoint(FULL)')
        plan['published']=commit
        write_json(plan_path,plan)
        print(json.dumps({'plan': str(plan_path),'count': len(items),'published': commit,'cleanup_gib': plan['cleanup_bytes']/1024**3,'target': str(target)}),flush=True)
    finally:
        catalog.close()


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--library',default='D:/MotionDataLibrary')
    parser.add_argument('--validation',required=True)
    parser.add_argument('--cohort')
    automatic=parser.add_mutually_exclusive_group()
    automatic.add_argument('--auto-stage',action='store_true',help='仅自动校验后暂存，保留源文件等待人工复核')
    automatic.add_argument('--auto-accept',action='store_true',help='技术校验通过后按用户授权正式入库，供后续精确清理')
    parser.add_argument('--commit',action='store_true')
    args=parser.parse_args()
    close(args.library,args.validation,args.cohort,args.commit,args.auto_stage,args.auto_accept)
