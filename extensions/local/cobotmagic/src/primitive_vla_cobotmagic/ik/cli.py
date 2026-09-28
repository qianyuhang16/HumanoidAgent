"""Unified Cobot Magic IK entrypoint: pixel planning, reviewed execution, or web."""
import argparse
from pathlib import Path
import sys
from .pixel import DEFAULT_CONFIG, execute_bundle, move_to_pixel


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, default=DEFAULT_CONFIG)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument('--pixel', type=float, nargs=2, metavar=('U', 'V'), help='full-resolution front image pixel')
    mode.add_argument('--plan', type=Path, help='replay a frozen plan folder')
    mode.add_argument('--web', action='store_true', help='start the observation/planning web page')
    parser.add_argument('--arm', choices=['left','right'], help='required for --pixel')
    parser.add_argument('--execute', action='store_true', help='execute after pressing Space in this terminal')
    parser.add_argument('--host', default='127.0.0.1', help='web listen address')
    parser.add_argument('--port', type=int, default=18085, help='web listen port')
    args = parser.parse_args(argv)
    if args.pixel is not None and args.arm is None:
        parser.error('--pixel requires --arm left/right')
    if args.web and args.execute:
        parser.error('Web has no motion endpoint; use --pixel or --plan with --execute')
    try:
        if args.web:
            from .web import create_app
            import uvicorn
            uvicorn.run(create_app(args.config), host=args.host, port=args.port)
            return 0
        if args.plan:
            return execute_bundle(args.plan, execute=args.execute)
        result = move_to_pixel(args.arm, *args.pixel, config=args.config, execute=args.execute)
        if not result['execution_ready']:
            print('\n'.join(result['blockers']), file=sys.stderr)
            return 1
        print(f"{result['arm']} 臂目标 XYZ：{result['target_xyz_m']} m；轨迹 {result['duration_s']:g} 秒")
        print(f"记录：{result['bundle']}")
        if not args.execute:
            print('仅规划，未发送动作。执行此计划：\n' + result['command'])
        return result.get('execution_exit_code', 0)
    except KeyboardInterrupt:
        print('用户取消。', file=sys.stderr)
        return 130
    except (ValueError, OSError, RuntimeError, KeyError, TypeError) as error:
        print(f'拒绝/中止: {error}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
