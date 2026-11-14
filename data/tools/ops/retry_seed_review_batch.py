"""仅重新处理用户指定的二审名单；完整长片段、新 UE 输出与源文件全部保留。"""
import argparse
import json
import os
import shutil
import sys
import uuid
from pathlib import Path

from data.runtime.motion_catalog import MotionCatalog
from data.runtime.unreal_dataset import file_sha256, read_json
from motionweaver.repository import unreal_scripts

from data.tools.audit.audit_ue_batch import audit
from data.tools.publish.close_seed_batch import write_json
from data.tools.common.portable_lock import lock_exclusive
from data.tools.ops.run_seed_queue import execute
from data.tools.audit.screen_pending_quality import screen


def retry(library, source, cohort, engine, project):
    library,source,cohort=Path(library).resolve(),Path(source).resolve(),Path(cohort).resolve()
    if cohort.parent!=library/'reviews':raise ValueError('名单路径越界')
    frozen=read_json(cohort)
    entries=frozen['entries']
    if len(entries)!=10 or len({e['id'] for e in entries})!=10:
        raise ValueError('本次仅处理用户指定的 10 条拒绝动作')
    catalog=MotionCatalog(library)
    lock=(library/'work/queue.lock').open('a+b')
    lock_exclusive(lock)
    session=library/'work'/('repair10_'+uuid.uuid4().hex)
    session.mkdir()
    write_json(session/'authorization_scope.json',{'cohort': str(cohort),'cohort_sha256': file_sha256(cohort),
               'ids': [e['id'] for e in entries],'source_deleted': False,'publication': False,'segmentation': False})
    try:
        if catalog.db.execute("SELECT COUNT(*) FROM motions WHERE state='retarget_running'").fetchone()[0]:
            raise ValueError('有在途批次，拒绝并行修改 UE 项目')
        selected=[]
        for entry in entries:
            mid=entry['id']
            row=catalog.db.execute('SELECT metadata_json,state FROM motions WHERE id=?',(mid,)).fetchone()
            if not row or row[1]!=entry['state']:
                raise ValueError('名单动作状态已变化：'+mid)
            metadata=json.loads(row[0])
            path=(source/metadata['move_soma_uniform_path']).resolve()
            if not path.is_relative_to(source/'soma_uniform') or file_sha256(path)!=entry['source_sha256']:
                raise ValueError('源身份变化：'+mid)
            if int(metadata['move_duration_frames'])>6000:
                raise ValueError('本次已验证预算只支持不超过 6000 帧')
            selected.append((mid,metadata,entry['state']=='needs_skeleton_review'))
        results=[]
        for normalized in (False,True):
            rows=[(mid,row) for mid,row,norm in selected if norm==normalized]
            if not rows:continue
            if len(rows)>8:raise ValueError('UE 微批超过 8 条')
            if shutil.disk_usage(library).free<44*1024**3 or shutil.disk_usage(Path(os.environ['LOCALAPPDATA'])).free<14*1024**3:
                raise RuntimeError('磁盘安全水位不足')
            run=library/'work'/('queue_repair_'+uuid.uuid4().hex)
            run.mkdir()
            write_json(run/'selection.json',[row for _,row in rows])
            command=[sys.executable,str(Path(__file__).resolve().parents[1]/'ingest'/'prepare_soma_ue_batch.py'),'--source',str(source),'--work',str(run),'--selection',str(run/'selection.json')]
            if normalized:command.append('--normalize-reference-offsets')
            execute(command,run/'prepare.log')
            request=read_json(run/'soma_batch_request.json')
            env=dict(os.environ,MOTION_PIPELINE_WORK_ROOT=str(run),MOTION_PIPELINE_TARGET_REVISION='TargetRepair')
            env.pop('MOTION_PIPELINE_REUSE_SOURCE',None)
            base=[engine,project,'-EnablePlugins=USDImporter','-DisablePlugins=MotionWeaverCMCPreview,AIAnimation','-unattended','-nop4','-NullRHI','-AILocomotionMaxBoneSamples=1000000',
                  '-ini:Editor:[/Script/AILocomotionDatasetEditor.AILocomotionDatasetSettings]:MaxFramesPerClip=6000','-ZenDataPath='+str(library/'work/zen')]
            print(json.dumps({'stage': 'retarget','count': len(rows),'normalized': normalized,'run': str(run)}),flush=True)
            execute(base+['-run=pythonscript','-script='+str(unreal_scripts()/'retarget_soma_batch.py')],run/'retarget.log',env)
            report=read_json(run/'soma_batch_retarget_report.json')
            if len(report)!=len(rows):raise ValueError('重定向数量不符')
            export_root=Path(project).parent/'Saved/AILocomotionDataset'
            previous={p.name for p in export_root.iterdir() if p.is_dir()}
            execute(base+['-NoSplash','-AILocomotionExportPath=/Game/MotionPipeline/'+request['batch_id']+'/TargetRepair'],run/'export.log',env)
            exports=[p for p in export_root.iterdir() if p.is_dir() and p.name not in previous and (p/'manifest.json').exists()]
            if len(exports)!=1:raise ValueError('无法唯一绑定当前 UE 导出')
            audit(exports[0],run/'export_audit')
            checks=read_json(run/'export_audit/audit.json')
            if len(checks)!=len(rows) or {c['asset'] for c in checks}!={r['asset'] for r in report}:
                raise ValueError('导出数量或资产身份不符')
            mapped={c['sha256'][:24]:c for c in request['clips']}
            batch_items=[]
            for index,item in enumerate(checks):
                key=item['asset'].split('.')[-1].removeprefix('UEFN_').removesuffix('x')
                clip=mapped[key]
                mid=next(mid for mid,row in rows if row['filename']==clip['name'])
                if item['frames']!=int(clip['metadata']['move_duration_frames']):
                    raise ValueError('完整动作帧数发生变化')
                output=run/'export_audit'/f'clip_{index:05d}.npz'
                if file_sha256(output)!=item['output_sha256']:raise ValueError('输出回读哈希变化')
                evidence={'run': str(run),'source_sha256': clip['sha256'],'audit': item,'repair_session': str(session),
                              'source_reference_normalized': normalized,'max_reference_correction_cm': clip['max_reference_correction_cm']}
                batch_items.append({'id': mid,'output': str(output),'sha256': item['output_sha256'],'evidence': evidence})
            write_json(run/'repair_receipt.json',{'items': batch_items,'source_deleted': False})
            with catalog.db:
                for item in batch_items:
                    catalog.db.execute("UPDATE motions SET state='needs_secondary_review',output_path=?,output_sha256=?,error=? WHERE id=?",
                                       (item['output'],item['sha256'],'重新处理完成；新输出需二审，旧拒绝结论保留',item['id']))
                    catalog.db.execute('INSERT INTO events(motion_id,state,detail) VALUES(?,?,?)',
                                       (item['id'],'retargeted_pending_quality',json.dumps(item['evidence'])))
                    catalog.db.execute('INSERT INTO events(motion_id,state,detail) VALUES(?,?,?)',
                                       (item['id'],'needs_secondary_review',str(run/'repair_receipt.json')))
            results.extend(batch_items)
            print(json.dumps({'stage': 'exported','count': len(batch_items),'run': str(run)}),flush=True)
        screen(library,[item['id'] for item in results],'needs_secondary_review','repair10_quality_screen.json')
        quality=read_json(library/'reports/repair10_quality_screen.json')
        with catalog.db:
            for item in quality['reports']:
                catalog.db.execute('UPDATE motions SET error=? WHERE id=? AND state=?',
                                   (json.dumps(item,ensure_ascii=False),item['id'],'needs_secondary_review'))
        write_json(session/'complete.json',{'items': results,'count': len(results),'segmented': False,'source_deleted': False,'training_ready': False})
        print(json.dumps({'stage': 'complete','count': len(results),'session': str(session)}),flush=True)
    finally:
        lock.close()
        catalog.close()


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--library',default='E:/AIAnimationSystemData/MotionDataLibrary')
    parser.add_argument('--source',default='E:/AIAnimationSystemData/BONES-SEED')
    parser.add_argument('--cohort',required=True)
    parser.add_argument('--engine',default='D:/UE_5.8/Engine/Binaries/Win64/UnrealEditor-Cmd.exe')
    parser.add_argument('--project',default='D:/GameAnimationSample/GameAnimationSample.uproject')
    args=parser.parse_args()
    retry(args.library,args.source,args.cohort,args.engine,args.project)
