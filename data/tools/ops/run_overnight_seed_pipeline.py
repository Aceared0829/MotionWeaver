"""持续 UE 批处理；异常保留二审，通过项发布后按哈希清单清理。"""
import argparse
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import time
from contextlib import closing
from pathlib import Path

from data.tools.common.portable_lock import lock_exclusive

ROOT = Path(__file__).resolve().parent
TOOLS = ROOT.parent
UNVERIFIED = {'heading_not_validated', 'contacts_not_validated', 'visual_quality_not_validated'}


def states(library):
    with closing(sqlite3.connect(library / 'catalog.sqlite3')) as db, db:
        return dict(db.execute('SELECT state,COUNT(*) FROM motions GROUP BY state'))


def execute(command, log):
    with log.open('w', encoding='utf-8') as stream:
        result = subprocess.run(command, stdout=stream, stderr=subprocess.STDOUT,
                                timeout=7200, creationflags=subprocess.CREATE_NO_WINDOW, check=False)
    if result.returncode:
        raise RuntimeError('阶段失败：' + str(log))
    return log.read_text(encoding='utf-8', errors='replace')


def isolate_issues(library):
    """只改状态和存证；异常源、输出以及混合批次资产均保留。"""
    screen = library / 'reports/pending_quality_screen.json'
    report = json.loads(screen.read_text(encoding='utf-8'))
    issues = [item for item in report['reports']
              if item['integrity_errors'] or set(item['review_flags']) - UNVERIFIED]
    from data.runtime.unreal_dataset import file_sha256

    from data.tools.publish.close_seed_batch import write_json
    digest = file_sha256(screen)
    evidence = library / 'reviews' / ('secondary_' + digest[:16] + '.json')
    evidence.parent.mkdir(exist_ok=True)
    if issues:
        write_json(evidence, {'screen_sha256': digest, 'entries': issues, 'source_deleted': False})
        with closing(sqlite3.connect(library / 'catalog.sqlite3')) as db, db:
            for item in issues:
                changed = db.execute("UPDATE motions SET state='needs_secondary_review',error=? WHERE id=? AND state='retargeted_pending_quality'",
                                     (json.dumps(item, ensure_ascii=False), item['id'])).rowcount
                if changed:
                    db.execute('INSERT INTO events(motion_id,state,detail) VALUES(?,?,?)',
                               (item['id'], 'needs_secondary_review', str(evidence)))
    return len(issues)


def disk_ok(library):
    return (shutil.disk_usage(library).free >= 44 * 1024**3
            and shutil.disk_usage(Path(os.environ.get('LOCALAPPDATA', 'C:/'))).free >= 14 * 1024**3)


def start_queue(library, source):
    stop = library / 'work/STOP'
    if stop.exists():
        reason = stop.read_text(encoding='utf-8')
        if not reason.startswith('review_required:'):
            raise RuntimeError('保留非验收 STOP，请检查：' + reason)
        stop.replace(library / 'reports' / ('STOP_resumed_' + str(time.time_ns()) + '.txt'))
    log = library / 'work' / ('overnight_queue_' + str(time.time_ns()) + '.log')
    command = [sys.executable, '-u', str(ROOT / 'run_seed_queue.py'), '--library', str(library),
               '--source', str(source), '--max-batches', '1000', '--batch-size', '32']
    with log.open('w', encoding='utf-8') as stream:
        child = subprocess.Popen(command, cwd=ROOT.parents[2], stdout=stream,
                                 stderr=subprocess.STDOUT, creationflags=subprocess.CREATE_NO_WINDOW)
    print(json.dumps({'stage': 'queue_started', 'pid': child.pid, 'log': str(log)}), flush=True)
    return child


def finish_batch(library):
    execute([sys.executable, str(TOOLS / 'audit' / 'screen_pending_quality.py'), '--library', str(library)], library / 'work/overnight_screen.log')
    isolated = isolate_issues(library)
    print(json.dumps({'stage': 'secondary_review', 'isolated': isolated}), flush=True)
    if not states(library).get('retargeted_pending_quality', 0):
        return
    execute([sys.executable, str(TOOLS / 'audit' / 'screen_pending_quality.py'), '--library', str(library)], library / 'work/overnight_screen.log')
    try:
        result = execute([sys.executable, str(TOOLS / 'audit' / 'validate_seed_training.py'), '--library', str(library)], library / 'work/overnight_training.log')
    except Exception:
        # 无法可靠定位单条失败时，整批保留二审，不能猜测通过项后删除。
        with closing(sqlite3.connect(library / 'catalog.sqlite3')) as db, db:
            rows=db.execute("SELECT id FROM motions WHERE state='retargeted_pending_quality'").fetchall()
            for (mid,) in rows:
                db.execute("UPDATE motions SET state='needs_secondary_review',error=? WHERE id=?",
                           ('训练回读失败；整批保留。日志：'+str(library/'work/overnight_training.log'),mid))
                db.execute('INSERT INTO events(motion_id,state,detail) VALUES(?,?,?)',
                           (mid,'needs_secondary_review',str(library/'work/overnight_training.log')))
        raise
    validation = json.loads(result.splitlines()[-1])['report']
    result = execute([sys.executable, str(TOOLS / 'publish' / 'close_seed_batch.py'), '--library', str(library), '--validation', validation, '--auto-accept', '--commit'], library / 'work/overnight_stage.log')
    stage = json.loads(result.splitlines()[-1])
    execute([sys.executable, str(TOOLS / 'publish' / 'promote_seed_training.py'), '--plan', stage['plan']], library / 'work/overnight_promote.log')
    execute([sys.executable, str(TOOLS / 'publish' / 'cleanup_seed_batch.py'), '--library', str(library), '--plan', stage['plan'], '--execute'], library / 'work/overnight_cleanup.log')
    print(json.dumps(dict(stage='published_and_cleaned', **stage)), flush=True)


def run(library, source, once=False):
    library, source = Path(library).resolve(), Path(source).resolve()
    lock = (library / 'work/overnight.lock').open('a+b')
    lock_exclusive(lock)
    worker = None
    try:
        with (library / 'work/queue.lock').open('a+b') as queue_lock:
            lock_exclusive(queue_lock)
        while True:
            stop = library / 'work/STOP'
            if stop.exists() and not stop.read_text(encoding='utf-8').startswith('review_required:'):
                print(json.dumps({'stage': 'paused', 'reason': stop.read_text(encoding='utf-8')}), flush=True)
                return
            if worker is not None:
                if worker.poll() is None:
                    time.sleep(10)
                    continue
                if worker.returncode:
                    raise RuntimeError('UE 队列失败；保留全部源和工作文件，请检查队列日志')
                worker = None
            current = states(library)
            if current.get('retarget_running', 0) or current.get('retarget_failed', 0):
                raise RuntimeError('存在未收尾或失败动作，保留供二审并停止自动清理')
            if not disk_ok(library):
                raise RuntimeError('磁盘低于安全水位，停止处理与清理')
            pending = current.get('retargeted_pending_quality', 0)
            if pending and (pending >= 500 or (library / 'work/STOP').exists()
                            or not current.get('source_header_checked', 0)):
                finish_batch(library)
                if once:
                    return
                continue
            if not current.get('source_header_checked', 0):
                print(json.dumps({'stage': 'queue_exhausted', 'states': current}), flush=True)
                return
            worker = start_queue(library, source)
            if once:
                worker.wait()
                if worker.returncode:
                    raise RuntimeError('队列失败')
                return
    except Exception as exc:
        (library / 'work/STOP').write_text('supervisor_error: ' + str(exc), encoding='utf-8')
        print(json.dumps({'stage': 'stopped', 'error': str(exc)}, ensure_ascii=False), flush=True)
        raise
    finally:
        lock.close()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--library', default='D:/MotionDataLibrary')
    parser.add_argument('--source', default='D:/BONES-SEED')
    parser.add_argument('--once', action='store_true')
    args = parser.parse_args()
    run(args.library, args.source, args.once)
