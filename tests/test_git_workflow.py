import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch
from werkzeug.security import generate_password_hash
from app import create_app


@unittest.skipUnless(shutil.which('git'),'Git is not installed')
class GitWorkflowTests(unittest.TestCase):
    def test_real_local_update_rollback_resume_and_dirty_protection(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);origin=root/'origin';checkout=root/'checkout'
            def command(args,timeout=25,user=None):
                result=subprocess.run(args,capture_output=True,text=True,timeout=timeout)
                if result.returncode:raise ValueError('Git command failed')
                return result.stdout.strip()
            command(['git','init','-b','main',str(origin)])
            command(['git','-C',str(origin),'config','user.email','test@example.invalid'])
            command(['git','-C',str(origin),'config','user.name','Pi Deck test'])
            (origin/'bot.py').write_text('version=1\n')
            command(['git','-C',str(origin),'add','bot.py']);command(['git','-C',str(origin),'commit','-m','first'])
            first=command(['git','-C',str(origin),'rev-parse','HEAD'])
            command(['git','clone',str(origin),str(checkout)])
            (origin/'bot.py').write_text('version=2\n')
            command(['git','-C',str(origin),'commit','-am','second'])
            bot=dict(id='test',name='Test',description='',service='test.service',user='pi',repo=str(checkout),env=str(checkout/'.env'))
            config={'secret_key':'x'*40,'password_hash':generate_password_hash('password'),'bots':[bot]}
            with patch('app.run',side_effect=command):
                app=create_app(config)
                version=app.extensions['deck_version']
                self.assertEqual(version('test','check')['behind'],1)
                self.assertEqual(version('test','pull')['behind'],0)
                self.assertEqual((checkout/'bot.py').read_text(),'version=2\n')
                version('test','pull')  # An up-to-date pull must preserve the last rollback point.
                result=version('test','rollback')
                self.assertTrue(result['detached'])
                self.assertEqual(command(['git','-C',str(checkout),'rev-parse','HEAD']),first)
                self.assertEqual((checkout/'bot.py').read_text(),'version=1\n')
                self.assertFalse(version('test','resume')['detached'])
                self.assertEqual((checkout/'bot.py').read_text(),'version=2\n')
                (checkout/'bot.py').write_text('local changes\n')
                with self.assertRaisesRegex(ValueError,'local changes'):version('test','rollback')
                self.assertEqual((checkout/'bot.py').read_text(),'local changes\n')

if __name__=='__main__':unittest.main()
