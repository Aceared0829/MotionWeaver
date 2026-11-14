"""动作数据库 CLI；当前提供可恢复的清单扫描与状态查询。"""
import argparse
import json

from data.runtime.motion_catalog import MotionCatalog


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--library', default='D:/MotionDataLibrary')
    sub = parser.add_subparsers(dest='command', required=True)
    scan = sub.add_parser('scan-seed')
    scan.add_argument('--source', required=True)
    scan.add_argument('--limit', type=int)
    sub.add_parser('status')
    args = parser.parse_args()
    catalog = MotionCatalog(args.library)
    try:
        if args.command == 'scan-seed':
            if args.limit is not None and args.limit < 1:
                parser.error('--limit 必须大于 0')
            count = catalog.scan_seed(args.source, args.limit)
            print(json.dumps({'scanned': count, 'states': catalog.status()}, ensure_ascii=False))
        else:
            print(json.dumps(catalog.status(), ensure_ascii=False))
    finally:
        catalog.close()


if __name__ == '__main__':
    main()
