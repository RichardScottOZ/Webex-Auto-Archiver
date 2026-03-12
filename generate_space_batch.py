#!/usr/bin/env python3
from __future__ import annotations

import argparse

from webex_auto_archiver import fetch_rooms, filter_rooms, resolve_access_token, write_batch_scripts


def non_negative_int(value: str) -> int:
    parsed = int(value)
    if parsed < 0:
        raise argparse.ArgumentTypeError('must be zero or greater')
    return parsed


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description='Generate shell and Windows batch scripts to archive every Webex space.'
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
        help='Archive script to call for each room.',
    )
    parser.add_argument('--config-file', help='Optional .ini file to pass to the archive script.')
    parser.add_argument('--output-dir', default='.', help='Directory for the generated script files.')
    parser.add_argument('--shell-name', default='webex-space-archive-ALL.sh')
    parser.add_argument('--batch-name', default='webex-space-archive-ALL.bat')
    parser.add_argument('--match', help='Only include rooms whose title contains this text.')
    parser.add_argument('--skip-direct', action='store_true', help='Skip direct-message rooms.')
    parser.add_argument('--skip-group', action='store_true', help='Skip group rooms and only include direct messages.')
    parser.add_argument('--limit', type=non_negative_int, help='Limit how many matching rooms are included.')
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
    shell_path, batch_path = write_batch_scripts(
        filtered_rooms,
        output_dir=args.output_dir,
        archive_script=args.archive_script,
        config_file=args.config_file,
        shell_name=args.shell_name,
        batch_name=args.batch_name,
    )
    print(f'Selected {len(filtered_rooms)} of {len(rooms)} rooms.')
    print(f'Generated {len(filtered_rooms)} archive commands.')
    print(f'Shell script : {shell_path}')
    print(f'Batch script : {batch_path}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
