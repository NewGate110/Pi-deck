import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import threading
import unittest
from unittest.mock import patch
from werkzeug.security import generate_password_hash

from app import create_app


def login(app):
    client=app.test_client();token=client.get('/api/session').json['csrf']
    response=client.post('/api/login',json={'username':'admin','password':'password'},headers={'X-CSRF-Token':token})
    return client,{'X-CSRF-Token':response.json['csrf']}


class SelfUpdateDemoTests(unittest.TestCase):
    def test_demo_actions_never_touch_host(self):
        with patch('app.run',side_effect=AssertionError('Host command')):
            app=create_app(demo=True);client,headers=login(app)
            self.assertEqual(app.test_client().get('/api/system/update').status_code,401)
            self.assertEqual(client.post('/api/system/update',json={'action':'pull'}).status_code,403)
            for action in ('check','pull','dependencies','rollback','resume'):
                response=client.post('/api/system/update',json={'action':action},headers=headers)
                self.assertEqual(response.status_code,202)
                app.extensions['deck_jobs'].queue.join()
                self.assertEqual(client.get('/api/jobs').json['jobs'][0]['state'],'succeeded')
            preview=client.post('/api/system/git-preview',json={},headers=headers).json
            self.assertEqual(client.post('/api/system/update',json={'action':'force','revision':preview['revision']},headers=headers).status_code,400)
            response=client.post('/api/system/update',json={'action':'force','revision':preview['revision'],'confirm':'pi-deck'},headers=headers)
            self.assertEqual(response.status_code,202);app.extensions['deck_jobs'].queue.join()
            self.assertEqual(client.post('/api/system/restart',json={'confirm':'pi-deck'},headers=headers).status_code,200)
            self.assertTrue(app.extensions['deck_jobs'].accepting)

    def test_restart_waits_for_jobs(self):
        app=create_app(demo=True);client,headers=login(app)
        release=threading.Event();started=threading.Event()
        def work():started.set();release.wait(5)
        jobs=app.extensions['deck_jobs'];jobs.submit('Slow job',work)
        try:
            self.assertTrue(started.wait(2))
            response=client.post('/api/system/restart',json={'confirm':'pi-deck'},headers=headers)
            self.assertEqual(response.status_code,400)
            self.assertTrue(jobs.accepting)
        finally:release.set();jobs.queue.join()


@unittest.skipUnless(shutil.which('git'),'Git unavailable')
class SelfUpdateRealTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.base=Path(self.temp.name);self.origin=self.base/'origin';self.repo=self.base/'checkout'
        self.git('init','-b','main',str(self.origin))
        self.git('-C',str(self.origin),'config','user.email','test@example.invalid')
        self.git('-C',str(self.origin),'config','user.name','Test')
        (self.origin/'app.py').write_text('version = 1\n')
        (self.origin/'requirements.txt').write_text('# dependencies\n')
        self.git('-C',str(self.origin),'add','.');self.git('-C',str(self.origin),'commit','-m','Initial')
        self.git('clone',str(self.origin),str(self.repo))
        (self.origin/'app.py').write_text('version = 2\n')
        self.git('-C',str(self.origin),'commit','-am','Update')
        private=self.base/'private';private.mkdir();self.config_path=private/'config.json'
        self.config={'secret_key':'x'*40,'password_hash':generate_password_hash('password'),'bots':[]}
        self.config_path.write_text(json.dumps(self.config))
        self.commands=[];self.main_pid=str(os.getpid());self.restart_fail=False
        self.app=self.make_app();self.client,self.headers=login(self.app)

    def git(self,*args):
        result=subprocess.run(['git',*args],capture_output=True,text=True)
        if result.returncode:raise ValueError('Git command failed')
        return result.stdout.strip()

    def command_runner(self,args,timeout=25,user=None):
        self.commands.append(args)
        if args[0]=='git':return self.git(*args[1:])
        if args[0]=='systemctl':return self.main_pid
        if args[0]=='systemd-run':
            if self.restart_fail:raise ValueError('Scheduler failed')
            return 'Scheduled'
        raise AssertionError('Unexpected command: '+repr(args))

    def make_app(self):
        with patch('self_update.APP_ROOT',self.repo),patch('app.run',side_effect=self.command_runner):
            return create_app(self.config,config_path=str(self.config_path))

    def test_pull_private_backup_rollback_and_resume(self):
        perform=self.app.extensions['deck_self_update']
        self.assertEqual(perform('check',{})['behind'],1)
        result=perform('pull',{})
        self.assertEqual((self.repo/'app.py').read_text(),'version = 2\n')
        backup=Path(result['private_backup'])
        self.assertEqual(json.loads((backup/'config.json').read_text()),self.config)
        self.assertTrue((backup/'management-state.json').is_file())
        self.assertTrue(result['restart_required'])
        perform('rollback',{});self.assertEqual((self.repo/'app.py').read_text(),'version = 1\n')
        perform('resume',{});self.assertEqual((self.repo/'app.py').read_text(),'version = 2\n')
        self.assertFalse(any(args[0]=='systemctl' for args in self.commands))

    def test_force_reuses_preview_and_retains_local_code(self):
        (self.repo/'app.py').write_text('local edit\n')
        with self.assertRaisesRegex(ValueError,'local changes'):self.app.extensions['deck_self_update']('pull',{})
        preview=self.client.post('/api/system/git-preview',json={},headers=self.headers).json
        self.assertTrue(preview['can_force'],preview['blockers'])
        result=self.app.extensions['deck_self_update']('force',{'revision':preview['revision']})
        self.assertEqual((self.repo/'app.py').read_text(),'version = 2\n')
        self.assertEqual(self.git('-C',str(self.repo),'show',result['recovery']['ref']+':app.py'),'local edit')

    def test_private_config_in_checkout_blocks_mutation(self):
        self.config_path=self.repo/'config.json';self.config_path.write_text(json.dumps(self.config))
        app=self.make_app()
        with self.assertRaisesRegex(ValueError,'outside'):
            app.extensions['deck_self_update']('dependencies',{})

    def test_target_private_files_block_pull(self):
        (self.origin/'config.json').write_text('{}')
        self.git('-C',str(self.origin),'add','config.json');self.git('-C',str(self.origin),'commit','-m','Private file')
        with self.assertRaisesRegex(ValueError,'private configuration'):
            self.app.extensions['deck_self_update']('pull',{})
        self.assertEqual((self.repo/'app.py').read_text(),'version = 1\n')

    def test_restart_checks_service_identity_and_gates_new_jobs(self):
        self.main_pid='-1'
        response=self.client.post('/api/system/restart',json={'confirm':'pi-deck'},headers=self.headers)
        self.assertEqual(response.status_code,400)
        self.assertTrue(self.app.extensions['deck_jobs'].accepting)
        self.main_pid=str(os.getpid());self.restart_fail=True
        self.assertEqual(self.client.post('/api/system/restart',json={'confirm':'pi-deck'},headers=self.headers).status_code,400)
        self.assertTrue(self.app.extensions['deck_jobs'].accepting)
        self.restart_fail=False
        with patch('self_update.threading.Timer') as timer:
            self.assertEqual(self.client.post('/api/system/restart',json={'confirm':'pi-deck'},headers=self.headers).status_code,200)
            watchdog=timer.call_args.args[1]
        with self.assertRaisesRegex(ValueError,'restarting'):
            self.app.extensions['deck_jobs'].submit('Must wait',lambda:None)
        self.assertEqual(self.commands[-1][-2:],['restart','pi-deck.service'])
        watchdog()
        self.assertTrue(self.app.extensions['deck_jobs'].accepting)
