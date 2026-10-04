import copy
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from werkzeug.security import generate_password_hash
from app import create_app, DEMO_BOTS, atomic_write


class DashboardTests(unittest.TestCase):
    def setUp(self):
        self.app = create_app(demo=True)
        self.client = self.app.test_client()
        self.token = self.client.get('/api/session').json['csrf']

    def login(self):
        result = self.client.post('/api/login', json={}, headers={'X-CSRF-Token': self.token})
        self.token = result.json['csrf']

    def post(self, path, data):
        return self.client.post(path, json=data, headers={'X-CSRF-Token': self.token})

    def test_authentication_and_csrf_required(self):
        self.assertEqual(self.client.get('/api/bots').status_code, 401)
        self.assertEqual(self.client.post('/api/login', json={}).status_code, 403)
        self.login()
        self.assertEqual(self.client.get('/api/bots').status_code, 200)
        self.assertEqual(self.client.post('/api/bots/notifier/action', json={'action':'stop'}).status_code, 403)

    def test_running_and_startup_are_independent(self):
        self.login()
        with patch('app.run', side_effect=AssertionError('Demo invoked real command')):
            self.assertEqual(self.post('/api/bots/notifier/action', {'action':'disable'}).status_code, 200)
            bot = self.client.get('/api/bots').json['bots'][0]
            self.assertEqual(bot['state'], 'active')
            self.assertFalse(bot['enabled'])
            self.post('/api/bots/notifier/action', {'action':'stop'})
            self.assertEqual(self.client.get('/api/bots').json['bots'][0]['state'], 'inactive')

    def test_file_round_trip_and_conflict(self):
        self.login()
        url='/api/bots/notifier/files/env'
        file=self.client.get(url).json
        content='# comment\nTOKEN="a=b # c"\n'
        args=dict(json={'content':content,'revision':file['revision']},headers={'X-CSRF-Token':self.token})
        self.assertEqual(self.client.put(url, **args).status_code, 200)
        self.assertEqual(self.client.get(url).json['content'], content)
        self.assertEqual(self.client.put(url, **args).status_code, 409)

    def test_allowlist_and_logout(self):
        self.login()
        self.assertEqual(self.post('/api/bots/notifier/action', {'action':'reboot'}).status_code, 400)
        self.assertEqual(self.post('/api/bots/unknown/action', {'action':'stop'}).status_code, 404)
        self.assertEqual(self.client.get('/api/bots/notifier/files/passwords').status_code, 400)
        self.post('/api/logout', {})
        self.assertEqual(self.client.get('/api/bots/notifier/files/env').status_code, 401)

    def test_headers_and_no_secret_in_cookie(self):
        self.login()
        response=self.client.get('/api/bots/notifier/files/env')
        self.assertEqual(response.headers['Cache-Control'], 'no-store')
        self.assertIn("frame-ancestors 'none'", response.headers['Content-Security-Policy'])
        self.assertNotIn('demo-token', str(response.headers))

    def test_real_login_rate_limit_and_password(self):
        app=create_app({'secret_key':'x'*40,'password_hash':generate_password_hash('correct password'), 'bots':[]})
        client=app.test_client()
        token=client.get('/api/session').json['csrf']
        headers={'X-CSRF-Token':token}
        for _ in range(10):
            self.assertEqual(client.post('/api/login',json={'username':'admin','password':'wrong'},headers=headers).status_code,401)
        self.assertEqual(client.post('/api/login',json={'username':'admin','password':'correct password'},headers=headers).status_code,429)
        client2=app.test_client()
        token2=client2.get('/api/session').json['csrf']
        self.assertEqual(client2.post('/api/login',json={'password':'wrong'},headers={'X-CSRF-Token':token2}).status_code,429)

    def test_atomic_write(self):
        with tempfile.TemporaryDirectory() as tmp:
            file=Path(tmp)/'.env'
            file.write_text('original')
            atomic_write(file,'changed\n')
            self.assertEqual(file.read_text(),'changed\n')
            self.assertEqual(len(list(Path(tmp).iterdir())),1)

    def test_real_pull_uses_owner_and_rejects_dirty_checkout(self):
        with tempfile.TemporaryDirectory() as tmp:
            bot=copy.deepcopy(DEMO_BOTS[0]); bot.update(repo=tmp,env=str(Path(tmp)/'.env'))
            app=create_app({'secret_key':'x'*40,'password_hash':generate_password_hash('correct password'),'bots':[bot]})
            client=app.test_client(); token=client.get('/api/session').json['csrf']
            result=client.post('/api/login',json={'username':'admin','password':'correct password'},headers={'X-CSRF-Token':token})
            self.assertEqual(result.status_code,200)
            headers={'X-CSRF-Token':result.json['csrf']}
            with patch('app.run',return_value=' M main.py') as command:
                result=client.post('/api/bots/notifier/action',json={'action':'pull'},headers=headers)
                self.assertEqual(result.status_code,400)
                self.assertIn('local changes',result.json['error'])
                command.assert_called_once_with(['git','-C',tmp,'status','--porcelain'],timeout=90,user='pi')
            with patch('app.run',return_value='') as command:
                result=client.post('/api/bots/notifier/action',json={'action':'pull'},headers=headers)
                self.assertEqual(result.status_code,200)
                self.assertEqual(command.call_args.args[0],['git','-C',tmp,'pull','--ff-only'])

if __name__ == '__main__':
    unittest.main()
