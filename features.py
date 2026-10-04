"""Management endpoints. Real machine operations are always explicit or scheduled."""
import copy
import json
import re
import threading
import time
import secrets
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.parse import urlencode
from flask import jsonify, request, session, Response
from werkzeug.security import check_password_hash, generate_password_hash
from werkzeug.exceptions import Conflict, NotFound
from onboarding import validate


def register_features(app, c):
    bots, store, jobs = c['bots'], c['store'], c['jobs']
    demo, run, git, find = c['demo'], c['run'], c['git'], c['find']

    def backup(bot_id):
        bot=find(bot_id)
        with c['locks'][bot_id]:
            for kind in ('env','service'):
                path=c['file_path'](bot,kind)
                if not demo and (path.is_symlink() or not path.is_file()):
                    raise ValueError('Backup requires existing regular environment and service files.')
                text=c['demo_files'][bot_id][kind] if demo else path.read_text(encoding='utf-8')
                if len(text.encode())>65536:raise ValueError('File is larger than the 64 KB backup limit.')
                store.backup(bot_id,kind,path,text)
        return {'message':'Environment and service backed up.'}

    def clean(bot):
        if git(bot,'status','--porcelain'):raise ValueError('Repository has local changes. Commit or stash them on the Pi first.')

    def version(bot_id, operation):
        bot=find(bot_id)
        with c['locks'][bot_id]:
            previous=store.snapshot('versions').get(bot_id,{})
            if demo:
                result={'current':'a7e9c42','branch':'main','behind':2,'ahead':0,'checked':time.time(),'repo':bot['repo']}
                if operation=='check':
                    result.update({k:v for k,v in previous.items() if k in ('current','branch','behind','ahead')})
                    result.update({k:v for k,v in previous.items() if k in ('rollback','return_branch','detached')})
                elif operation in ('pull','update'):
                    result.update(current='f12ab83',behind=0,rollback='a7e9c42',return_branch='main')
                    if operation=='update':bot['state']='active'
                elif operation=='rollback':
                    if not previous.get('rollback'):raise ValueError('No previous version recorded. Update this bot first.')
                    result.update(current=previous['rollback'],detached=True,return_branch='main')
                elif operation=='resume':result.update(current='f12ab83',behind=0,detached=False)
                elif operation=='dependencies':return {'message':'Demo: dependencies installed.'}
            else:
                if operation=='dependencies':
                    python=Path(bot['repo'])/'.venv/bin/python'; req=Path(bot['repo'])/'requirements.txt'
                    if not python.is_file() or not req.is_file():raise ValueError('Requires an existing .venv and requirements.txt.')
                    run([str(python),'-m','pip','install','-r',str(req)],timeout=600,user=bot['user'])
                    return {'message':'Dependencies installed. Restart the bot when ready.'}
                if operation in ('pull','update','rollback','resume'):clean(bot)
                if operation=='check':git(bot,'fetch','--prune')
                if operation in ('pull','update'):
                    branch=git(bot,'symbolic-ref','--quiet','--short','HEAD')
                    old=git(bot,'rev-parse','HEAD')
                    if not re.fullmatch(r'[0-9a-f]{40,64}',old):raise ValueError('Cannot identify current commit.')
                    git(bot,'pull','--ff-only')
                    new=git(bot,'rev-parse','HEAD')
                    if new != old:
                        previous={'rollback':old,'return_branch':branch,'repo':bot['repo']}
                    # Save rollback metadata before restart or subsequent inspections can fail.
                    with store.lock:
                        store.data['versions'][bot_id]=previous;store.save()
                    if operation=='update':run(['systemctl','restart',bot['service']])
                elif operation=='rollback':
                    if previous.get('repo')!=bot['repo'] or not re.fullmatch(r'[0-9a-f]{40,64}',previous.get('rollback','')):
                        raise ValueError('No rollback commit recorded for this checkout.')
                    git(bot,'switch','--detach',previous['rollback'])
                elif operation=='resume':
                    branch=previous.get('return_branch','')
                    if previous.get('repo')!=bot['repo'] or not branch or branch.startswith('-'):
                        raise ValueError('No original branch recorded for this checkout.')
                    git(bot,'switch',branch)
                current=git(bot,'rev-parse','--short','HEAD')
                branch=git(bot,'rev-parse','--abbrev-ref','HEAD')
                result={**previous,'repo':bot['repo'],'current':current,'branch':branch,'detached':branch=='HEAD','checked':time.time(),'behind':None,'ahead':None}
                if branch!='HEAD':
                    try:
                        counts=git(bot,'rev-list','--left-right','--count','HEAD...@{upstream}').split()
                        result.update(ahead=int(counts[0]),behind=int(counts[1]))
                    except (ValueError,IndexError):pass
            with store.lock:
                store.data['versions'][bot_id]=result;store.save()
            return {'message':{'check':'Update check complete.','rollback':'Previous code checked out in detached mode. Restart when ready. Dependencies and data were not rolled back.','resume':'Returned to the original branch. Restart when ready.','update':'Code updated and service restarted.','pull':'Code updated. Restart when ready.'}.get(operation,'Completed'),**result}

    @app.get('/api/health')
    def health():return jsonify(c['health'].read())

    @app.get('/api/jobs')
    def job_list():return jsonify(jobs=store.snapshot('jobs'))

    @app.get('/api/activity')
    def activity():return jsonify(events=store.snapshot('events'))

    @app.get('/api/versions')
    def versions():return jsonify(versions=store.snapshot('versions'))

    @app.post('/api/bots/<bot_id>/maintenance')
    def maintenance(bot_id):
        find(bot_id)
        op=request.get_json().get('action')
        if op not in ('check','pull','update','rollback','resume','dependencies','backup'):raise ValueError('Unsupported maintenance action.')
        return jsonify(job=jobs.submit(op,lambda:backup(bot_id) if op=='backup' else version(bot_id,op),bot_id)),202

    @app.get('/api/bots/<bot_id>/backups')
    def backups(bot_id):
        find(bot_id)
        return jsonify(backups=[{k:v for k,v in b.items() if k!='content'} for b in store.snapshot('backups') if b['bot']==bot_id])

    @app.get('/api/bots/<bot_id>/backups/<backup_id>')
    def backup_content(bot_id,backup_id):
        bot=find(bot_id)
        item=next((b for b in store.snapshot('backups') if b['bot']==bot_id and b['id']==backup_id),None)
        if not item:raise NotFound()
        if item['kind']!='settings' and item['path']!=str(c['file_path'](bot,item['kind'])):raise ValueError('Backup belongs to an old file path. Restore it manually on the Pi.')
        return jsonify(item)

    @app.put('/api/bots/<bot_id>/settings')
    def bot_settings(bot_id):
        data=request.get_json()
        find(bot_id)
        with c['registration_lock'],c['locks'][bot_id]:
            bot=find(bot_id)
            if data.get('revision')!=c['revision'](json.dumps({k:bot[k] for k in ('id','name','description','user','repo','env','service')},sort_keys=True)):
                raise Conflict('Bot settings changed. Reopen settings.')
            replacement=validate({**data,'id':bot_id,'mode':'manual'})
            if any(b['id']!=bot_id and (b['service']==replacement['service'] or b['repo']==replacement['repo']) for b in bots):raise Conflict('Service or checkout already registered.')
            if not demo:
                # Reuse manual validation, but persist only after all checks succeed.
                from onboarding import provision
                provision(replacement,{'mode':'manual'},run,lambda b:None)
            original={k:bot[k] for k in replacement}
            store.backup(bot_id,'settings','registration',json.dumps(original,indent=2))
            updated={**bot,**replacement}
            c['save_config']({'bots':[updated if b['id']==bot_id else b for b in bots]})
            bot.update(replacement)
        store.event('Edit bot settings',bot_id)
        return jsonify(message='Registration updated. Use Service file to change the actual launch command or runtime paths.')

    @app.get('/api/bots/<bot_id>/settings')
    def read_settings(bot_id):
        bot=find(bot_id)
        data={k:bot[k] for k in ('id','name','description','user','repo','env','service')}
        return jsonify(bot=data,revision=c['revision'](json.dumps(data,sort_keys=True)))

    @app.post('/api/bots/<bot_id>/remove')
    def remove(bot_id):
        data=request.get_json();bot=find(bot_id)
        if data.get('confirm')!=bot_id:raise ValueError('Type the bot ID to confirm removal.')
        uninstall=data.get('uninstall') is True
        def execute():
            with c['registration_lock'],c['locks'][bot_id]:
                bot=find(bot_id)
                if uninstall:
                    backup(bot_id)
                    if not demo:
                        unit=c['file_path'](bot,'service')
                        # Only this exact regular /etc unit; never remove repository data.
                        if unit.is_symlink() or not unit.is_file():raise ValueError('Only regular /etc/systemd/system service files can be uninstalled.')
                        run(['systemctl','disable','--now',bot['service']])
                        saved=unit.read_text(encoding='utf-8')
                        unit.unlink()
                        try:run(['systemctl','daemon-reload'])
                        except Exception:
                            unit.write_text(saved,encoding='utf-8');raise ValueError('Reload failed; service file restored. Check service status on the Pi.')
                try:
                    c['save_config']({'bots':[b for b in bots if b['id']!=bot_id]})
                except Exception:
                    if uninstall and not demo:
                        unit.write_text(saved,encoding='utf-8')
                        run(['systemctl','daemon-reload'])
                        raise ValueError('Registration save failed; service file restored. The service remains stopped and disabled; inspect it on the Pi.')
                    raise
                bots[:]=[b for b in bots if b['id']!=bot_id]
                with store.lock:
                    for schedule in store.data['schedules']:
                        if schedule['bot']==bot_id:schedule['enabled']=False
                    store.save()
            return {'message':'Bot unregistered. Repository and environment files retained.'}
        return jsonify(job=jobs.submit('Uninstall service' if uninstall else 'Unregister',execute,bot_id)),202

    @app.post('/api/bulk')
    def bulk():
        data=request.get_json();ids=data.get('ids');op=data.get('action')
        if not isinstance(ids,list) or not 1<=len(ids)<=50 or any(not isinstance(i,str) for i in ids):raise ValueError('Select 1–50 bots.')
        if op not in ('start','stop','restart','check','update','backup'):raise ValueError('Unsupported bulk action.')
        ids=list(dict.fromkeys(ids))
        for bot_id in ids:find(bot_id)
        def execute():
            result=[]
            for bot_id in ids:
                try:
                    if op=='backup':backup(bot_id)
                    elif op in ('check','update'):version(bot_id,op)
                    else:c['perform_action'](bot_id,op)
                    result.append({'bot':bot_id,'outcome':'succeeded'})
                except Exception:result.append({'bot':bot_id,'outcome':'failed; inspect this bot separately'})
            return {'message':'Bulk action complete. Review individual results.','results':result}
        return jsonify(job=jobs.submit('Bulk '+op,execute)),202

    @app.get('/api/discovery')
    def discovery():
        if demo:return jsonify(services=[{'service':'weather-bot.service','description':'Example existing service'}])
        raw=run(['systemctl','list-unit-files','--type=service','--no-legend','--no-pager'])
        registered={b['service'] for b in bots}
        items=[]
        for line in raw.splitlines():
            name=line.split()[0] if line.split() else ''
            if not re.fullmatch(r'[a-zA-Z0-9_][a-zA-Z0-9_.-]*\.service',name) or name in registered:continue
            path=Path('/etc/systemd/system')/name
            if path.is_file() and not path.is_symlink():items.append({'service':name,'description':'Local service; verify this is a bot before importing.'})
        return jsonify(services=items[:200])

    @app.get('/api/bots/<bot_id>/log-stream')
    def log_stream(bot_id):
        bot=find(bot_id); priority=request.args.get('priority','all'); query=request.args.get('q','')[:200].lower()
        if priority not in ('all','warning','error'):raise ValueError('Invalid log priority.')
        if demo:
            stamp=time.strftime('%Y-%m-%d %H:%M:%S',time.gmtime())
            records=[{'priority':6,'text':stamp+' [INFO] Demo polling heartbeat'}, {'priority':4,'text':stamp+' [WARNING] Demo connection retry'}, {'priority':3,'text':stamp+' [ERROR] Demo example error (sample only)'}]
        else:
            args=['journalctl','-u',bot['service'],'-n','200','--no-pager','-o','json']
            if priority!='all':args+=['-p','warning' if priority=='warning' else 'err']
            records=[]
            for line in run(args).splitlines():
                item=json.loads(line);message=item.get('MESSAGE','')
                if not isinstance(message,str):message='[Non-text journal entry]'
                stamp=time.strftime('%Y-%m-%d %H:%M:%S',time.gmtime(int(item.get('__REALTIME_TIMESTAMP','0'))/1000000))
                records.append({'priority':int(item.get('PRIORITY',6)),'text':stamp+' '+message[:12000]})
        limit=7 if priority=='all' else 4 if priority=='warning' else 3
        text='\n'.join(r['text'] for r in records if r['priority']<=limit and query in r['text'].lower())
        if request.args.get('download')=='1':
            return Response(text,mimetype='text/plain',headers={'Content-Disposition':'attachment; filename="bot-logs.txt"'})
        return jsonify(content=text,time=time.time(),note='Last 200 matching journal entries. Times are UTC. Severity comes from journald metadata.')

    @app.get('/api/schedules')
    def schedules():return jsonify(schedules=store.snapshot('schedules'))

    @app.post('/api/schedules')
    def save_schedule():
        data=request.get_json();bot_id=data.get('bot');find(bot_id)
        if data.get('action') not in ('restart','backup','check'):raise ValueError('Choose restart, backup or update check.')
        minutes=data.get('minutes')
        if isinstance(minutes,bool) or not isinstance(minutes,int) or not 5<=minutes<=10080:raise ValueError('Interval must be 5–10080 minutes.')
        with store.lock:
            if len(store.data['schedules'])>=30:raise ValueError('Maximum 30 schedules.')
            store.data['schedules'].append(dict(id=secrets.token_hex(8),bot=bot_id,action=data['action'],minutes=minutes,enabled=True,next=time.time()+minutes*60,last=None,job=None))
            store.save()
        store.event('Add schedule',bot_id)
        return jsonify(message='Schedule added. It runs while Pi Deck is running.'),201

    @app.put('/api/schedules/<schedule_id>')
    def toggle_schedule(schedule_id):
        enabled=request.get_json().get('enabled')
        if not isinstance(enabled,bool):raise ValueError('Enabled must be true or false.')
        with store.lock:
            row=next((s for s in store.data['schedules'] if s['id']==schedule_id),None)
            if not row:raise NotFound()
            find(row['bot']);row.update(enabled=enabled,next=time.time()+row['minutes']*60);store.save()
        return jsonify(message='Schedule updated.')

    @app.delete('/api/schedules/<schedule_id>')
    def delete_schedule(schedule_id):
        with store.lock:
            store.data['schedules']=[s for s in store.data['schedules'] if s['id']!=schedule_id];store.save()
        return jsonify(message='Schedule deleted.')

    @app.get('/api/alerts')
    def get_alerts():
        settings=store.snapshot('alerts')
        return jsonify(enabled=settings.get('enabled',False),chat_id=settings.get('chat_id',''),configured=bool(settings.get('token')),temperature=settings.get('temperature',75),disk=settings.get('disk',90),last_error=settings.get('last_error'))

    @app.put('/api/alerts')
    def save_alerts():
        data=request.get_json()
        if not isinstance(data.get('enabled'),bool):raise ValueError('Enabled must be true or false.')
        temperature=data.get('temperature',75);disk=data.get('disk',90)
        if not isinstance(temperature,(int,float)) or not 40<=temperature<=100 or not isinstance(disk,(int,float)) or not 50<=disk<=99:raise ValueError('Use a temperature of 40–100°C and disk threshold of 50–99%.')
        with store.lock:
            old=store.data['alerts'];token=data.get('token') or old.get('token','');chat_id=str(data.get('chat_id','')).strip()
            if data['enabled'] and (not re.fullmatch(r'[0-9]+:[A-Za-z0-9_-]+',token) or not re.fullmatch(r'-?[0-9]+',chat_id)):raise ValueError('Enter a Telegram bot token and numeric chat ID.')
            store.data['alerts']={**old,'token':token,'chat_id':chat_id,'enabled':data['enabled'],'temperature':temperature,'disk':disk}
            store.save()
        store.event('Configure alerts')
        return jsonify(message='Alert settings saved. No test message was sent.')

    @app.post('/api/account')
    def account():
        data=request.get_json()
        if not demo and not check_password_hash(c['config']['password_hash'],str(data.get('current_password',''))):raise ValueError('Current password is incorrect.')
        minutes=data.get('session_minutes',60)
        if isinstance(minutes,bool) or not isinstance(minutes,int) or not 5<=minutes<=1440:raise ValueError('Session duration must be 5–1440 minutes.')
        changes={'session_minutes':minutes,'session_generation':c['config'].get('session_generation',0)+1}
        password=data.get('password','')
        if password:
            if not isinstance(password,str) or not 12<=len(password)<=256:raise ValueError('Password must contain 12–256 characters.')
            changes['password_hash']=generate_password_hash(password)
        c['save_config'](changes);app.config['PERMANENT_SESSION_LIFETIME']=minutes*60
        session.clear();store.event('Update account and revoke sessions')
        return jsonify(message='Account updated. All sessions revoked. Sign in again.')

    @app.get('/api/account')
    def account_info():return jsonify(username=c['config'].get('username','admin'),session_minutes=c['config'].get('session_minutes',60))

    @app.post('/api/power')
    def power():
        data=request.get_json();op=data.get('action')
        if op not in ('reboot','poweroff') or data.get('confirm')!=op.upper():raise ValueError('Type REBOOT or POWEROFF to confirm.')
        def execute():
            if not demo:
                time.sleep(3);run(['systemctl',op])
            return {'message':'Demo: power action simulated.' if demo else 'Power action requested.'}
        return jsonify(job=jobs.submit('Pi '+op,execute)),202

    stop=threading.Event()
    def tick():
        now=time.time()
        for item in store.snapshot('schedules'):
            if not item['enabled'] or item['next']>now:continue
            # Skip overlapping runs and never replay missed intervals as a burst.
            old=next((j for j in store.snapshot('jobs') if j['id']==item.get('job')),None)
            if old and old['state'] in ('queued','running'):continue
            bot_id,op=item['bot'],item['action']
            try:
                find(bot_id)
                job=jobs.submit('Scheduled '+op,lambda b=bot_id,a=op:backup(b) if a=='backup' else version(b,'check') if a=='check' else c['perform_action'](b,'restart'),bot_id)
                with store.lock:
                    row=next((s for s in store.data['schedules'] if s['id']==item['id']),None)
                    if row:row.update(next=now+item['minutes']*60,last=now,job=job['id'])
                    store.save()
            except Exception:
                store.event('Schedule failed',bot_id,'failed')
                with store.lock:
                    for row in store.data['schedules']:
                        if row['id']==item['id']:row['next']=now+item['minutes']*60
                    store.save()
        monitor_alerts()

    def monitor_alerts():
        settings=store.snapshot('alerts')
        if not settings.get('enabled'):return
        current={}
        for bot in list(bots):
            status=c['status'](bot)
            if status['state'] in ('failed','unknown'):current['bot:'+bot['id']]='Bot '+bot['id']+' needs attention.'
        health=c['health'].read()
        for key,threshold in [('temperature',settings.get('temperature',75)),('disk_percent',settings.get('disk',90))]:
            if health.get(key) is not None and health[key]>=threshold:current[key]='Pi '+key+' exceeded its configured threshold.'
        previous=settings.get('active',{})
        changes=[current[k] for k in current if k not in previous]+['Recovered: '+k for k in previous if k not in current]
        if not changes:return
        if time.time()-settings.get('last_sent',0)<900:return
        try:
            if not demo:
                # Fixed Telegram destination; tokens never appear in responses or logs.
                req=Request('https://api.telegram.org/bot'+settings['token']+'/sendMessage',data=urlencode({'chat_id':settings['chat_id'],'text':'Pi Deck\n'+'\n'.join(changes)}).encode(),method='POST')
                with urlopen(req,timeout=10) as response:
                    if not json.load(response).get('ok'):raise ValueError('Telegram rejected notification.')
            with store.lock:
                store.data['alerts'].update(active=current,last_sent=time.time(),last_error=None);store.save()
            store.event('Demo alert simulated' if demo else 'Telegram alert sent')
        except Exception:
            with store.lock:
                store.data['alerts'].update(last_error='Notification failed. Check token, chat ID and network.',last_sent=time.time());store.save()

    def loop():
        while not stop.wait(60):
            try:tick()
            except Exception:app.logger.error('Scheduled maintenance failed; inspect local configuration.')
    def start():
        if not app.extensions.get('deck_scheduler'):
            worker=threading.Thread(target=loop,daemon=True,name='pi-deck-scheduler')
            app.extensions['deck_scheduler']=worker;worker.start()
    app.extensions.update(deck_start_scheduler=start,deck_scheduler_stop=stop,deck_tick=tick,deck_version=version)
