"""Updates for the fixed Pi Deck checkout, separate from bot registrations."""
import copy
import json
import os
from pathlib import Path
import re
import secrets
import threading
import time

from flask import jsonify, request
from git_review import review, force_update
from management import private_json, progress

APP_ROOT = Path(__file__).resolve().parent


def register_self_updates(app, config, config_path, demo, store, jobs, run):
    root=APP_ROOT
    service=config.get('manager_service','pi-deck.service')
    if not isinstance(service,str) or not re.fullmatch(r'[a-zA-Z0-9_][a-zA-Z0-9_.-]*\.service',service):
        raise ValueError('Invalid manager_service in configuration.')
    manager=dict(id='pi-deck',name='Pi Deck',repo=str(root),env='',user='',service=service)
    lock=threading.RLock()
    with store.lock:store.data.setdefault('self_update',{})

    def state():return store.snapshot('self_update')

    def save(**changes):
        with store.lock:
            store.data['self_update'].update(changes);store.save()

    def git(bot,*args):
        # The supplied service owns this checkout as root. Never accept a path from HTTP.
        return run(['git','-C',str(root),*args],timeout=90)

    def clean():
        if git(manager,'status','--porcelain'):
            raise ValueError('Pi Deck has local changes. Review changes / force update, or commit/stash them first.')

    def protect(target):
        if not config_path or Path(config_path).resolve().is_relative_to(root):
            raise ValueError('Move private configuration and management state outside the application checkout (for example /etc/pi-deck), then restart with that config path before updating.')
        entries=git(manager,'ls-tree','-r','--name-only','-z',target).split('\0')
        tracked=git(manager,'ls-files','-z').split('\0')
        for name in entries+tracked:
            parts=Path(name).parts
            if any(part in ('.venv','.env','config.json','management-state.json','update-backups') or part.startswith('.env.') for part in parts):
                raise ValueError('The checkout or target tracks private configuration, state, or the virtual environment. Resolve this on the Pi before updating.')
        if 'app.py' not in entries or 'requirements.txt' not in entries:
            raise ValueError('The target does not contain a Pi Deck application. Check the configured upstream.')

    def backup_private():
        progress('Backing up Pi Deck configuration and management state outside the checkout')
        base=Path(config_path).resolve().parent/'update-backups'
        if base.is_symlink():raise ValueError('Update backup directory must not be a symlink.')
        base.mkdir(mode=0o700,exist_ok=True)
        destination=base/(time.strftime('%Y%m%d-%H%M%S')+'-'+secrets.token_hex(4))
        destination.mkdir(mode=0o700)
        private_json(destination/'config.json',json.loads(Path(config_path).read_text()))
        with store.lock:private_json(destination/'management-state.json',copy.deepcopy(store.data))
        save(private_backup=str(destination))

    def save_recovery(recovery):
        save(recovery=recovery,rollback=recovery['head'],return_branch=recovery['branch'])

    def checkpoint(head,branch):
        ref='refs/pi-deck/recovery/'+secrets.token_hex(12)
        git(manager,'update-ref',ref,head)
        save_recovery(dict(ref=ref,head=head,branch=branch,repo=str(root),stash=False))

    def preview():
        if demo:
            return dict(demo=True,head='a7e9c42',target='f12ab83',branch='main',upstream='origin/main',ahead=0,behind=1,
                revision='demo-pi-deck-'+str(state().get('current','old')),can_force=True,blockers=[],untracked=[],untracked_count=0,
                sections=[dict(title='Pi Deck local edits (demo)',patch='diff --git a/app.py b/app.py\n--- a/app.py\n+++ b/app.py\n@@ -1 +1 @@\n-PORT = 8080\n+PORT = 8081'),
                          dict(title='Incoming Pi Deck changes (demo)',patch='diff --git a/requirements.txt b/requirements.txt\n--- a/requirements.txt\n+++ b/requirements.txt\n@@ -1 +1 @@\n-Flask>=3.0,<4\n+Flask>=3.1,<4')],recovery=state().get('recovery'))
        result=review(manager,git)
        try:protect(result['target'])
        except ValueError as exc:
            result['blockers'].append(str(exc));result['can_force']=False
        result['recovery']=state().get('recovery')
        return result

    @app.get('/api/system/update')
    def info():
        return jsonify(demo=demo,repo=str(root),service=service,state=state(),restart_pending=not jobs.accepting)

    @app.post('/api/system/git-preview')
    def show_preview():
        with lock:return jsonify(preview())

    def perform(operation, data):
        with lock:
            if demo:
                if operation=='force' and data.get('revision')!=preview()['revision']:
                    raise ValueError('Preview changed. Review Pi Deck again.')
                progress('Demo: simulating Pi Deck '+operation+'; no application files are changed')
                save(current='a7e9c42' if operation=='rollback' else 'f12ab83',branch='main',checked=time.time(),
                     restart_required=operation!='check')
                return dict(message='Demo: Pi Deck '+operation+' simulated. No real update or restart.',**state())
            if operation=='check':
                git(manager,'fetch','--prune')
            else:
                target=git(manager,'rev-parse','HEAD')
                if operation=='pull':
                    clean();git(manager,'fetch','--prune');target=git(manager,'rev-parse','@{upstream}')
                elif operation=='force':
                    result=preview()
                    if result['revision']!=data.get('revision'):raise ValueError('Preview changed. Review Pi Deck again.')
                    if not result['can_force']:raise ValueError('Force update blocked: '+' '.join(result['blockers']))
                    target=result['target']
                elif operation=='rollback':
                    clean();target=state().get('rollback','')
                    if not re.fullmatch(r'[0-9a-f]{40,64}',target):raise ValueError('No Pi Deck rollback commit recorded.')
                elif operation=='resume':
                    clean();branch=state().get('return_branch','')
                    if not branch or branch.startswith('-'):raise ValueError('No original Pi Deck branch recorded.')
                    target=git(manager,'rev-parse','refs/heads/'+branch)
                protect(target);backup_private()
                if operation=='force':
                    force_update(manager,git,data['revision'],save_recovery)
                elif operation=='pull':
                    old=git(manager,'rev-parse','HEAD');branch=git(manager,'symbolic-ref','--quiet','--short','HEAD')
                    if old!=target:checkpoint(old,branch)
                    git(manager,'merge','--ff-only',target)
                elif operation=='rollback':git(manager,'switch','--detach',target)
                elif operation=='resume':git(manager,'switch',branch)
                elif operation=='dependencies':
                    python=root/'.venv/bin/python';requirements=root/'requirements.txt'
                    if not python.is_file() or not requirements.is_file():raise ValueError('Pi Deck requires an existing .venv and requirements.txt.')
                    run([str(python),'-m','pip','install','-r',str(requirements)],timeout=600)
                save(restart_required=True)
            current=git(manager,'rev-parse','--short','HEAD');branch=git(manager,'rev-parse','--abbrev-ref','HEAD')
            counts={}
            if branch!='HEAD':
                try:
                    ahead,behind=map(int,git(manager,'rev-list','--left-right','--count','HEAD...@{upstream}').split())
                    counts=dict(ahead=ahead,behind=behind)
                except ValueError:pass
            save(current=current,branch=branch,checked=time.time(),ahead=counts.get('ahead'),behind=counts.get('behind'))
            return dict(message='Pi Deck '+operation+' completed.'+(' Restart Pi Deck to load the code. Install changed requirements first.' if operation!='check' else ''),**state())

    @app.post('/api/system/update')
    def update():
        data=request.get_json();operation=data.get('action')
        if operation not in ('check','pull','force','rollback','resume','dependencies'):raise ValueError('Unsupported Pi Deck update action.')
        if operation=='force' and (data.get('confirm')!='pi-deck' or not isinstance(data.get('revision'),str)):
            raise ValueError('Review the diff and type pi-deck to confirm replacing dashboard code.')
        return jsonify(job=jobs.submit('Pi Deck '+operation,lambda:perform(operation,data),'pi-deck')),202

    @app.post('/api/system/restart')
    def restart():
        if request.get_json().get('confirm')!='pi-deck':raise ValueError('Type pi-deck to confirm restarting the dashboard.')
        jobs.pause_for_restart()
        try:
            if demo:
                store.event('Demo: Pi Deck restart simulated');jobs.resume_submissions()
                return jsonify(message='Demo: restart simulated. The dashboard remains running.')
            # Only restart a service whose main process is this application.
            pid=run(['systemctl','show',service,'--property=MainPID','--value'])
            if pid!=str(os.getpid()):raise ValueError('Pi Deck is not the main process of '+service+'. Restart it manually, or configure manager_service correctly.')
            store.event('Pi Deck restart requested')
            save(restart_required=False)
            run(['systemd-run','--unit=pi-deck-restart-'+secrets.token_hex(6),'--on-active=3s',
                 '--collect','/usr/bin/systemctl','restart',service])
            def restart_watchdog():
                # A successful restart kills this process and its daemon timer.
                jobs.resume_submissions()
                try:
                    save(restart_required=True)
                    store.event('Pi Deck restart did not complete; inspect systemd and retry',outcome='failed')
                except OSError:app.logger.error('Could not record failed Pi Deck restart.')
            watchdog=threading.Timer(30,restart_watchdog);watchdog.daemon=True;watchdog.start()
            return jsonify(message='Pi Deck will restart in a few seconds. Reload this page after reconnecting.')
        except Exception:
            jobs.resume_submissions();save(restart_required=True);raise

    app.extensions['deck_self_update']=perform
