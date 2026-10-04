import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from werkzeug.security import generate_password_hash
from app import create_app
from onboarding import validate, unit_text


class OnboardingTests(unittest.TestCase):
    def setUp(self):
        self.data = dict(mode='github', id='new-bot', name='New bot', description='Test bot',
                         user='pi', repo='/home/pi/new-bot', env='/home/pi/new-bot/.env',
                         service='new-bot.service', url='https://github.com/example/bot',
                         command='/usr/bin/python3 main.py', environment='TOKEN=example\n')

    def client(self, app, password=''):
        client=app.test_client()
        token=client.get('/api/session').json['csrf']
        login=client.post('/api/login',json={'username':'admin','password':password},headers={'X-CSRF-Token':token})
        return client, {'X-CSRF-Token':login.json['csrf']}

    def test_demo_creation_and_duplicate(self):
        client, headers=self.client(create_app(demo=True))
        with patch('app.provision',side_effect=AssertionError('real provisioning')):
            result=client.post('/api/bots',json=self.data,headers=headers)
        self.assertEqual(result.status_code,201)
        self.assertEqual(result.json['bot']['state'],'inactive')
        self.assertEqual(client.get('/api/bots/new-bot/files/env').json['content'],'TOKEN=example\n')
        self.assertEqual(client.post('/api/bots',json=self.data,headers=headers).status_code,409)

    def test_manual_demo(self):
        client,headers=self.client(create_app(demo=True))
        self.data['mode']='manual'
        for key in ('url','command','environment'):
            self.data.pop(key)
        self.assertEqual(client.post('/api/bots',json=self.data,headers=headers).status_code,201)

    def test_unauthenticated_and_csrf(self):
        app=create_app(demo=True);client=app.test_client()
        token=client.get('/api/session').json['csrf']
        self.assertEqual(client.post('/api/bots',json=self.data,headers={'X-CSRF-Token':token}).status_code,401)
        client,headers=self.client(app)
        self.assertEqual(client.post('/api/bots',json=self.data).status_code,403)

    def test_validation(self):
        for key,value in [('user','root'),('service','../bad.service'),('id','--bad'),('repo','/home/pi/../root'),('url','https://evil.example/repo'),('url','https://token@github.com/owner/repo'),('command','/usr/bin/python3\nUser=root'),('command','/bin/echo %u'),('env','/etc/shadow')]:
            with self.subTest(key=key,value=value),self.assertRaises(ValueError):
                validate({**self.data,key:value})
        text=unit_text(validate(self.data),self.data['command'])
        self.assertIn('User=pi\n',text)
        self.assertIn('ExecStart=/usr/bin/python3 main.py\n',text)

    def test_persistence_restart_and_disk_conflict(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'config.json'
            config={'secret_key':'x'*40,'password_hash':generate_password_hash('password'),'bots':[]}
            path.write_text(json.dumps(config))
            client,headers=self.client(create_app(config,config_path=str(path)),'password')
            def fake_provision(bot,data,run,persist):persist(bot)
            with patch('app.provision',side_effect=fake_provision):
                self.assertEqual(client.post('/api/bots',json=self.data,headers=headers).status_code,201)
            saved=json.loads(path.read_text())
            self.assertEqual(saved['bots'][0]['id'],'new-bot')
            self.assertNotIn('environment',saved['bots'][0])
            self.assertEqual(saved['secret_key'],config['secret_key'])
            # Use Linux path validation on the Windows test host.
            with patch('app.Path.is_absolute',return_value=True):
                restarted=create_app(saved,config_path=str(path))
            client2,headers2=self.client(restarted,'password')
            with patch('app.run',return_value='ActiveState=inactive'):
                self.assertEqual(client2.get('/api/bots').json['bots'][0]['id'],'new-bot')
            path.write_text(path.read_text()+'\n')
            second={**self.data,'id':'second','service':'second.service','repo':'/home/pi/second','env':'/home/pi/second/.env'}
            with patch('app.provision',side_effect=fake_provision):
                response=client2.post('/api/bots',json=second,headers=headers2)
            self.assertEqual(response.status_code,400)
            self.assertIn('changed on disk',response.json['error'])
            self.assertEqual(len(json.loads(path.read_text())['bots']),1)

    def test_failed_provision_does_not_register(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'config.json'; config={'secret_key':'x'*40,'password_hash':generate_password_hash('password'),'bots':[]}
            path.write_text(json.dumps(config))
            client,headers=self.client(create_app(config,config_path=str(path)),'password')
            with patch('app.provision',side_effect=ValueError('Clone failed')):
                self.assertEqual(client.post('/api/bots',json=self.data,headers=headers).status_code,400)
            self.assertEqual(json.loads(path.read_text())['bots'],[])

if __name__=='__main__':unittest.main()
