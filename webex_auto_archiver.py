from __future__ import annotations

import json
import os
import re
import shlex
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
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


def parse_date_arg(value: str) -> datetime:
    """Parse a flexible date/time string into a UTC-aware :class:`~datetime.datetime`.

    Accepted formats:

    * ``yesterday`` – start of yesterday (midnight UTC).
    * ``today`` – start of today (midnight UTC).
    * Relative values: ``7d``, ``2w``, ``1m`` (days, weeks, or months ago).
      Months are approximated as 30 calendar days.
    * ISO 8601 date: ``2024-01-15``.
    * ISO 8601 datetime: ``2024-01-15T10:30:00``.

    :raises ValueError: When the format cannot be recognised.
    """
    stripped = value.strip()
    lower = stripped.lower()
    now = datetime.now(tz=timezone.utc)

    if lower == 'yesterday':
        yesterday = (now - timedelta(days=1)).date()
        return datetime(yesterday.year, yesterday.month, yesterday.day, tzinfo=timezone.utc)

    if lower == 'today':
        today = now.date()
        return datetime(today.year, today.month, today.day, tzinfo=timezone.utc)

    rel_match = re.fullmatch(r'(\d+)\s*(d|day|days|w|week|weeks|m|month|months)', lower)
    if rel_match:
        count = int(rel_match.group(1))
        unit = rel_match.group(2)[0]  # 'd', 'w', or 'm'
        if unit == 'd':
            return now - timedelta(days=count)
        if unit == 'w':
            return now - timedelta(weeks=count)
        return now - timedelta(days=count * 30)

    for fmt in ('%Y-%m-%dT%H:%M:%S', '%Y-%m-%dT%H:%M', '%Y-%m-%d'):
        try:
            dt = datetime.strptime(stripped, fmt)
            return dt.replace(tzinfo=timezone.utc)
        except ValueError:
            continue

    raise ValueError(
        f'Unrecognised date value {stripped!r}. '
        'Use "yesterday", "today", a relative value such as "7d", "2w" or "1m" '
        '(months are approximated as 30 days), '
        'or an ISO 8601 date/datetime (YYYY-MM-DD or YYYY-MM-DDTHH:MM:SS).'
    )


def _parse_webex_datetime(value: str) -> datetime | None:
    """Parse a Webex API ISO 8601 datetime string into a UTC-aware datetime."""
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace('Z', '+00:00'))
    except ValueError:
        return None


def sort_rooms(rooms: Sequence[Mapping[str, str]]) -> list[Mapping[str, str]]:
    return sorted(rooms, key=lambda room: room.get('title', '').lower())


def filter_rooms(
    rooms: Sequence[Mapping[str, str]],
    *,
    match: str | None = None,
    skip_direct: bool = False,
    skip_group: bool = False,
    limit: int | None = None,
    since: datetime | None = None,
    before: datetime | None = None,
) -> list[Mapping[str, str]]:
    """Return a filtered and sorted subset of *rooms*.

    :param since: Only include rooms whose ``lastActivity`` is at or after this
        datetime.  Rooms with no ``lastActivity`` are excluded when this filter
        is active.
    :param before: Only include rooms whose ``lastActivity`` is strictly before
        this datetime.  Rooms with no ``lastActivity`` are excluded when this
        filter is active.
    """
    if limit is not None and limit < 0:
        raise ValueError('limit must be zero or greater')

    def _ensure_utc(dt: datetime) -> datetime:
        return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)

    since_utc = _ensure_utc(since) if since is not None else None
    before_utc = _ensure_utc(before) if before is not None else None
    date_filter_active = since_utc is not None or before_utc is not None

    filtered_rooms: list[Mapping[str, str]] = []
    match_text = match.casefold() if match else None
    for room in sort_rooms(rooms):
        room_type = room.get('type', '').casefold()
        if skip_direct and room_type == 'direct':
            continue
        if skip_group and room_type == 'group':
            continue
        if match_text and match_text not in room.get('title', '').casefold():
            continue
        if date_filter_active:
            last_activity = _parse_webex_datetime(room.get('lastActivity', ''))
            if last_activity is None:
                continue
            if since_utc is not None and last_activity < since_utc:
                continue
            if before_utc is not None and last_activity >= before_utc:
                continue
        filtered_rooms.append(room)

    if limit is not None:
        return filtered_rooms[:limit]
    return filtered_rooms


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


def fetch_messages(
    token: str,
    room_id: str,
    *,
    since: datetime | None = None,
    before: datetime | None = None,
    max_items: int | None = None,
    session: requests.Session | None = None,
) -> list[dict]:
    """Fetch messages from a Webex room, optionally within a date range.

    The Webex API returns messages in reverse-chronological order (newest
    first).  This function reverses the result so that messages are returned
    in chronological order (oldest first).

    :param token: Webex personal access token.
    :param room_id: The ID of the Webex room/space to retrieve messages from.
    :param since: Only return messages created at or after this datetime.
    :param before: Only return messages created strictly before this datetime.
    :param max_items: Maximum total number of messages to return.
    :param session: Optional :class:`requests.Session` to reuse for HTTP calls.
    :returns: List of message dicts in chronological order (oldest first).
    """
    def _ensure_utc(dt: datetime) -> datetime:
        return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)

    since_utc = _ensure_utc(since) if since is not None else None
    before_utc = _ensure_utc(before) if before is not None else None

    client = session or requests.Session()
    headers = {
        'Authorization': f'Bearer {token}',
        'Content-Type': 'application/json',
    }

    messages: list[dict] = []
    page_before: datetime | None = before_utc

    while True:
        params: dict = {'roomId': room_id, 'max': 200}
        if page_before is not None:
            params['before'] = page_before.astimezone(timezone.utc).isoformat().replace('+00:00', 'Z')

        response = client.get(
            'https://webexapis.com/v1/messages',
            headers=headers,
            params=params,
            timeout=REQUEST_TIMEOUT,
        )
        response.raise_for_status()
        batch = response.json().get('items', [])
        if not batch:
            break

        done = False
        for message in batch:
            created_dt = _parse_webex_datetime(message.get('created', ''))
            if created_dt is not None and since_utc is not None and created_dt < since_utc:
                done = True
                break
            messages.append(message)
            if max_items is not None and len(messages) >= max_items:
                done = True
                break

        if done:
            break

        oldest_dt = _parse_webex_datetime(batch[-1].get('created', ''))
        if oldest_dt is None:
            break
        page_before = oldest_dt

    messages.reverse()
    return messages


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
