import argparse
import csv
import importlib.util
import json
import os
import shutil
import sys
import time
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import numpy as np

if sys.argv[1].startswith('baseline'):
    # 对照进程（包括 spawn 子进程）固定使用修改前的源码。
    for name in ('runtime.native_motion_io', 'runtime.motion_features', 'runtime.motion_processing', 'tools.soma_observation', 'tools.process_soma_dataset'):
        module_name = 'data.' + name
        path = Path('output/soma-speed-profile/baseline-code') / (name.replace('.', '/')+'.py')
        spec = importlib.util.spec_from_file_location(module_name, path)
        module = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = module
        spec.loader.exec_module(module)
    module.DATA = Path('data').resolve()
from data.tools.process_soma_dataset import process_clip


def measured(item):
    start = time.perf_counter()
    result = process_clip(item)
    return result, time.perf_counter() - start


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('version')
    parser.add_argument('--workers', type=int, required=True)
    args = parser.parse_args()
    os.environ['OPENBLAS_NUM_THREADS'] = '1'
    root = Path('E:/MotionWeaverData')
    selection = Path('output/soma-speed-profile/selection.json')
    if selection.exists():
        rows = json.loads(selection.read_text(encoding='utf-8'))
    else:
        with (root/'reports/soma_uniform/20261018-soma-full-v1/selection.csv').open(encoding='utf-8',newline='') as stream:
            all_rows = list(csv.DictReader(stream))
        rows = [all_rows[i] for i in np.linspace(0, len(all_rows)-1, 8, dtype=int)]
        selection.write_text(json.dumps(rows, ensure_ascii=False), encoding='utf-8')
    run_id = '20261018-speed-' + args.version
    report = root/'reports/soma_uniform'/run_id
    report.mkdir(exist_ok=False)
    shutil.copytree(root/'processed/soma_uniform/20261018-soma-full-v1/skeletons',root/'processed/soma_uniform'/run_id/'skeletons')
    start = time.perf_counter()
    items = [(row,str(root),run_id) for row in rows]
    if args.workers == 1:
        outputs = list(map(measured, items))
    else:
        with ProcessPoolExecutor(max_workers=args.workers) as pool:
            outputs = list(pool.map(measured, items))
    elapsed = time.perf_counter()-start
    result = {'version':args.version,'workers':args.workers,'seconds':elapsed,'frames':sum(int(r['move_duration_frames']) for r in rows),
              'clips':len(rows),'clip_seconds':[v[1] for v in outputs],'results':[v[0] for v in outputs]}
    Path('output/soma-speed-profile/'+args.version+'.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({k:v for k,v in result.items() if k!='results'},ensure_ascii=False),flush=True)
