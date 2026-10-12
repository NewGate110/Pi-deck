import tempfile
import unittest
from pathlib import Path

from git_hygiene import has_local_changes


class GitHygieneTests(unittest.TestCase):
    def test_only_wholly_untracked_recognized_environments_are_allowed(self):
        with tempfile.TemporaryDirectory() as directory:
            bot = {'repo': directory}
            environment = Path(directory) / 'venv'
            environment.mkdir()
            check = lambda status: has_local_changes(bot, lambda *args: status)
            self.assertTrue(check('?? venv/'))
            (environment/'pyvenv.cfg').write_text('home = /usr/bin\n')
            self.assertFalse(check('?? venv/'))
            self.assertFalse(check(''))
            for status in (' M bot.py', 'A  venv/file.py', ' M venv/file.py',
                           '?? venv/file.py', '?? other/', '?? venv/\n?? notes.txt',
                           '?? venv/\n M bot.py'):
                with self.subTest(status=status):
                    self.assertTrue(check(status))
