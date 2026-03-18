import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

from webex_auto_archiver import (
    build_archive_commands,
    extract_token_candidates,
    fetch_messages,
    filter_rooms,
    parse_date_arg,
    render_batch_script,
    render_shell_script,
    sanitize_comment,
    sort_rooms,
)
from download_everything import build_parser as build_download_parser
from generate_space_batch import build_parser as build_generate_parser


class TokenExtractionTests(unittest.TestCase):
    def test_extracts_tokens_from_nested_values(self):
        token = 'A' * 24 + '.' + 'B' * 24 + '.' + 'C' * 24
        values = [
            {'label': 'Bearer', 'value': token},
            ['masked', '••••••••••'],
            {'nested': [{'token': token}]},
        ]

        self.assertEqual(extract_token_candidates(values), [token])

    def test_ignores_masked_values(self):
        self.assertEqual(extract_token_candidates(['••••••••', '***']), [])


class ScriptRenderingTests(unittest.TestCase):
    def setUp(self):
        self.rooms = [
            {'title': 'Zulu', 'id': 'room-2', 'type': 'group'},
            {'title': 'alpha\nteam', 'id': 'room-1', 'type': 'direct'},
            {'title': 'Bravo Group', 'id': 'room-3', 'type': 'group'},
            {'title': 'Delta Direct', 'id': 'room-4', 'type': 'direct'},
        ]

    def test_sort_rooms_is_case_insensitive(self):
        sorted_rooms = sort_rooms(self.rooms)
        self.assertEqual([room['id'] for room in sorted_rooms], ['room-1', 'room-3', 'room-4', 'room-2'])

    def test_shell_script_uses_sanitized_comments_and_quotes(self):
        shell_script = render_shell_script(
            self.rooms,
            archive_script='archive tool.py',
            config_file='custom config.ini',
        )
        self.assertIn('# 0. alpha team', shell_script)
        self.assertIn("python3 'archive tool.py' 'custom config.ini' room-1", shell_script)

    def test_batch_script_uses_windows_friendly_quoting(self):
        batch_script = render_batch_script(
            self.rooms,
            archive_script='archive tool.py',
            config_file='custom config.ini',
        )
        self.assertIn('REM 0. alpha team', batch_script)
        self.assertIn('"archive tool.py" "custom config.ini" "room-1"', batch_script)

    def test_build_archive_commands_use_current_python_when_requested(self):
        commands = build_archive_commands(
            self.rooms,
            archive_script='archive.py',
            config_file='config.ini',
            python_executable='python-custom',
        )
        self.assertEqual(commands[0], ['python-custom', 'archive.py', 'config.ini', 'room-1'])

    def test_sanitize_comment_collapses_whitespace(self):
        self.assertEqual(sanitize_comment(' A\n messy\r\n title '), 'A messy title')

    def test_filter_rooms_can_skip_group_rooms(self):
        filtered_rooms = filter_rooms(self.rooms, skip_group=True)
        self.assertEqual([room['id'] for room in filtered_rooms], ['room-1', 'room-4'])

    def test_filter_rooms_can_skip_direct_rooms(self):
        filtered_rooms = filter_rooms(self.rooms, skip_direct=True)
        self.assertEqual([room['id'] for room in filtered_rooms], ['room-3', 'room-2'])

    def test_filter_rooms_can_match_and_limit(self):
        filtered_rooms = filter_rooms(self.rooms, match='dir', limit=1)
        self.assertEqual([room['id'] for room in filtered_rooms], ['room-4'])


class CliOptionsTests(unittest.TestCase):
    def test_generate_parser_supports_room_filters(self):
        args = build_generate_parser().parse_args(['--skip-group', '--match', 'alpha', '--limit', '2'])
        self.assertTrue(args.skip_group)
        self.assertEqual(args.match, 'alpha')
        self.assertEqual(args.limit, 2)

    def test_download_parser_supports_room_filters(self):
        args = build_download_parser().parse_args(['--skip-direct', '--match', 'team', '--limit', '3'])
        self.assertTrue(args.skip_direct)
        self.assertEqual(args.match, 'team')
        self.assertEqual(args.limit, 3)

    def test_windows_launcher_routes_to_python_scripts(self):
        launcher_path = Path(__file__).resolve().parents[1] / 'webex-auto-archiver.cmd'
        launcher_text = launcher_path.read_text(encoding='utf-8')
        self.assertIn('generate_space_batch.py', launcher_text)
        self.assertIn('download_everything.py', launcher_text)

    def test_generate_parser_supports_date_range(self):
        args = build_generate_parser().parse_args(['--since', '7d', '--before', '2024-06-01'])
        self.assertEqual(args.since, '7d')
        self.assertEqual(args.before, '2024-06-01')

    def test_download_parser_supports_date_range(self):
        args = build_download_parser().parse_args(['--since', 'yesterday', '--before', '2024-06-01'])
        self.assertEqual(args.since, 'yesterday')
        self.assertEqual(args.before, '2024-06-01')


class ParseDateArgTests(unittest.TestCase):
    def test_yesterday(self):
        now = datetime.now(tz=timezone.utc)
        result = parse_date_arg('yesterday')
        self.assertEqual(result.tzinfo, timezone.utc)
        delta = now - result
        self.assertGreater(delta.total_seconds(), 0)
        self.assertLess(delta.total_seconds(), 2 * 24 * 3600)

    def test_today(self):
        now = datetime.now(tz=timezone.utc)
        result = parse_date_arg('today')
        self.assertEqual(result.tzinfo, timezone.utc)
        self.assertEqual(result.date(), now.date())

    def test_relative_days(self):
        now = datetime.now(tz=timezone.utc)
        result = parse_date_arg('7d')
        delta = now - result
        self.assertAlmostEqual(delta.total_seconds() / 3600, 7 * 24, delta=1)

    def test_relative_weeks(self):
        now = datetime.now(tz=timezone.utc)
        result = parse_date_arg('2w')
        delta = now - result
        self.assertAlmostEqual(delta.total_seconds() / 3600, 14 * 24, delta=1)

    def test_relative_months(self):
        now = datetime.now(tz=timezone.utc)
        result = parse_date_arg('1m')
        delta = now - result
        self.assertAlmostEqual(delta.total_seconds() / 3600, 30 * 24, delta=1)

    def test_iso_date(self):
        result = parse_date_arg('2024-01-15')
        self.assertEqual(result, datetime(2024, 1, 15, tzinfo=timezone.utc))

    def test_iso_datetime(self):
        result = parse_date_arg('2024-01-15T10:30:00')
        self.assertEqual(result, datetime(2024, 1, 15, 10, 30, 0, tzinfo=timezone.utc))

    def test_invalid_raises(self):
        with self.assertRaises(ValueError):
            parse_date_arg('not-a-date')

    def test_case_insensitive_keywords(self):
        self.assertEqual(parse_date_arg('Yesterday').date(), parse_date_arg('yesterday').date())


class FilterRoomsByDateTests(unittest.TestCase):
    def setUp(self):
        self.rooms = [
            {'id': 'room-1', 'title': 'Alpha', 'type': 'group', 'lastActivity': '2024-01-10T10:00:00.000Z'},
            {'id': 'room-2', 'title': 'Beta', 'type': 'group', 'lastActivity': '2024-01-20T10:00:00.000Z'},
            {'id': 'room-3', 'title': 'Gamma', 'type': 'group', 'lastActivity': '2024-02-05T10:00:00.000Z'},
            {'id': 'room-4', 'title': 'Delta', 'type': 'group'},  # no lastActivity
        ]

    def test_filter_since_excludes_older_rooms(self):
        since = datetime(2024, 1, 15, tzinfo=timezone.utc)
        result = filter_rooms(self.rooms, since=since)
        self.assertEqual([r['id'] for r in result], ['room-2', 'room-3'])

    def test_filter_before_excludes_newer_rooms(self):
        before = datetime(2024, 2, 1, tzinfo=timezone.utc)
        result = filter_rooms(self.rooms, before=before)
        self.assertEqual([r['id'] for r in result], ['room-1', 'room-2'])

    def test_filter_since_and_before(self):
        since = datetime(2024, 1, 15, tzinfo=timezone.utc)
        before = datetime(2024, 2, 1, tzinfo=timezone.utc)
        result = filter_rooms(self.rooms, since=since, before=before)
        self.assertEqual([r['id'] for r in result], ['room-2'])

    def test_rooms_without_last_activity_excluded_when_date_filter_active(self):
        since = datetime(2024, 1, 1, tzinfo=timezone.utc)
        result = filter_rooms(self.rooms, since=since)
        ids = [r['id'] for r in result]
        self.assertNotIn('room-4', ids)

    def test_rooms_without_last_activity_included_when_no_date_filter(self):
        result = filter_rooms(self.rooms)
        ids = [r['id'] for r in result]
        self.assertIn('room-4', ids)

    def test_naive_datetime_treated_as_utc(self):
        since_naive = datetime(2024, 1, 15)  # no tzinfo
        result = filter_rooms(self.rooms, since=since_naive)
        self.assertEqual([r['id'] for r in result], ['room-2', 'room-3'])


class FetchMessagesTests(unittest.TestCase):
    def _make_session(self, pages):
        """Build a mock session whose .get() returns successive response pages."""
        session = MagicMock()
        responses = []
        for page in pages:
            resp = MagicMock()
            resp.ok = True
            resp.json.return_value = {'items': page}
            responses.append(resp)
        session.get.side_effect = responses
        return session

    def test_returns_messages_in_chronological_order(self):
        # The API returns newest-first; fetch_messages should reverse to oldest-first.
        page = [
            {'id': 'msg-3', 'created': '2024-01-15T12:00:00.000Z', 'text': 'c'},
            {'id': 'msg-2', 'created': '2024-01-15T11:00:00.000Z', 'text': 'b'},
            {'id': 'msg-1', 'created': '2024-01-15T10:00:00.000Z', 'text': 'a'},
        ]
        session = self._make_session([page, []])
        result = fetch_messages('token', 'room-1', session=session)
        self.assertEqual([m['id'] for m in result], ['msg-1', 'msg-2', 'msg-3'])

    def test_since_filter_stops_at_older_messages(self):
        page = [
            {'id': 'msg-3', 'created': '2024-01-15T12:00:00.000Z', 'text': 'c'},
            {'id': 'msg-2', 'created': '2024-01-15T11:00:00.000Z', 'text': 'b'},
            {'id': 'msg-1', 'created': '2024-01-14T10:00:00.000Z', 'text': 'a'},  # before since
        ]
        session = self._make_session([page])
        since = datetime(2024, 1, 15, tzinfo=timezone.utc)
        result = fetch_messages('token', 'room-1', since=since, session=session)
        ids = [m['id'] for m in result]
        self.assertIn('msg-2', ids)
        self.assertIn('msg-3', ids)
        self.assertNotIn('msg-1', ids)

    def test_max_items_limits_results(self):
        page = [
            {'id': f'msg-{i}', 'created': f'2024-01-15T{12 - i:02d}:00:00.000Z', 'text': str(i)}
            for i in range(5)
        ]
        session = self._make_session([page, []])
        result = fetch_messages('token', 'room-1', max_items=2, session=session)
        self.assertEqual(len(result), 2)

    def test_empty_room_returns_empty_list(self):
        session = self._make_session([[]])
        result = fetch_messages('token', 'room-1', session=session)
        self.assertEqual(result, [])


if __name__ == '__main__':
    unittest.main()
