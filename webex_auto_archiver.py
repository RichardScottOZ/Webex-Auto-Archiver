from __future__ import annotations

import json
import os
import re
import shlex
import subprocess
import sys
import time
from pathlib import Path
from typing import Iterable, Iterator, Mapping, Sequence

import requests

DEFAULT_ARCHIVE_SCRIPT = 'webex-space-archive.py'
DEFAULT_SHELL_OUTPUT = 'webex-space-archive-ALL.sh'
DEFAULT_BATCH_OUTPUT = 'webex-space-archive-ALL.bat'
WEBEX_TOKEN_PAGE = 'https://developer.webex.com/docs/getting-your-personal-access-token'
TOKEN_PATTERN = re.compile(r'(?<![A-Za-z0-9_-])([A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{20,})(?![A-Za-z0-9_-])')
MASKED_TOKEN_PATTERN = re.compile(r'^[•*•\s]+$')
REQUEST_TIMEOUT = 30


class TokenDiscoveryError(RuntimeError):
    pass


def _flatten_values(value: object) -> Iterator[str]:
    if value is None:
        return
    if isinstance(value, str):
        yield value
        return
    if isinstance(value, Mapping):
        for item in value.items():
            yield from _flatten_values(item[0])
            yield from _flatten_values(item[1])
        return
    if isinstance(value, Sequence) and not isinstance(value, (bytes, bytearray)):
        for item in value:
            yield from _flatten_values(item)
        return
    yield str(value)


def extract_token_candidates(values: Iterable[object]) -> list[str]:
    candidates: list[str] = []
    seen: set[str] = set()
    for raw_value in values:
        for value in _flatten_values(raw_value):
            stripped = value.strip()
            if not stripped or MASKED_TOKEN_PATTERN.fullmatch(stripped):
                continue
            for match in TOKEN_PATTERN.finditer(stripped):
                token = match.group(1)
                if token not in seen:
                    candidates.append(token)
                    seen.add(token)
    return candidates


def validate_token(token: str, session: requests.Session | None = None) -> bool:
    if not token:
        return False
    client = session or requests.Session()
    response = client.get(
        'https://webexapis.com/v1/people/me',
        headers={'Authorization': f'Bearer {token}'},
        timeout=REQUEST_TIMEOUT,
    )
    return response.ok


def _scan_browser_candidates(page) -> list[str]:
    values = page.evaluate(
        """() => {
            const values = [];
            const add = (value) => {
                if (typeof value === 'string' && value.trim()) {
                    values.push(value.trim());
                }
            };
            const pushNodeValues = (node) => {
                if (!node) {
                    return;
                }
                add(node.textContent || '');
                add(node.value || '');
                add(node.getAttribute && node.getAttribute('value'));
                add(node.getAttribute && node.getAttribute('aria-label'));
                add(node.getAttribute && node.getAttribute('title'));
                add(node.getAttribute && node.getAttribute('placeholder'));
                if (node.dataset) {
                    add(JSON.stringify(node.dataset));
                }
            };
            add(document.cookie || '');
            for (const storage of [window.localStorage, window.sessionStorage]) {
                for (let index = 0; index < storage.length; index += 1) {
                    const key = storage.key(index);
                    add(key || '');
                    add(storage.getItem(key) || '');
                }
            }
            for (const node of document.querySelectorAll('input, textarea, button, [role="button"], label, code, pre, p, div, span')) {
                pushNodeValues(node);
            }
            return values;
        }"""
    )
    return extract_token_candidates(values)


def _discover_token_from_browser(timeout_seconds: int, headless: bool, session: requests.Session | None = None) -> str:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:  # pragma: no cover - exercised manually
        raise TokenDiscoveryError(
            'Browser-based token discovery requires Playwright. '
            'Install dependencies with "pip install -r requirements.txt" '
            'and then run "python -m playwright install chromium".'
        ) from exc

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=headless)
        context = browser.new_context()
        page = context.new_page()
        page.goto(WEBEX_TOKEN_PAGE, wait_until='domcontentloaded')
        page.bring_to_front()
        print('Opened a browser to Webex Developer. Complete any login or MFA steps there if prompted.')
        print('Waiting for a valid personal access token to appear...')
        deadline = time.time() + timeout_seconds
        last_hint_at = 0.0
        while time.time() < deadline:
            for candidate in _scan_browser_candidates(page):
                if validate_token(candidate, session=session):
                    browser.close()
                    return candidate
            if time.time() - last_hint_at >= 20:
                print('Still waiting for the token. If it is hidden, click the reveal/copy control in the browser window.')
                last_hint_at = time.time()
            page.wait_for_timeout(1000)
        browser.close()

    raise TokenDiscoveryError(
        f'Unable to locate a valid Webex personal access token within {timeout_seconds} seconds.'
    )


def resolve_access_token(
    explicit_token: str | None = None,
    *,
    allow_browser: bool = True,
    timeout_seconds: int = 300,
    headless: bool = False,
    session: requests.Session | None = None,
) -> str:
    token_sources = [explicit_token, os.environ.get('WEBEX_ARCHIVE_TOKEN')]
    for token in token_sources:
        if token and validate_token(token, session=session):
            return token
        if token:
            print('The supplied Webex token was rejected by the API. Trying browser discovery instead...')
    if allow_browser:
        return _discover_token_from_browser(timeout_seconds, headless, session=session)
    raise TokenDiscoveryError(
        'A valid Webex token was not supplied. Set WEBEX_ARCHIVE_TOKEN, pass --token, or allow browser discovery.'
    )


def sort_rooms(rooms: Sequence[Mapping[str, str]]) -> list[Mapping[str, str]]:
    return sorted(rooms, key=lambda room: room.get('title', '').lower())


def fetch_rooms(token: str, session: requests.Session | None = None) -> list[dict[str, str]]:
    client = session or requests.Session()
    response = client.get(
        'https://webexapis.com/v1/rooms?max=1000',
        headers={
            'Authorization': f'Bearer {token}',
            'Content-Type': 'application/json',
        },
        timeout=REQUEST_TIMEOUT,
    )
    response.raise_for_status()
    return [dict(room) for room in sort_rooms(response.json().get('items', []))]


def sanitize_comment(text: str) -> str:
    return ' '.join((text or '').replace('\r', ' ').replace('\n', ' ').split())


def _split_python_command(python_command: str | Sequence[str]) -> list[str]:
    if isinstance(python_command, str):
        return shlex.split(python_command)
    return list(python_command)


def _build_command_parts(
    archive_script: str,
    room_id: str,
    config_file: str | None,
    python_command: str | Sequence[str],
) -> list[str]:
    python_parts = _split_python_command(python_command)
    command = [*python_parts, archive_script]
    if config_file:
        command.extend([config_file, room_id])
    else:
        command.append(room_id)
    return command


def render_shell_script(
    rooms: Sequence[Mapping[str, str]],
    *,
    archive_script: str = DEFAULT_ARCHIVE_SCRIPT,
    config_file: str | None = None,
    python_command: str = 'python3',
) -> str:
    lines = ['#!/usr/bin/env bash', 'set -euo pipefail', '']
    direct_count = 0
    group_count = 0
    for index, room in enumerate(sort_rooms(rooms)):
        lines.append(f'# {index}. {sanitize_comment(room.get("title", ""))}')
        command = _build_command_parts(archive_script, room['id'], config_file, python_command)
        lines.append(' '.join(shlex.quote(part) for part in command))
        if room.get('type') == 'direct':
            direct_count += 1
        else:
            group_count += 1
    lines.extend(
        [
            '',
            f'#   TOTAL  space: {len(rooms)}',
            f'#   Direct space: {direct_count}',
            f'#   Group  space: {group_count}',
        ]
    )
    if len(rooms) > 990:
        lines.append('# ---> CAREFUL, you may have more than 1000 spaces. This script only includes the first 1000 spaces.')
    lines.append('')
    return '\n'.join(lines)


def _quote_batch_value(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


def render_batch_script(
    rooms: Sequence[Mapping[str, str]],
    *,
    archive_script: str = DEFAULT_ARCHIVE_SCRIPT,
    config_file: str | None = None,
    python_command: str = 'py -3',
) -> str:
    lines = ['@echo off', 'setlocal', '']
    direct_count = 0
    group_count = 0
    python_parts = _split_python_command(python_command)
    for index, room in enumerate(sort_rooms(rooms)):
        lines.append(f'REM {index}. {sanitize_comment(room.get("title", ""))}')
        command = _build_command_parts(archive_script, room['id'], config_file, python_command)
        rendered_parts = []
        for command_index, part in enumerate(command):
            if command_index >= len(python_parts) or not part or any(character.isspace() for character in part) or '"' in part:
                rendered_parts.append(_quote_batch_value(part))
            else:
                rendered_parts.append(part)
        lines.append(' '.join(rendered_parts))
        if room.get('type') == 'direct':
            direct_count += 1
        else:
            group_count += 1
    lines.extend(
        [
            '',
            f'REM   TOTAL  space: {len(rooms)}',
            f'REM   Direct space: {direct_count}',
            f'REM   Group  space: {group_count}',
            '',
        ]
    )
    return '\n'.join(lines)


def write_batch_scripts(
    rooms: Sequence[Mapping[str, str]],
    *,
    output_dir: str | Path = '.',
    archive_script: str = DEFAULT_ARCHIVE_SCRIPT,
    config_file: str | None = None,
    shell_name: str = DEFAULT_SHELL_OUTPUT,
    batch_name: str = DEFAULT_BATCH_OUTPUT,
) -> tuple[Path, Path]:
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)
    shell_path = output_path / shell_name
    batch_path = output_path / batch_name
    shell_path.write_text(
        render_shell_script(rooms, archive_script=archive_script, config_file=config_file),
        encoding='utf-8',
    )
    shell_path.chmod(0o755)
    batch_path.write_text(
        render_batch_script(rooms, archive_script=archive_script, config_file=config_file),
        encoding='utf-8',
    )
    return shell_path, batch_path


def build_archive_commands(
    rooms: Sequence[Mapping[str, str]],
    *,
    archive_script: str = DEFAULT_ARCHIVE_SCRIPT,
    config_file: str | None = None,
    python_executable: str | None = None,
) -> list[list[str]]:
    executable = python_executable or sys.executable
    return [
        _build_command_parts(archive_script, room['id'], config_file, executable)
        for room in sort_rooms(rooms)
    ]


def archive_everything(
    rooms: Sequence[Mapping[str, str]],
    *,
    token: str,
    archive_script: str = DEFAULT_ARCHIVE_SCRIPT,
    config_file: str | None = None,
    python_executable: str | None = None,
    dry_run: bool = False,
) -> list[list[str]]:
    commands = build_archive_commands(
        rooms,
        archive_script=archive_script,
        config_file=config_file,
        python_executable=python_executable,
    )
    if dry_run:
        return commands
    env = os.environ.copy()
    env['WEBEX_ARCHIVE_TOKEN'] = token
    for command in commands:
        subprocess.run(command, check=True, env=env)
    return commands
