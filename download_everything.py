#!/usr/bin/env python3
from __future__ import annotations

import argparse

from webex_auto_archiver import archive_everything, fetch_rooms, filter_rooms, resolve_access_token


def non_negative_int(value: str) -> int:
    parsed = int(value)
    if parsed < 0:
        raise argparse.ArgumentTypeError('must be zero or greater')
    return parsed


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description='Archive every Webex space immediately using a single discovered token.'
    )
    parser.add_argument('--token', help='Existing Webex personal access token.')
    parser.add_argument(
        '--no-browser',
        action='store_true',
        help='Do not open a browser to discover the token automatically.',
    )
    parser.add_argument(
        '--browser-timeout',
        type=int,
        default=300,
        help='How long to wait for the Webex token when browser discovery is used.',
    )
    parser.add_argument(
        '--headless',
        action='store_true',
        help='Launch the browser in headless mode during token discovery.',
    )
    parser.add_argument(
        '--archive-script',
        default='webex-space-archive.py',
        help='Archive script to execute for each room.',
    )
    parser.add_argument('--config-file', help='Optional .ini file to pass to the archive script.')
    parser.add_argument('--match', help='Only include rooms whose title contains this text.')
    parser.add_argument('--skip-direct', action='store_true', help='Skip direct-message rooms.')
    parser.add_argument('--skip-group', action='store_true', help='Skip group rooms and only include direct messages.')
    parser.add_argument('--limit', type=non_negative_int, help='Limit how many matching rooms are processed.')
    parser.add_argument(
        '--dry-run',
        action='store_true',
        help='Print the archive commands that would run without executing them.',
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    token = resolve_access_token(
        args.token,
        allow_browser=not args.no_browser,
        timeout_seconds=args.browser_timeout,
        headless=args.headless,
    )
    rooms = fetch_rooms(token)
    filtered_rooms = filter_rooms(
        rooms,
        match=args.match,
        skip_direct=args.skip_direct,
        skip_group=args.skip_group,
        limit=args.limit,
    )
    commands = archive_everything(
        filtered_rooms,
        token=token,
        archive_script=args.archive_script,
        config_file=args.config_file,
        dry_run=args.dry_run,
    )
    if args.dry_run:
        print(f'Selected {len(filtered_rooms)} of {len(rooms)} rooms.')
        print('Dry run only. The following commands would be executed:')
        for command in commands:
            print(' '.join(command))
    else:
        print(f'Selected {len(filtered_rooms)} of {len(rooms)} rooms.')
        print(f'Archived {len(commands)} spaces.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
