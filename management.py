"""Private state, bounded background jobs, backups and host telemetry."""
import copy
from contextlib import contextmanager
import json
import os
from pathlib import Path
import queue
import secrets
import shutil
import tempfile
import threading
import time


job_context = threading.local()


def progress(message):
    context = getattr(job_context, 'current', None)
    if context:
        store, job = context
        with store.lock:
            job['message'] = message
            job.setdefault('steps', []).append(dict(message=message, time=time.time(), state='info'))
            del job['steps'][:-200]
            store.save()


@contextmanager
def job_step(message):
    """Record trusted operation descriptions, never command arguments or output."""
    context = getattr(job_context, 'current', None)
    if not context:
        yield
        return
    store, job = context
    entry = dict(message=message, time=time.time(), state='running')
    started = time.monotonic()
    with store.lock:
        job['message'] = message
        job.setdefault('steps', []).append(entry)
        del job['steps'][:-200]
        store.save()
    try:
        yield
    except BaseException:
        entry_state = 'failed'
        raise
    else:
        entry_state = 'succeeded'
    finally:
        with store.lock:
            entry.update(state=entry_state, duration=round(time.monotonic()-started, 2))
            store.save()


def private_json(path, data):
    path = Path(path)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix='.deck-')
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            json.dump(data, stream, ensure_ascii=True)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp): os.unlink(tmp)


class Store:
    def __init__(self, path=None):
        self.path = Path(path) if path else None
        self.lock = threading.RLock()
        self.data = dict(events=[], logins=[], backups=[], jobs=[], versions={}, schedules=[], alerts={})
        if self.path and self.path.exists():
            if self.path.is_symlink(): raise ValueError('State file must not be a symlink.')
            self.data.update(json.loads(self.path.read_text(encoding='utf-8')))
        for job in self.data['jobs']:
            if job['state'] in ('queued', 'running'):
                job.update(state='interrupted', message='Manager restarted. Check the bot before retrying.')
                for step in job.get('steps', []):
                    if step['state'] == 'running': step['state'] = 'interrupted'

    def save(self):
        if self.path: private_json(self.path, self.data)

    def snapshot(self, key):
        with self.lock: return copy.deepcopy(self.data[key])

    def event(self, action, bot='', outcome='ok'):
        with self.lock:
            self.data['events'].insert(0, dict(time=time.time(), action=action, bot=bot, outcome=outcome))
            del self.data['events'][300:]
            self.save()

    def record_login(self, username, address):
        with self.lock:
            self.data['logins'].insert(0, dict(time=time.time(), username=username, address=address))
            del self.data['logins'][20:]
            self.save()

    def backup(self, bot, kind, path, content):
        with self.lock:
            item = dict(id=secrets.token_hex(12), bot=bot, kind=kind, path=str(path), time=time.time(), content=content)
            self.data['backups'].insert(0, item)
            count=0
            kept=[]
            for old in self.data['backups']:
                if old['bot']==bot and old['kind']==kind:
                    count+=1
                    if count>20: continue
                kept.append(old)
            self.data['backups']=kept
            self.save()
            return item['id']


class Jobs:
    def __init__(self, store):
        self.store=store
        self.queue=queue.Queue(maxsize=32)
        self.worker=None
        self.start_lock=threading.Lock()
        self.accepting=True

    def pause_for_restart(self):
        with self.start_lock:
            if not self.accepting:raise ValueError('A dashboard restart is already pending.')
            if self.queue.unfinished_tasks:raise ValueError('Wait for all background jobs to finish before restarting Pi Deck.')
            self.accepting=False

    def resume_submissions(self):
        with self.start_lock:self.accepting=True

    def submit(self, label, callback, bot=''):
        with self.start_lock:
            if not self.accepting:raise ValueError('Pi Deck is restarting. Retry after reconnecting.')
            if self.queue.full(): raise ValueError('Job queue is full. Try again after current work finishes.')
            job=dict(id=secrets.token_hex(10), label=label, bot=bot, time=time.time(), state='queued', message='Waiting', result=None, steps=[])
            with self.store.lock:
                active=[j for j in self.store.data['jobs'] if j['state'] in ('queued','running')]
                finished=[j for j in self.store.data['jobs'] if j['state'] not in ('queued','running')][:60]
                self.store.data['jobs']=[job,*active,*finished]
                self.store.save()
            self.queue.put((job,callback))
            if not self.worker or not self.worker.is_alive():
                self.worker=threading.Thread(target=self.work, daemon=True, name='pi-deck-jobs')
                self.worker.start()
        return dict(job)

    def work(self):
        while True:
            job,callback=self.queue.get()
            try:
                with self.store.lock:
                    job.update(state='running',message='Working'); self.store.save()
                job_context.current=(self.store,job)
                progress('Started: '+job['label'] + (' · '+job['bot'] if job['bot'] else ''))
                result=callback()
                progress('Finished: '+job['label'])
                with self.store.lock:
                    job.update(state='succeeded',message='Completed',result=result); self.store.save()
                self.store.event(job['label'],job['bot'])
            except Exception as exc:
                # Never record raw subprocess output, credentials or payloads.
                message=str(exc) if isinstance(exc,ValueError) else 'Operation failed. Check the Pi and retry.'
                progress('Failed: '+message[:600])
                with self.store.lock:
                    job.update(state='failed',message=message[:600]); self.store.save()
                self.store.event(job['label'],job['bot'],'failed')
            finally:
                job_context.current=None
                self.queue.task_done()


class Health:
    def __init__(self, demo=False):
        self.demo=demo; self.last=None; self.cached=None; self.at=0; self.lock=threading.Lock()

    def read(self):
        if self.demo:
            return dict(demo=True,cpu_percent=18,ram_percent=42,disk_percent=31,temperature=48.2,uptime=172800,load=[.2,.3,.2])
        with self.lock:
            if self.cached and time.monotonic()-self.at<3:return self.cached
            result=dict(demo=False,cpu_percent=None,ram_percent=None,disk_percent=None,temperature=None,uptime=None,load=None)
            try:
                fields=list(map(int,Path('/proc/stat').read_text().splitlines()[0].split()[1:9]))
                sample=(sum(fields),fields[3]+fields[4])
                if self.last and sample[0]>self.last[0]:result['cpu_percent']=round(100*(1-(sample[1]-self.last[1])/(sample[0]-self.last[0])),1)
                self.last=sample
                mem={line.split(':')[0]:int(line.split()[1]) for line in Path('/proc/meminfo').read_text().splitlines()}
                result['ram_percent']=round(100*(1-mem['MemAvailable']/mem['MemTotal']),1)
                result['uptime']=float(Path('/proc/uptime').read_text().split()[0])
                result['load']=list(os.getloadavg())
            except (OSError,ValueError,KeyError):pass
            try:
                disk=shutil.disk_usage('/'); result['disk_percent']=round(100*disk.used/disk.total,1)
            except OSError:pass
            try: result['temperature']=round(float(Path('/sys/class/thermal/thermal_zone0/temp').read_text())/1000,1)
            except (OSError,ValueError):pass
            self.cached=result; self.at=time.monotonic()
            return result
