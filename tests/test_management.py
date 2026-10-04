import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch
from werkzeug.security import generate_password_hash
from app import create_app
from management import Store, Jobs, Health


class ManagementTests(unittest.TestCase):
    def setUp(self):
        self.app=create_app(demo=True);self.client=self.app.test_client()
        token=self.client.get('/api/session').json['csrf']
        login=self.client.post('/api/login',json={},headers={'X-CSRF-Token':token})
        self.headers={'X-CSRF-Token':login.json['csrf']}

    def send(self,path,data=None,method='POST'):
        return self.client.open('/api/'+path,method=method,json=data or {},headers=self.headers)

    def finish(self,response):
        self.assertEqual(response.status_code,202,response.json)
        job_id=response.json['job']['id']
        self.app.extensions['deck_jobs'].queue.join()
        return next(j for j in self.client.get('/api/jobs').json['jobs'] if j['id']==job_id)

    def test_background_controls_and_audit(self):
        job=self.finish(self.send('bots/notifier/action',{'action':'stop','background':True}))
        self.assertEqual(job['state'],'succeeded')
        self.assertEqual(self.client.get('/api/bots').json['bots'][0]['state'],'inactive')
        self.assertTrue(self.client.get('/api/activity').json['events'])
        self.assertEqual(self.send('bots/notifier/action',{'action':'bad','background':True}).status_code,400)

    def test_auto_backup_restore_and_retention(self):
        path='/api/bots/notifier/files/env'
        original=self.client.get(path).json
        current=original
        for i in range(22):
            response=self.send('bots/notifier/files/env',{'content':'TOKEN=secret-'+str(i),'revision':current['revision']},'PUT')
            self.assertEqual(response.status_code,200)
            current=response.json
        backups=self.client.get('/api/bots/notifier/backups').json['backups']
        self.assertEqual(len(backups),20)
        self.assertNotIn('content',backups[0])
        old=self.client.get('/api/bots/notifier/backups/'+backups[0]['id']).json
        response=self.send('bots/notifier/files/env',{'content':old['content'],'revision':current['revision']},'PUT')
        self.assertEqual(response.status_code,200)
        self.assertEqual(self.client.get(path).json['content'],old['content'])
        self.assertNotIn('secret-',json.dumps(self.client.get('/api/activity').json))

    def test_settings_conflict_and_backup(self):
        response=self.client.get('/api/bots/notifier/settings').json
        data={**response['bot'],'name':'Renamed','revision':response['revision']}
        self.assertEqual(self.send('bots/notifier/settings',data,'PUT').status_code,200)
        self.assertEqual(self.send('bots/notifier/settings',data,'PUT').status_code,409)
        backup=self.client.get('/api/bots/notifier/backups').json['backups'][0]
        self.assertEqual(backup['kind'],'settings')
        self.assertEqual(self.client.get('/api/bots/notifier/backups/'+backup['id']).status_code,200)

    def test_update_rollback_resume(self):
        failed=self.finish(self.send('bots/notifier/maintenance',{'action':'rollback'}))
        self.assertEqual(failed['state'],'failed')
        for op in ('check','update','rollback','resume','dependencies'):
            job=self.finish(self.send('bots/notifier/maintenance',{'action':op}))
            self.assertEqual(job['state'],'succeeded',job)
        v=self.client.get('/api/versions').json['versions']['notifier']
        self.assertFalse(v['detached'])

    def test_bulk_partial_results(self):
        job=self.finish(self.send('bulk',{'ids':['notifier','assistant'],'action':'stop'}))
        self.assertEqual(len(job['result']['results']),2)
        self.assertEqual(self.send('bulk',{'ids':['unknown'],'action':'stop'}).status_code,404)
        self.assertEqual(self.send('bulk',{'ids':[],'action':'stop'}).status_code,400)

    def test_remove_requires_confirmation_and_retains_other_bots(self):
        self.assertEqual(self.send('bots/notifier/remove',{'confirm':'wrong'}).status_code,400)
        job=self.finish(self.send('bots/notifier/remove',{'confirm':'notifier','uninstall':True}))
        self.assertEqual(job['state'],'succeeded')
        self.assertEqual(len(self.client.get('/api/bots').json['bots']),2)
        self.assertTrue(self.app.extensions['deck_store'].snapshot('backups'))

    def test_logs_filters_and_download(self):
        response=self.client.get('/api/bots/notifier/log-stream?priority=error&q=example')
        self.assertIn('ERROR',response.json['content'])
        self.assertNotIn('INFO',response.json['content'])
        download=self.client.get('/api/bots/notifier/log-stream?download=1')
        self.assertEqual(download.mimetype,'text/plain')
        self.assertIn('attachment',download.headers['Content-Disposition'])
        self.assertEqual(self.client.get('/api/bots/notifier/log-stream?priority=bad').status_code,400)

    def test_schedule_tick_does_not_repeat_missed_intervals(self):
        self.assertEqual(self.send('schedules',{'bot':'notifier','action':'restart','minutes':1}).status_code,400)
        self.assertEqual(self.send('schedules',{'bot':'notifier','action':'backup','minutes':5}).status_code,201)
        store=self.app.extensions['deck_store']
        store.data['schedules'][0]['next']=1
        self.app.extensions['deck_tick']();self.app.extensions['deck_jobs'].queue.join()
        self.app.extensions['deck_tick']()
        jobs=self.client.get('/api/jobs').json['jobs']
        self.assertEqual(len(jobs),1)
        self.assertEqual(jobs[0]['state'],'succeeded')
        sid=store.data['schedules'][0]['id']
        self.assertEqual(self.send('schedules/'+sid,{'enabled':False},'PUT').status_code,200)
        self.assertEqual(self.send('schedules/'+sid,{},'DELETE').status_code,200)

    def test_alert_secrets_masked_and_demo_never_sends(self):
        with patch('features.urlopen',side_effect=AssertionError('Demo made network call')):
            self.assertEqual(self.send('alerts',{'enabled':True,'token':'123:test_token','chat_id':'123','temperature':40,'disk':50},'PUT').status_code,200)
            self.app.extensions['deck_tick']()
        settings=self.client.get('/api/alerts').json
        self.assertTrue(settings['configured'])
        self.assertNotIn('test_token',json.dumps(settings))
        self.assertNotIn('test_token',json.dumps(self.client.get('/api/activity').json))

    def test_account_revokes_existing_sessions(self):
        result=self.send('account',{'session_minutes':15,'password':'a long new password'})
        self.assertEqual(result.status_code,200)
        self.assertEqual(self.client.get('/api/bots').status_code,401)

    def test_power_is_explicit_and_demo_isolated(self):
        self.assertEqual(self.send('power',{'action':'reboot'}).status_code,400)
        with patch('features.time.sleep'),patch('app.run',side_effect=AssertionError('Real command')):
            job=self.finish(self.send('power',{'action':'reboot','confirm':'REBOOT'}))
        self.assertEqual(job['state'],'succeeded')

    def test_protected_features(self):
        client=self.app.test_client()
        for path in ('health','jobs','activity','alerts','schedules','versions','account','discovery','bots/notifier/backups'):
            self.assertEqual(client.get('/api/'+path).status_code,401,path)
        self.assertEqual(self.client.post('/api/schedules',json={}).status_code,403)
        self.assertTrue(self.client.get('/api/health').json['demo'])

    def test_restart_marks_incomplete_jobs_interrupted(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'state.json';store=Store(path)
            store.data['jobs']=[dict(id='job',state='running')];store.save()
            self.assertEqual(Store(path).snapshot('jobs')[0]['state'],'interrupted')

    def test_failed_job_is_reported_and_queue_keeps_working(self):
        store=Store();jobs=Jobs(store)
        def fail():raise ValueError('Expected failure')
        jobs.submit('fail',fail);jobs.submit('success',lambda:{'ok':True});jobs.queue.join()
        states={j['label']:j['state'] for j in store.snapshot('jobs')}
        self.assertEqual(states,{'fail':'failed','success':'succeeded'})

    def test_real_config_change_and_session_revocation_persist(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'config.json'
            config={'secret_key':'x'*40,'password_hash':generate_password_hash('old password'),'bots':[]}
            path.write_text(json.dumps(config));app=create_app(config,config_path=str(path));client=app.test_client()
            token=client.get('/api/session').json['csrf']
            login=client.post('/api/login',json={'username':'admin','password':'old password'},headers={'X-CSRF-Token':token})
            response=client.post('/api/account',json={'current_password':'old password','password':'a new secure password','session_minutes':30},headers={'X-CSRF-Token':login.json['csrf']})
            self.assertEqual(response.status_code,200)
            saved=json.loads(path.read_text());self.assertEqual(saved['session_generation'],1)
            self.assertEqual(saved['session_minutes'],30)
            self.assertTrue((Path(tmp)/'management-state.json').is_file())

if __name__=='__main__':unittest.main()
