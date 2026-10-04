"""Validated onboarding for existing services and new GitHub checkouts."""
import os
from pathlib import Path, PurePosixPath
import re
import tempfile


def validate(data):
    if not isinstance(data, dict):
        raise ValueError("Expected bot details.")
    mode = data.get("mode")
    if mode not in ("manual", "github"):
        raise ValueError("Choose GitHub or manual setup.")
    bot = {}
    for key in ("id", "name", "description", "user", "repo", "env", "service"):
        value = data.get(key, "")
        if not isinstance(value, str) or len(value) > 512 or any(ord(c) < 32 for c in value):
            raise ValueError("Invalid " + key + ".")
        bot[key] = value.strip()
    if not re.fullmatch(r"[a-z][a-z0-9-]{0,47}", bot["id"]):
        raise ValueError("Bot ID must start with a lowercase letter and use letters, numbers or hyphens (48 characters maximum).")
    if not bot["name"] or len(bot["name"]) > 80:
        raise ValueError("Enter a name of up to 80 characters.")
    if not re.fullmatch(r"[a-z_][a-z0-9_-]*", bot["user"]) or bot["user"] == "root":
        raise ValueError("Choose an existing non-root Linux user.")
    if not re.fullmatch(r"[a-zA-Z0-9_][a-zA-Z0-9_.-]*\.service", bot["service"]):
        raise ValueError("Enter a simple service name ending in .service.")
    for key in ("repo", "env"):
        if not re.fullmatch(r"/[a-zA-Z0-9_./-]+", bot[key]) or ".." in PurePosixPath(bot[key]).parts:
            raise ValueError("Use absolute Linux paths without spaces or parent traversal.")
    if mode == "github":
        url = data.get("url", "")
        if not isinstance(url, str) or not re.fullmatch(r"(?:https://github\.com/|git@github\.com:)[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", url):
            raise ValueError("Use a GitHub HTTPS or git@github.com:owner/repo URL, without embedded credentials.")
        command = data.get("command", "")
        if not isinstance(command, str) or not command.startswith("/") or len(command) > 2048 or any(ord(c) < 32 for c in command) or "%" in command:
            raise ValueError("Launch command must start with an absolute executable path and contain no newlines or % specifiers.")
        content = data.get("environment", "")
        if not isinstance(content, str) or "\x00" in content or len(content.encode()) > 65536:
            raise ValueError("Environment must be text under 64 KB.")
        if bot["env"] != bot["repo"].rstrip("/") + "/.env":
            raise ValueError("For GitHub setup, environment path must be the new checkout's .env file.")
    return bot


def unit_text(bot, command):
    return (f"[Unit]\nDescription=Pi Deck bot {bot['id']}\nAfter=network-online.target\nWants=network-online.target\n\n"
            f"[Service]\nType=simple\nUser={bot['user']}\nWorkingDirectory={bot['repo']}\n"
            f"EnvironmentFile={bot['env']}\nExecStart={command}\nRestart=on-failure\nRestartSec=5\nUMask=0077\n\n"
            "[Install]\nWantedBy=multi-user.target\n")


def provision(bot, data, run, persist):
    """Only persist a bot after setup succeeds. Never overwrite existing artifacts."""
    import pwd
    try:
        owner = pwd.getpwnam(bot['user'])
    except KeyError:
        raise ValueError("Linux user does not exist.")
    if owner.pw_uid == 0:
        raise ValueError("Bot owner must not have root privileges.")
    repo = Path(bot['repo'])
    env = Path(bot['env'])
    if data['mode'] == 'manual':
        if not repo.is_dir() or not env.is_file() or env.is_symlink():
            raise ValueError("Repository directory and regular environment file must already exist.")
        run(['git', '-C', str(repo), 'rev-parse', '--show-toplevel'], user=bot['user'])
        if run(['systemctl', 'show', bot['service'], '--property=LoadState', '--value']) != 'loaded':
            raise ValueError("The system service is not loaded. Check its name on the Pi.")
        persist(bot)
        return
    # Restrict newly created checkouts to the owner's home and avoid symlink aliases.
    home = Path(owner.pw_dir).resolve()
    if repo.is_symlink() or repo.exists() or home not in repo.resolve().parents or str(repo.resolve()) != str(repo):
        raise ValueError("Choose a new checkout directory inside the bot owner's home, without symlinked parents.")
    if not repo.parent.is_dir():
        raise ValueError("Checkout parent directory must already exist on the Pi.")
    unit = Path('/etc/systemd/system') / bot['service']
    if unit.exists() or unit.is_symlink() or run(['systemctl', 'show', bot['service'], '--property=LoadState', '--value']) != 'not-found':
        raise ValueError("That service already exists. Use manual setup or choose another name.")
    made_repo = False
    made_unit = False
    try:
        # Directory creation and all repository code execute as the non-root owner.
        run(['mkdir', '--', str(repo)], user=bot['user'])
        made_repo = True
        run(['git', 'clone', '--', data['url'], str(repo)], timeout=180, user=bot['user'])
        # Environment files must not be versioned, or pulls could overwrite secrets.
        if env.exists() or env.is_symlink() or run(['git', '-C', str(repo), 'ls-files', '--', '.env'], user=bot['user']):
            raise ValueError("Checkout contains .env. Configure it on the Pi and use manual setup.")
        if data.get('python_setup'):
            run(['/usr/bin/python3', '-m', 'venv', str(repo / '.venv')], timeout=180, user=bot['user'])
            requirements = repo / 'requirements.txt'
            if requirements.is_file():
                run([str(repo / '.venv/bin/python'), '-m', 'pip', 'install', '-r', str(requirements)], timeout=600, user=bot['user'])
        run(['/usr/bin/python3', '-c',
             "from pathlib import Path; import sys; p=Path(sys.argv[1])/'.git/info/exclude'; p.open('a').write('\\n.env\\n.venv/\\n')",
             str(repo)], user=bot['user'])
        fd = os.open(env, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, 'O_NOFOLLOW', 0), 0o600)
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            os.fchown(stream.fileno(), owner.pw_uid, owner.pw_gid)
            stream.write(data.get('environment', ''))
        text = unit_text(bot, data['command'])
        with tempfile.TemporaryDirectory() as tmp:
            candidate = Path(tmp) / bot['service']
            candidate.write_text(text, encoding='utf-8')
            run(['systemd-analyze', 'verify', str(candidate)])
        with unit.open('x', encoding='utf-8') as stream:
            made_unit = True
            stream.write(text)
        run(['systemctl', 'daemon-reload'])
        persist(bot)
    except Exception as exc:
        if made_unit:
            unit.unlink()
            run(['systemctl', 'daemon-reload'])
        if made_repo:
            raise ValueError("Setup did not complete. The new checkout was kept at " + str(repo) + ". Inspect it on the Pi, then finish using manual setup or retry with a new directory. " + (str(exc) if isinstance(exc, ValueError) else "Check dependencies, permissions and GitHub access.")) from exc
        raise
