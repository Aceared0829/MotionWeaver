"""运行有超时和日志验收的 UE 小批量检查；不依据单独 JSON 成功标记放行。"""
import argparse
import json
import os
import subprocess
import uuid
from pathlib import Path

from motionweaver.repository import unreal_scripts


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--engine', required=True)
    parser.add_argument('--project', required=True)
    parser.add_argument('--work', required=True)
    parser.add_argument('--timeout', type=int, default=600)
    args = parser.parse_args()
    work = Path(args.work).resolve()
    request = json.loads((work / 'ue_roundtrip_input.json').read_text(encoding='utf-8'))
    if not 1 <= len(request['clips']) <= 8:
        raise ValueError('试验批次必须为 1..8 条')
    names = [clip['name'] for clip in request['clips']]
    if len(names) != len(set(names)):
        raise ValueError('重复动作名称')
    run = work / ('run_' + uuid.uuid4().hex)
    run.mkdir()
    (run / 'ue_roundtrip_input.json').write_text(json.dumps(request), encoding='utf-8')
    env = dict(os.environ, MOTION_PIPELINE_WORK_ROOT=str(run))
    script = unreal_scripts() / 'validate_animation_roundtrip.py'
    command = [str(Path(args.engine).resolve()), str(Path(args.project).resolve()), '-run=pythonscript', '-script=' + str(script.resolve()), '-unattended', '-nop4', '-NullRHI']
    log = run / 'engine.log'
    with log.open('w', encoding='utf-8') as stream:
        result = subprocess.run(command, env=env, stdout=stream, stderr=subprocess.STDOUT, timeout=args.timeout, creationflags=subprocess.CREATE_NO_WINDOW, check=False)
    messages = log.read_text(encoding='utf-8', errors='replace')
    reports = json.loads((run / 'ue_roundtrip_report.json').read_text()) if (run / 'ue_roundtrip_report.json').exists() else []
    passed = (result.returncode == 0 and 'Success - 0 error(s), 0 warning(s)' in messages
              and not any(marker in messages for marker in ('Assertion failed:', 'Ensure condition failed:', 'LogPython: Error:'))
              and [item.get('name') for item in reports] == names and all(item.get('engine_roundtrip_pass') is True for item in reports))
    receipt = {'passed': passed, 'engine_exit_code': result.returncode, 'clips': len(reports), 'log': str(log), 'training_ready': False, 'source_deleted': False}
    (run / 'receipt.json').write_text(json.dumps(receipt, indent=2), encoding='utf-8')
    print(json.dumps(receipt))
    if not passed:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
