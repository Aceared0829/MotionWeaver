"""全量流式检查源路径、BVH 骨架头与声明帧数；不代替逐帧或重定向验收。"""
import argparse
import hashlib
import json
import math
from pathlib import Path

from data.runtime.motion_catalog import MotionCatalog


def header(path):
    lines = []
    size = 0
    with Path(path).open(encoding='utf-8-sig') as stream:
        for line in stream:
            size += len(line)
            if size > 128 * 1024:
                raise ValueError('骨架头超过限制')
            if line.strip() == 'MOTION':
                break
            lines.append(line)
        else:
            raise ValueError('缺少 MOTION')
        frames = stream.readline().split(':')
        timing = stream.readline().split(':')
        if frames[0].strip() != 'Frames' or timing[0].strip() != 'Frame Time':
            raise ValueError('帧数或时间头无效')
        count, dt = int(frames[1]), float(timing[1])
        if count < 2 or not math.isfinite(dt) or abs(1/dt-120)>0.01:
            raise ValueError('帧数或帧率超出 SOMA 协议')
    return hashlib.sha256(' '.join(''.join(lines).split()).encode()).hexdigest(), count


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--library', default='D:/MotionDataLibrary')
    parser.add_argument('--source', default='D:/BONES-SEED')
    args = parser.parse_args()
    catalog = MotionCatalog(args.library)
    source = Path(args.source).resolve()
    # 头签名只做分组，不能把首个文件当权威骨骼；所有新组须人工/解析器检查。
    groups = {}
    processed = 0
    try:
        while True:
            rows = catalog.db.execute("SELECT id,metadata_json FROM motions WHERE dataset='bones-seed' AND state='discovered' ORDER BY id LIMIT 256").fetchall()
            if not rows:
                break
            with catalog.db:
                for motion_id, metadata in rows:
                    data = json.loads(metadata)
                    try:
                        path = (source/data['move_soma_uniform_path']).resolve()
                        if not path.is_relative_to(source/'soma_uniform'):
                            raise ValueError('源路径越界')
                        signature, frames = header(path)
                        if frames != int(data['move_duration_frames']):
                            raise ValueError('声明帧数与清单不符')
                        groups[signature] = groups.get(signature,0)+1
                        detail = json.dumps({'header_sha256': signature,'frames': frames,'body_validated': False})
                        catalog.db.execute("UPDATE motions SET state='source_header_checked',error=NULL WHERE id=?",(motion_id,))
                        catalog.db.execute('INSERT INTO events(motion_id,state,detail) VALUES(?,?,?)',(motion_id,'source_header_checked',detail))
                    except (OSError, ValueError, IndexError) as exc:
                        catalog.db.execute("UPDATE motions SET state='source_check_failed',error=? WHERE id=?",(str(exc),motion_id))
                    processed += 1
            if processed % 4096 == 0:
                print(json.dumps({'processed': processed,'header_groups': len(groups)}),flush=True)
        report = {'processed_this_run': processed,'header_groups_this_run': groups,'states': catalog.status(),'body_validated': False,'source_deleted': False}
        (catalog.folder/'reports/source_headers.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
        print(json.dumps(report),flush=True)
    finally:
        catalog.close()


if __name__=='__main__':
    main()
