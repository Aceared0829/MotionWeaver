"""有界、单实例 UE 批次调度；输出隔离待验收，绝不删除原始动作。"""
import argparse
import json
import os
import shutil
import subprocess
import sys
import uuid
from pathlib import Path

from data.runtime.motion_catalog import MotionCatalog
from data.runtime.unreal_dataset import read_json
from motionweaver.repository import unreal_scripts

from data.tools.audit_ue_batch import audit
from data.tools.check_seed_sources import header
from data.tools.motion_batch_gate import batch_capacity, pending_count
from data.tools.portable_lock import lock_exclusive


def execute(command, log, env=None):
    with log.open('w',encoding='utf-8') as stream:
        result = subprocess.run(command,stdout=stream,stderr=subprocess.STDOUT,env=env,timeout=1800,creationflags=subprocess.CREATE_NO_WINDOW, check=False)
    if result.returncode:
        raise RuntimeError(f'进程退出码 {result.returncode}，日志：{log}')
    content = log.read_text(encoding='utf-8',errors='replace')
    if any(marker in content for marker in ['Assertion failed:', 'Ensure condition failed:', 'LogPython: Error:']):
        raise RuntimeError(f'引擎报告异常：{log}')


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--library',default='D:/MotionDataLibrary')
    parser.add_argument('--source',default='D:/BONES-SEED')
    parser.add_argument('--project',default='D:/GameAnimationSample/GameAnimationSample.uproject')
    parser.add_argument('--engine',default='D:/UE_5.8/Engine/Binaries/Win64/UnrealEditor-Cmd.exe')
    parser.add_argument('--max-batches',type=int,default=1)
    parser.add_argument('--max-frames-per-clip',type=int,default=6000,help='完整动作帧数预算，默认 6000；配合 1000000 骨骼采样预算，不自动分段')
    parser.add_argument('--batch-size',type=int,default=32,help='每个 UE 微批处理的动作数，默认 32；受总帧数上限约束')
    args=parser.parse_args()
    if args.max_batches<1:
        parser.error('--max-batches 必须大于 0')
    if not 2<=args.max_frames_per_clip<=12000:
        parser.error('--max-frames-per-clip 必须在 2 到 12000 之间')
    if not 1<=args.batch_size<=32:
        parser.error('--batch-size 必须在 1 到 32 之间')
    catalog=MotionCatalog(args.library)
    lock=(catalog.folder/'work/queue.lock').open('a+b')
    try:
        lock_exclusive(lock)
    except OSError:
        raise RuntimeError('已有批处理工作进程，拒绝重复启动')
    scripts=Path(__file__).resolve().parent
    try:
        # 崩溃遗留状态不自动覆盖已有 UE 资产，需要基于回执恢复。
        if catalog.db.execute("SELECT COUNT(*) FROM motions WHERE state='retarget_running'").fetchone()[0]:
            raise RuntimeError('存在未收尾批次，须先核对日志并恢复')
        for _ in range(args.max_batches):
            if (catalog.folder/'work/STOP').exists():
                break
            capacity=min(args.batch_size,max(0,500-pending_count(catalog.db)))
            if not capacity:
                (catalog.folder/'work/STOP').write_text('review_required: 500 pending motions; review, publish and verify cleanup before resuming',encoding='utf-8')
                break
            # 提前留出当前批次及验收工作空间，不等到硬安全线才停止领取任务。
            if shutil.disk_usage(catalog.folder).free<44*1024**3:
                (catalog.folder/'work/STOP').write_text('disk_headroom: D below 44 GiB; validate and reclaim before resuming',encoding='utf-8')
                break
            if shutil.disk_usage(Path(os.environ.get('LOCALAPPDATA','C:/'))).free<14*1024**3:
                (catalog.folder/'work/STOP').write_text('disk_headroom: system below 14 GiB; validate and reclaim before resuming',encoding='utf-8')
                break
            candidates=catalog.db.execute("SELECT id,metadata_json FROM motions WHERE dataset='bones-seed' AND state='source_header_checked' ORDER BY id LIMIT 128").fetchall()
            rows=[]
            total=0
            for motion_id,metadata in candidates:
                row=json.loads(metadata)
                count=int(row['move_duration_frames'])
                # 显式提高完整动作导出预算；超预算保留原动作，不隐式切片或截断。
                if count>args.max_frames_per_clip:
                    with catalog.db:
                        catalog.db.execute("UPDATE motions SET state='needs_segmentation' WHERE id=?",(motion_id,))
                    continue
                path=(Path(args.source)/row['move_soma_uniform_path']).resolve()
                if not path.is_relative_to((Path(args.source)/'soma_uniform').resolve()):
                    raise ValueError('源路径越界')
                signature,_=header(path)
                if signature!='b489b161dbd742cc821e333b584db45058c8f5d813a8eb7cb2d8e81427cc2b84':
                    with catalog.db:
                        catalog.db.execute("UPDATE motions SET state='needs_skeleton_review' WHERE id=?",(motion_id,))
                    continue
                if total+count>20000 and rows:
                    break
                rows.append((motion_id,row))
                total+=count
                if len(rows)==capacity:
                    break
            if not rows:
                if candidates:
                    continue
                break
            run=catalog.folder/'work'/('queue_'+uuid.uuid4().hex)
            run.mkdir()
            selection=run/'selection.json'
            selection.write_text(json.dumps([row for _,row in rows]),encoding='utf-8')
            with catalog.db:
                for motion_id,_ in rows:
                    catalog.db.execute("UPDATE motions SET state='retarget_running',error=NULL WHERE id=?",(motion_id,))
                    catalog.db.execute('INSERT INTO events(motion_id,state,detail) VALUES(?,?,?)',(motion_id,'retarget_running',str(run)))
            try:
                execute([sys.executable,str(scripts/'prepare_soma_ue_batch.py'),'--source',args.source,'--work',str(run),'--selection',str(selection)],run/'prepare.log')
                request=read_json(run/'soma_batch_request.json')
                env=dict(os.environ,MOTION_PIPELINE_WORK_ROOT=str(run),MOTION_PIPELINE_TARGET_REVISION='TargetQueue')
                env.pop('MOTION_PIPELINE_REUSE_SOURCE',None)
                base=[args.engine,args.project,'-EnablePlugins=USDImporter','-DisablePlugins=MotionWeaverCMCPreview,AIAnimation','-unattended','-nop4','-NullRHI','-AILocomotionMaxBoneSamples=1000000',
                      '-ini:Editor:[/Script/AILocomotionDatasetEditor.AILocomotionDatasetSettings]:MaxFramesPerClip='+str(args.max_frames_per_clip),
                      '-ZenDataPath='+str(catalog.folder/'work/zen')]
                execute(base+['-run=pythonscript','-script='+str(unreal_scripts()/'retarget_soma_batch.py')],run/'retarget.log',env)
                report=read_json(run/'soma_batch_retarget_report.json')
                if len(report)!=len(rows):
                    raise ValueError('重定向输出数与请求不符')
                export_root=Path(args.project).parent/'Saved/AILocomotionDataset'
                previous={p.name for p in export_root.iterdir() if p.is_dir()}
                target='/Game/MotionPipeline/'+request['batch_id']+'/TargetQueue'
                execute(base+['-NoSplash','-AILocomotionExportPath='+target],run/'export.log',env)
                exports=[p for p in export_root.iterdir() if p.is_dir() and p.name not in previous and (p/'manifest.json').exists()]
                if len(exports)!=1:
                    raise ValueError('无法唯一确定当前UE导出批次')
                audit(exports[0],run/'export_audit')
                checks=read_json(run/'export_audit/audit.json')
                expected={item['asset'] for item in report}
                if {item['asset'] for item in checks}!=expected or len(checks)!=len(rows):
                    raise ValueError('导出身份或数量不符')
                mapped={c['sha256'][:24]:c for c in request['clips']}
                with catalog.db:
                    for index,item in enumerate(checks):
                        key=item['asset'].split('.')[-1].removeprefix('UEFN_').removesuffix('x')
                        clip=mapped[key]
                        motion_id=next(mid for mid,row in rows if row['filename']==clip['name'])
                        output=run/'export_audit'/f'clip_{index:05d}.npz'
                        catalog.db.execute("UPDATE motions SET state='retargeted_pending_quality',output_path=?,output_sha256=? WHERE id=?",(str(output),item['output_sha256'],motion_id))
                        catalog.db.execute('INSERT INTO events(motion_id,state,detail) VALUES(?,?,?)',(motion_id,'retargeted_pending_quality',json.dumps({'run': str(run),'source_sha256': clip['sha256'],'audit': item})))
                (run/'receipt.json').write_text(json.dumps({'count': len(rows),'stage': 'retargeted_pending_quality','training_ready': False,'source_deleted': False}),encoding='utf-8')
                print(json.dumps({'run': str(run),'count': len(rows),'states': catalog.status()}),flush=True)
                if not batch_capacity(pending_count(catalog.db)):
                    (catalog.folder/'work/STOP').write_text('review_required: 500 pending motions; review, publish and verify cleanup before resuming',encoding='utf-8')
                    break
            except Exception as exc:
                with catalog.db:
                    for motion_id,_ in rows:
                        catalog.db.execute("UPDATE motions SET state='retarget_failed',error=? WHERE id=?",(str(exc),motion_id))
                raise
        if (catalog.folder/'work/STOP').exists() and pending_count(catalog.db):
            # 初筛故障不能把已完成重定向的动作倒退标记为 retarget_failed。
            execute([sys.executable,str(scripts/'screen_pending_quality.py'),'--library',str(catalog.folder)],catalog.folder/'work/review_screen.log')
    finally:
        lock.close()
        catalog.close()


if __name__=='__main__':
    main()
