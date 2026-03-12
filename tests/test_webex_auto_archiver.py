import unittest
from pathlib import Path

from webex_auto_archiver import (
    build_archive_commands,
    extract_token_candidates,
    filter_rooms,
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


if __name__ == '__main__':
    unittest.main()
