"""Validated onboarding for existing services and new GitHub checkouts."""
import os
from pathlib import Path, PurePosixPath
import re
import tempfile
from management import progress


# Shared 24px line-icon paths; keys remain stable in saved registrations.
BOT_ICONS = {
    'bot': ('M12 3v3 M10 3h4 M7 6h10a3 3 0 0 1 3 3v9H4V9a3 3 0 0 1 3-3Z M8 11h.01 M16 11h.01 M8 15h8 M1 10v5 M23 10v5', 'Bot'),
    'chat': ('M21 11a8 8 0 0 1-8 8H7l-5 3 1.5-6A8 8 0 0 1 3 11a8 8 0 0 1 8-8h2a8 8 0 0 1 8 8Z M7 10h10 M7 14h6', 'Chat'),
    'notification': ('M18 8a6 6 0 0 0-12 0c0 7-3 7-3 9h18c0-2-3-2-3-9 M10 21h4 M12 1v1', 'Notifications'),
    'download': ('M12 3v12 M7 10l5 5 5-5 M4 16v5h16v-5', 'Downloads'),
    'weather': ('M16 12a4 4 0 1 1-8 0 4 4 0 0 1 8 0 M12 2v2 M12 20v2 M2 12h2 M20 12h2 M5 5l1.5 1.5 M17.5 17.5 19 19 M5 19l1.5-1.5 M17.5 6.5 19 5', 'Weather'),
    'calendar': ('M5 4h14a2 2 0 0 1 2 2v14H3V6a2 2 0 0 1 2-2Z M7 2v4 M17 2v4 M3 9h18 M7 13h2 M15 13h2 M7 17h2 M15 17h2', 'Calendar'),
    'music': ('M9 18V5l11-2v13 M9 9l11-2 M9 18a3 2 0 1 1-6 0 3 2 0 0 1 6 0 M20 16a3 2 0 1 1-6 0 3 2 0 0 1 6 0', 'Music'),
    'news': ('M4 3h13v17H4a2 2 0 0 1-2-2V7h2 M17 7h5v11a2 2 0 0 1-2 2h-3 M7 7h7 M7 11h7 M7 15h7', 'News'),
    'tools': ('M14 6a5 5 0 0 0-6 6L2 18a2.8 2.8 0 0 0 4 4l6-6a5 5 0 0 0 6-6l-3 3-4-4 3-3Z', 'Tools'),
    'shield': ('M12 2 3 6v6c0 5 9 10 9 10s9-5 9-10V6l-9-4Z M8 12l3 3 5-6', 'Security'),
}


def service_defaults(service, run):
    """Read effective systemd properties, including drop-ins, without reading secrets."""
    if not re.fullmatch(r'[a-zA-Z0-9_][a-zA-Z0-9_.-]*\.service', service):
        raise ValueError('Enter a simple service name ending in .service.')
    raw = run(['systemctl', 'show', service,
               '--property=LoadState,Description,User,WorkingDirectory,EnvironmentFiles'])
    props = dict(line.split('=', 1) for line in raw.splitlines() if '=' in line)
    if props.get('LoadState') != 'loaded':
        raise ValueError('Service is not loaded. Check its name or enter the details manually.')
    warnings = []
    def supported_path(value):
        return bool(re.fullmatch(r'/[a-zA-Z0-9_./-]+', value)) and '..' not in PurePosixPath(value).parts
    repo = props.get('WorkingDirectory', '')
    if not supported_path(repo):
        repo = ''
        warnings.append('No supported WorkingDirectory was found. Enter the Git checkout directory.')
    user = props.get('User', '')
    if user == 'root' or not re.fullmatch(r'[a-z_][a-z0-9_-]*', user):
        user = ''
        warnings.append('No non-root User was found. Enter the existing bot owner.')
    raw_env = props.get('EnvironmentFiles', '')
    pattern = r'(/[^\s]+) \(ignore_errors=(yes|no)\)'
    env_files = list(dict.fromkeys(path for path, _ in re.findall(pattern, raw_env) if supported_path(path)))
    if re.sub(pattern, '', raw_env).strip() or any(not supported_path(path) for path, _ in re.findall(pattern, raw_env)):
        warnings.append('Some environment paths could not be imported. Enter those paths manually.')
    if not env_files:
        warnings.append('No supported EnvironmentFile was found. Leave it blank if this bot does not use one.')
    elif len(env_files) > 1:
        warnings.append('This service uses multiple environment files. Choose which one Pi Deck should edit.')
    name = props.get('Description', '').strip()[:80] or service[:-8]
    return dict(service=service, name=name, user=user, repo=repo,
                env=env_files[0] if len(env_files) == 1 else '', env_files=env_files, warnings=warnings)


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
    icon = data.get('icon', 'bot')
    if not isinstance(icon, str) or icon not in BOT_ICONS:
        raise ValueError('Choose an icon from the list.')
    bot['icon'] = icon
    for key in ("repo", "env"):
        if key == 'env' and not bot[key]:
            continue
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
        if bot["env"] and bot["env"] != bot["repo"].rstrip("/") + "/.env":
            raise ValueError("For GitHub setup, environment path must be the new checkout's .env file.")
    if mode == 'github' and not bot['env'] and data.get('environment', '').strip():
        raise ValueError('Enable the environment file to save environment values.')
    return bot


def unit_text(bot, command):
    environment = f"EnvironmentFile={bot['env']}\n" if bot.get('env') else ""
    return (f"[Unit]\nDescription=Pi Deck bot {bot['id']}\nAfter=network-online.target\nWants=network-online.target\n\n"
            f"[Service]\nType=simple\nUser={bot['user']}\nWorkingDirectory={bot['repo']}\n"
            f"{environment}ExecStart={command}\nRestart=on-failure\nRestartSec=5\nUMask=0077\n\n"
            "[Install]\nWantedBy=multi-user.target\n")


def provision(bot, data, run, persist):
    """Only persist a bot after setup succeeds. Never overwrite existing artifacts."""
    import pwd
    progress('Checking Linux owner, checkout paths and service registration')
    try:
        owner = pwd.getpwnam(bot['user'])
    except KeyError:
        raise ValueError("Linux user does not exist.")
    if owner.pw_uid == 0:
        raise ValueError("Bot owner must not have root privileges.")
    repo = Path(bot['repo'])
    env = Path(bot['env']) if bot.get('env') else None
    if data['mode'] == 'manual':
        if not repo.is_dir() or (env is not None and (not env.is_file() or env.is_symlink())):
            raise ValueError("Repository directory and any specified regular environment file must already exist.")
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
        if env is not None and (env.exists() or env.is_symlink() or run(['git', '-C', str(repo), 'ls-files', '--', '.env'], user=bot['user'])):
            raise ValueError("Checkout contains .env. Configure it on the Pi and use manual setup.")
        if data.get('python_setup'):
            run(['/usr/bin/python3', '-m', 'venv', str(repo / '.venv')], timeout=180, user=bot['user'])
            requirements = repo / 'requirements.txt'
            if requirements.is_file():
                run([str(repo / '.venv/bin/python'), '-m', 'pip', 'install', '-r', str(requirements)], timeout=600, user=bot['user'])
        run(['/usr/bin/python3', '-c',
             "from pathlib import Path; import sys; p=Path(sys.argv[1])/'.git/info/exclude'; p.open('a').write('\\n.env\\n.venv/\\n')",
             str(repo)], user=bot['user'])
        if env is not None:
            progress('Writing private environment file with mode 0600')
            fd = os.open(env, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, 'O_NOFOLLOW', 0), 0o600)
            with os.fdopen(fd, 'w', encoding='utf-8') as stream:
                os.fchown(stream.fileno(), owner.pw_uid, owner.pw_gid)
                stream.write(data.get('environment', ''))
        text = unit_text(bot, data['command'])
        with tempfile.TemporaryDirectory() as tmp:
            candidate = Path(tmp) / bot['service']
            candidate.write_text(text, encoding='utf-8')
            run(['systemd-analyze', 'verify', str(candidate)])
        progress('Creating systemd service file: '+bot['service'])
        with unit.open('x', encoding='utf-8') as stream:
            made_unit = True
            stream.write(text)
        run(['systemctl', 'daemon-reload'])
        persist(bot)
    except Exception as exc:
        if made_unit:
            progress('Cleaning up service file after unsuccessful setup')
            unit.unlink()
            run(['systemctl', 'daemon-reload'])
        if made_repo:
            raise ValueError("Setup did not complete. The new checkout was kept at " + str(repo) + ". Inspect it on the Pi, then finish using manual setup or retry with a new directory. " + (str(exc) if isinstance(exc, ValueError) else "Check dependencies, permissions and GitHub access.")) from exc
        raise
