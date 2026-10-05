"""Pi Deck: a small, single-administrator Telegram bot control panel."""
import argparse
import collections
import copy
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import signal
import subprocess
import tempfile
import threading
import time

from flask import Flask, jsonify, request, session, render_template
from werkzeug.exceptions import HTTPException, Conflict
from werkzeug.security import check_password_hash
from onboarding import validate, provision, unit_text, BOT_ICONS
from management import Store, Jobs, Health, progress, job_step
from features import register_features
from self_update import register_self_updates

DEMO_BOTS = [
    dict(id="notifier", name="Channel notifier", description="Channel posts & notifications", service="channel-notifier.service", repo="/home/pi/bots/channel-notifier", env="/home/pi/bots/channel-notifier/.env", user="pi", state="active", enabled=True),
    dict(id="assistant", name="Personal assistant", description="Commands, reminders & daily tasks", service="personal-assistant.service", repo="/home/pi/bots/personal-assistant", env="/home/pi/bots/personal-assistant/.env", user="pi", state="active", enabled=True),
    dict(id="downloads", name="Download helper", description="Download queue & file delivery", service="download-helper.service", repo="/home/pi/bots/download-helper", env="/home/pi/bots/download-helper/.env", user="pi", state="inactive", enabled=False),
]


def run(args, timeout=25, user=None):
    executable=Path(args[0]).name
    stage='Running system command'
    if executable=='git':
        operation=next((word for word in args[1:] if word in ('clone','fetch','pull','switch','status','rev-parse','rev-list','diff','stash','update-ref','reset')), 'inspect')
        stage='Git: '+operation
    elif 'pip' in args: stage='Installing Python dependencies'
    elif 'venv' in args: stage='Creating Python environment'
    elif executable=='systemd-analyze': stage='Validating service file'
    elif executable=='systemctl': stage='Systemd: '+args[1]
    elif executable=='mkdir': stage='Creating checkout directory'
    elif '-c' in args: stage='Configuring Git local exclusions'
    with job_step(stage + (' · as '+user if user else '')):
        return run_command(args, timeout, user, stage)


def run_command(args, timeout, user, stage):
    if user:
        args = ["runuser", "-u", user, "--"] + args
    process = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                               text=True, cwd='/', start_new_session=os.name != 'nt',
                               env={**os.environ, 'GIT_TERMINAL_PROMPT': '0', 'LC_ALL': 'C'})
    try:
        stdout, stderr = process.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        if os.name != 'nt':
            try: os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError: pass
        else: process.kill()
        process.communicate()
        raise ValueError(stage+' timed out after '+str(timeout)+' seconds. Check the Pi before retrying.') from None
    if process.returncode:
        raise ValueError(stage+' failed (exit code '+str(process.returncode)+'). Check this operation directly on the Pi.')
    return stdout.strip()



def atomic_write(path, content):
    path = Path(path)
    if path.is_symlink() or not path.is_file():
        raise ValueError("Only existing regular files can be edited.")
    stat = path.stat()
    fd, temp = tempfile.mkstemp(dir=path.parent, prefix=".pi-deck-")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temp, stat.st_mode & 0o777)
        if hasattr(os, "chown"):
            os.chown(temp, stat.st_uid, stat.st_gid)
        os.replace(temp, path)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)


def revision(text):
    return hashlib.sha256(text.encode()).hexdigest()


def create_app(config=None, demo=False, config_path=None):
    app = Flask(__name__)
    config = config or {}
    if not demo and (len(config.get("secret_key", "")) < 32 or not config.get("password_hash", "").startswith(("scrypt:", "pbkdf2:"))):
        raise ValueError("Generate config.json credentials with setup.py first.")
    app.secret_key = config.get("secret_key") if not demo else secrets.token_hex(32)
    app.config.update(MAX_CONTENT_LENGTH=131072, SESSION_COOKIE_HTTPONLY=True,
                      SESSION_COOKIE_SAMESITE="Strict", PERMANENT_SESSION_LIFETIME=3600, SESSION_REFRESH_EACH_REQUEST=False,
                      SESSION_COOKIE_SECURE=config.get("secure_cookie", False))
    bots = copy.deepcopy(DEMO_BOTS if demo else config.get("bots", []))
    for bot in bots:
        bot.setdefault('env', '')
        bot.setdefault('icon', 'bot')
        if not isinstance(bot['env'], str) or not isinstance(bot['icon'], str) or bot['icon'] not in BOT_ICONS:
            raise ValueError('Invalid environment path or bot icon.')
        if not re.fullmatch(r"[a-zA-Z0-9_-]+", bot["id"]) or not re.fullmatch(r"[a-zA-Z0-9_.@-]+\.service", bot["service"]) or bot["service"].startswith("-"):
            raise ValueError("Invalid bot ID or systemd service name.")
        if not re.fullmatch(r"[a-z_][a-z0-9_-]*", bot["user"]) or bot["user"] == "root":
            raise ValueError("Git must run as a non-root bot owner.")
        if not demo and (not Path(bot["repo"]).is_absolute() or (bot["env"] and not Path(bot["env"]).is_absolute())):
            raise ValueError("Repository and environment paths must be absolute.")
    if len({b["id"] for b in bots}) != len(bots):
        raise ValueError("Bot IDs must be unique.")
    locks = {b["id"]: threading.RLock() for b in bots}
    demo_files = {b["id"]: {"env": "# Demo values only\nBOT_TOKEN=demo-token\nADMIN_CHAT_ID=123456789\nLOG_LEVEL=INFO\n", "service": f"[Unit]\nDescription={b['name']}\nAfter=network-online.target\n\n[Service]\nUser=pi\nWorkingDirectory={b['repo']}\nEnvironmentFile={b['env']}\nExecStart={b['repo']}/.venv/bin/python main.py\nRestart=on-failure\n\n[Install]\nWantedBy=multi-user.target\n"} for b in bots}
    failures = collections.deque(maxlen=20)
    auth_lock = threading.Lock()
    registration_lock = threading.RLock()
    disk_revision = revision(Path(config_path).read_text()) if config_path else None

    store = Store(None if demo or not config_path else Path(config_path).with_name('management-state.json'))
    jobs = Jobs(store)
    health = Health(demo)
    app.extensions.update(deck_store=store, deck_jobs=jobs)
    app.config['PERMANENT_SESSION_LIFETIME'] = config.get('session_minutes', 60)*60

    def save_config(changes):
        nonlocal disk_revision
        with registration_lock:
            if not demo:
                if not config_path:
                    raise ValueError('Start Pi Deck with a configuration file to save settings.')
                current = Path(config_path).read_text()
                if revision(current) != disk_revision:
                    raise ValueError('Configuration changed on disk. Restart Pi Deck before saving settings.')
                saved = json.loads(current)
                saved.update(changes)
                content = json.dumps(saved, indent=2) + '\n'
                atomic_write(config_path, content)
                disk_revision = revision(content)
            config.update(changes)

    def persist_bot(bot):
        progress('Saving bot registration: '+bot['id'])
        save_config({'bots': [*bots, bot]})
        locks[bot['id']] = threading.RLock()
        bots.append(bot)

    def register_bot(data):
        bot = validate(data)
        with registration_lock:
            if any(bot['id'] == b['id'] or bot['service'] == b['service'] or bot['repo'] == b['repo'] for b in bots):
                raise Conflict("That ID, service or repository is already registered.")
            if demo:
                progress('Demo: simulating bot registration; no files or services are created.')
                bot.update(state='inactive', enabled=False)
                demo_files[bot['id']] = {'env': data.get('environment', '# Demo environment\n'), 'service': unit_text(bot, data.get('command') or '/usr/bin/python3 main.py')}
                persist_bot(bot)
            else:
                if not config_path:
                    raise ValueError("Start Pi Deck with a configuration file to add bots.")
                provision(bot, data, run, persist_bot)
        return {'message': 'Demo bot added.' if demo else 'Bot added. Review settings before starting.', 'bot': bot}

    @app.post('/api/bots')
    def add_bot():
        data=request.get_json()
        validate(data)
        if data.get('background'):
            return jsonify(job=jobs.submit('Add bot', lambda: register_bot(data))), 202
        return jsonify(register_bot(data)), 201

    @app.before_request
    def protect():
        if session.get('auth') and session.get('generation') != config.get('session_generation', 0):
            session.clear()
        session.setdefault("csrf", secrets.token_hex(32))
        if request.method not in ("GET", "HEAD", "OPTIONS"):
            if not secrets.compare_digest(request.headers.get("X-CSRF-Token", ""), session["csrf"]):
                return jsonify(error="Session expired. Reload the page and try again."), 403
        if request.path.startswith("/api/") and request.path not in ("/api/session", "/api/login") and not session.get("auth"):
            return jsonify(error="Please sign in."), 401

    @app.after_request
    def headers(response):
        response.headers.update({"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff", "X-Frame-Options": "DENY", "Referrer-Policy": "no-referrer", "Content-Security-Policy": "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"})
        return response

    @app.errorhandler(Exception)
    def error(exc):
        if isinstance(exc, HTTPException):
            return jsonify(error=exc.description), exc.code
        if isinstance(exc, ValueError):
            return jsonify(error=str(exc)), 400
        if isinstance(exc, subprocess.TimeoutExpired):
            return jsonify(error="Operation timed out. Refresh status before retrying."), 504
        return jsonify(error="Operation failed. Check paths and permissions on the Pi."), 500

    @app.get("/")
    def index():
        return render_template("index.html", bot_icons=BOT_ICONS)

    @app.get("/api/session")
    def session_info():
        return jsonify(authenticated=bool(session.get("auth")), csrf=session["csrf"], demo=demo)

    @app.post("/api/login")
    def login():
        data = request.get_json()
        with auth_lock:
            now = time.monotonic()
            while failures and now - failures[0] > 300:
                failures.popleft()
            if len(failures) >= 10:
                return jsonify(error="Too many login attempts. Try again in five minutes."), 429
            valid = demo or (check_password_hash(config["password_hash"], str(data.get("password", ""))) and secrets.compare_digest(str(data.get("username", "")), config.get("username", "admin")))
            if not valid:
                failures.append(now)
                return jsonify(error="Incorrect username or password."), 401
        session.clear()
        session.update(auth=True, csrf=secrets.token_hex(32), generation=config.get("session_generation", 0))
        session.permanent = True
        return jsonify(csrf=session["csrf"])

    @app.post("/api/logout")
    def logout():
        session.clear()
        return jsonify(ok=True)

    def find_bot(bot_id):
        bot = next((b for b in bots if b["id"] == bot_id), None)
        if not bot:
            from flask import abort
            abort(404)
        return bot

    def git(bot, *args):
        return run(["git", "-C", bot["repo"], *args], timeout=90, user=bot["user"])

    def status(bot):
        item = dict(bot)
        if not demo:
            try:
                props = dict(line.split("=", 1) for line in run(["systemctl", "show", bot["service"], "--property=ActiveState,UnitFileState,LoadState"]).splitlines() if "=" in line)
                item.update(state=props.get("ActiveState", "unknown"), enabled=props.get("UnitFileState") == "enabled", startup=props.get("UnitFileState", "unknown"), load=props.get("LoadState", "unknown"))
            except (ValueError, OSError, subprocess.TimeoutExpired):
                item.update(state="unknown", enabled=False, startup="unknown")
        return item

    @app.get("/api/bots")
    def list_bots():
        return jsonify(bots=[status(b) for b in bots], demo=demo)

    @app.post("/api/bots/<bot_id>/action")
    def action(bot_id):
        find_bot(bot_id)
        data = request.get_json()
        operation = data.get('action')
        if operation not in ('start','stop','restart','enable','disable','pull'):
            raise ValueError('Unsupported action.')
        if data.get('background'):
            return jsonify(job=jobs.submit(str(operation), lambda: app.extensions['deck_version'](bot_id,'pull') if operation=='pull' else perform_action(bot_id, operation), bot_id)), 202
        return jsonify(perform_action(bot_id, operation))

    def perform_action(bot_id, action):
        bot = find_bot(bot_id)
        if action not in ("start", "stop", "restart", "enable", "disable", "pull"):
            raise ValueError("Unsupported action.")
        with locks[bot_id]:
            progress(('Demo: simulating ' if demo else 'Requested ')+action+' · '+bot['service'])
            if demo:
                if action in ("start", "restart", "stop"):
                    bot["state"] = "inactive" if action == "stop" else "active"
                if action in ("enable", "disable"):
                    bot["enabled"] = action == "enable"
                store.event(action, bot_id)
                return dict(message='Demo: latest version pulled.' if action == 'pull' else f'Demo: {action} completed.')
            if action == "pull":
                if git(bot, "status", "--porcelain"):
                    raise ValueError("Repository has local changes. Commit or stash them on the Pi before updating.")
                git(bot, "symbolic-ref", "--quiet", "--short", "HEAD")
                git(bot, "rev-parse", "--abbrev-ref", "@{upstream}")
                git(bot, "pull", "--ff-only")
                message = "Latest version pulled. Install any changed dependencies on the Pi, then restart when ready."
            else:
                run(["systemctl", action, bot["service"]])
                message = f"{action.capitalize()} completed."
            app.logger.warning("Bot %s: %s", bot_id, action)
            store.event(action, bot_id)
            return dict(message=message)

    def file_path(bot, kind):
        if kind == 'env' and not bot.get('env'):
            raise ValueError('This bot has no environment file configured.')
        return Path(bot["env"]) if kind == "env" else Path("/etc/systemd/system") / bot["service"]

    @app.route("/api/bots/<bot_id>/files/<kind>", methods=["GET", "PUT"])
    def files(bot_id, kind):
        bot = find_bot(bot_id)
        if kind not in ("env", "service"):
            raise ValueError("Unsupported file.")
        with locks[bot_id]:
            path = file_path(bot, kind)
            if not demo and (path.is_symlink() or not path.is_file()):
                raise ValueError("Editor requires an existing regular file. Service files must be in /etc/systemd/system.")
            current = demo_files[bot_id][kind] if demo else path.read_text(encoding="utf-8")
            if request.method == "GET":
                return jsonify(content=current, revision=revision(current), path=str(path))
            data = request.get_json()
            if data.get("revision") != revision(current):
                return jsonify(error="File changed since you opened it. Reopen the editor before saving."), 409
            content = data.get("content")
            if not isinstance(content, str) or "\x00" in content or len(content.encode()) > 65536:
                raise ValueError("File must be valid text under 64 KB.")
            if kind == "service" and ("[Service]" not in content or "[Unit]" not in content):
                raise ValueError("Service file needs [Unit] and [Service] sections.")
            if len(current.encode()) > 65536:
                raise ValueError('Existing file exceeds the 64 KB backup limit. Edit it on the Pi.')
            store.backup(bot_id, kind, path, current)
            if demo:
                demo_files[bot_id][kind] = content
            else:
                if kind == "service":
                    with tempfile.TemporaryDirectory() as temp:
                        candidate = Path(temp) / bot["service"]
                        candidate.write_text(content, encoding="utf-8")
                        run(["systemd-analyze", "verify", str(candidate)])
                atomic_write(path, content)
                if kind == "service":
                    try:
                        run(["systemctl", "daemon-reload"])
                    except Exception:
                        atomic_write(path, current)
                        run(["systemctl", "daemon-reload"])
                        raise ValueError("Reload failed; restored the previous service file.")
                app.logger.warning("Bot %s: saved %s", bot_id, kind)
            store.event('Save '+kind, bot_id)
            return jsonify(message="Saved. Restart the bot when ready to apply changes.", revision=revision(content))

    @app.get("/api/bots/<bot_id>/logs")
    def logs(bot_id):
        bot = find_bot(bot_id)
        text = "Demo log — no connection to your Pi.\n12:40:01  Service started\n12:40:02  Telegram polling connected\n12:45:19  Processed /help command\n" if demo else run(["journalctl", "-u", bot["service"], "-n", "150", "--no-pager", "-o", "short-iso"])
        return jsonify(content=text)

    register_features(app, dict(bots=bots, locks=locks, config=config, demo=demo,
        store=store, jobs=jobs, health=health, run=run, git=git, find=find_bot,
        status=status, save_config=save_config, registration_lock=registration_lock,
        demo_files=demo_files, file_path=file_path, perform_action=perform_action,
        register_bot=register_bot, atomic_write=atomic_write, revision=revision))
    register_self_updates(app, config, config_path, demo, store, jobs, run)
    return app


if __name__ == "__main__":
    from waitress import serve
    parser = argparse.ArgumentParser()
    parser.add_argument("--demo", action="store_true")
    parser.add_argument("--config", default="config.json")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8080)
    args = parser.parse_args()
    configuration = {} if args.demo else json.loads(Path(args.config).read_text())
    application = create_app(configuration, args.demo, None if args.demo else str(Path(args.config).resolve()))
    application.extensions['deck_start_scheduler']()
    serve(application, host=args.host, port=args.port, threads=4, channel_timeout=900)
