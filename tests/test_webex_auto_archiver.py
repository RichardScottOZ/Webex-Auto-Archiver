import unittest

from webex_auto_archiver import (
    build_archive_commands,
    extract_token_candidates,
    render_batch_script,
    render_shell_script,
    sanitize_comment,
    sort_rooms,
)


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
        ]

    def test_sort_rooms_is_case_insensitive(self):
        sorted_rooms = sort_rooms(self.rooms)
        self.assertEqual([room['id'] for room in sorted_rooms], ['room-1', 'room-2'])

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


if __name__ == '__main__':
    unittest.main()
