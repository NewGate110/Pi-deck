"""Recognize local Python environments without hiding tracked code changes."""
from pathlib import Path


def has_local_changes(bot, git):
    status = git(bot, 'status', '--porcelain')
    for line in status.splitlines():
        # Default porcelain collapses wholly untracked directories. Never skip
        # tracked modifications, staged files, or arbitrary untracked paths.
        if line in ('?? venv/', '?? .venv/'):
            environment = Path(bot['repo']) / line[3:-1]
            marker = environment / 'pyvenv.cfg'
            if (environment.is_dir() and not environment.is_symlink()
                    and marker.is_file() and not marker.is_symlink()):
                continue
        if line.strip():
            return True
    return False
