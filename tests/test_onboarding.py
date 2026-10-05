import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch, Mock
from werkzeug.security import generate_password_hash
from app import create_app
from onboarding import validate, unit_text, service_defaults


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

    def test_optional_environment_and_icons_across_bot_lifecycle(self):
        app=create_app(demo=True);client,headers=self.client(app)
        data={**self.data,'mode':'manual','env':'','icon':'weather'}
        response=client.post('/api/bots',json=data,headers=headers)
        self.assertEqual(response.status_code,201)
        self.assertEqual(response.json['bot']['icon'],'weather')
        self.assertEqual(client.get('/api/bots/new-bot/files/env').status_code,400)
        self.assertNotIn('EnvironmentFile=',client.get('/api/bots/new-bot/files/service').json['content'])
        response=client.post('/api/bots/new-bot/maintenance',json={'action':'backup'},headers=headers)
        app.extensions['deck_jobs'].queue.join()
        self.assertEqual(client.get('/api/jobs').json['jobs'][0]['state'],'succeeded')
        self.assertEqual([b['kind'] for b in client.get('/api/bots/new-bot/backups').json['backups']],['service'])
        settings=client.get('/api/bots/new-bot/settings').json
        response=client.put('/api/bots/new-bot/settings',json={**settings['bot'],'icon':'music','revision':settings['revision']},headers=headers)
        self.assertEqual(response.status_code,200)
        self.assertEqual(client.get('/api/bots/new-bot/settings').json['bot']['icon'],'music')
        response=client.post('/api/bots/new-bot/remove',json={'confirm':'new-bot','uninstall':True},headers=headers)
        app.extensions['deck_jobs'].queue.join()
        self.assertEqual(client.get('/api/jobs').json['jobs'][0]['state'],'succeeded')

    def test_github_without_environment_and_invalid_icons(self):
        data={**self.data,'env':'','environment':'','icon':'download'}
        self.assertNotIn('EnvironmentFile=',unit_text(validate(data),data['command']))
        with self.assertRaises(ValueError):validate({**data,'environment':'TOKEN=secret'})
        for icon in ('unknown','<script>',[],None):
            with self.subTest(icon=icon),self.assertRaises(ValueError):validate({**data,'icon':icon})

    def test_real_manual_provision_does_not_require_environment_file(self):
        from types import SimpleNamespace
        from onboarding import provision
        with tempfile.TemporaryDirectory() as temp:
            bot={**self.data,'repo':temp,'env':''}
            runner=Mock(side_effect=['repository','loaded']);persist=Mock()
            fake_pwd=SimpleNamespace(getpwnam=lambda user:SimpleNamespace(pw_uid=1000))
            with patch.dict('sys.modules',{'pwd':fake_pwd}):
                provision(bot,{'mode':'manual'},runner,persist)
            persist.assert_called_once_with(bot)

    def test_no_environment_and_icon_survive_config_reload(self):
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp)/'config.json'
            bot={**validate({**self.data,'mode':'manual','env':'','icon':'shield'}),'repo':temp}
            config={'secret_key':'x'*40,'password_hash':generate_password_hash('password'),'bots':[bot]}
            path.write_text(json.dumps(config))
            client,_=self.client(create_app(config,config_path=str(path)),'password')
            settings=client.get('/api/bots/new-bot/settings').json['bot']
            self.assertEqual((settings['env'],settings['icon']),('','shield'))
            del bot['env'];del bot['icon']
            client,_=self.client(create_app(config),'password')
            settings=client.get('/api/bots/new-bot/settings').json['bot']
            self.assertEqual((settings['env'],settings['icon']),('','bot'))

    def test_service_defaults_reads_only_effective_properties(self):
        runner=Mock(return_value='LoadState=loaded\nDescription=Weather bot\nUser=weather\nWorkingDirectory=/home/weather/bot\nEnvironmentFiles=/home/weather/bot/.env (ignore_errors=yes)\n')
        data=service_defaults('weather-bot.service',runner)
        self.assertEqual((data['name'],data['user'],data['repo'],data['env']),
                         ('Weather bot','weather','/home/weather/bot','/home/weather/bot/.env'))
        self.assertEqual(data['warnings'],[])
        runner.assert_called_once_with(['systemctl','show','weather-bot.service',
            '--property=LoadState,Description,User,WorkingDirectory,EnvironmentFiles'])

    def test_service_defaults_requires_choice_for_multiple_environment_files(self):
        runner=Mock(return_value='LoadState=loaded\nUser=pi\nWorkingDirectory=/home/pi/bot\nEnvironmentFiles=/etc/bot.env (ignore_errors=no) /home/pi/bot/.env (ignore_errors=yes)\n')
        data=service_defaults('bot.service',runner)
        self.assertEqual(data['env'],'')
        self.assertEqual(data['env_files'],['/etc/bot.env','/home/pi/bot/.env'])
        self.assertIn('multiple',data['warnings'][0])

    def test_service_defaults_handles_missing_and_unsupported_settings(self):
        for properties in ('', 'User=root\nWorkingDirectory=~\nEnvironmentFiles=/home/pi/a\\x20b/.env (ignore_errors=no)'):
            with self.subTest(properties=properties):
                data=service_defaults('bot.service',Mock(return_value='LoadState=loaded\n'+properties))
                self.assertEqual((data['user'],data['repo'],data['env']),('','',''))
                self.assertGreaterEqual(len(data['warnings']),3)
        runner=Mock()
        for name in ('--bad.service','../bad.service','bot.service\nOther'):
            with self.assertRaises(ValueError):service_defaults(name,runner)
        runner.assert_not_called()
        with self.assertRaises(ValueError):
            service_defaults('missing.service',Mock(return_value='LoadState=not-found'))

    def test_discovery_prefill_is_authenticated_and_demo_isolated(self):
        app=create_app(demo=True)
        self.assertEqual(app.test_client().get('/api/discovery/weather-bot.service').status_code,401)
        client,_=self.client(app)
        with patch('features.service_defaults',side_effect=AssertionError('Real system lookup')):
            response=client.get('/api/discovery/weather-bot.service')
        self.assertEqual(response.status_code,200)
        self.assertEqual(response.json['env'],'/home/pi/bots/weather-bot/.env')
        self.assertEqual(len(client.get('/api/bots').json['bots']),3)

    def test_real_discovery_endpoint_uses_service_defaults(self):
        config={'secret_key':'x'*40,'password_hash':generate_password_hash('password'),'bots':[]}
        client,_=self.client(create_app(config),'password')
        with patch('features.service_defaults',return_value={'name':'Detected'}) as detect:
            response=client.get('/api/discovery/bot.service')
        self.assertEqual(response.json,{'name':'Detected'})
        self.assertEqual(detect.call_args.args[0],'bot.service')

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
