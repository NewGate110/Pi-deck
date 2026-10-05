import subprocess
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from git_review import review, force_update
from app import create_app


@unittest.skipUnless(shutil.which('git'), 'Git is not installed')
class GitReviewTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.origin=self.root/'origin';self.repo=self.root/'checkout'
        self.command('init','-b','main',str(self.origin))
        self.origin_git('config','user.name','Test');self.origin_git('config','user.email','test@example.invalid')
        (self.origin/'main.py').write_text('value = 1\n')
        (self.origin/'.gitignore').write_text('.env\n.venv/\n')
        self.origin_git('add','.');self.origin_git('commit','-m','Initial')
        self.command('clone',str(self.origin),str(self.repo))
        self.bot=dict(id='test',repo=str(self.repo),env=str(self.repo/'.env'),user='pi',service='test.service')
        (self.origin/'main.py').write_text('value = 2\n')
        self.origin_git('commit','-am','Incoming update')

    def command(self,*args):
        result=subprocess.run(['git',*args],capture_output=True,text=True)
        if result.returncode:raise ValueError('Git failed: '+result.stderr)
        return result.stdout.strip()

    def origin_git(self,*args):return self.command('-C',str(self.origin),*args)
    def git(self,bot,*args):return self.command('-C',bot['repo'],*args)

    def test_force_update_preserves_recovery_and_untracked_files(self):
        (self.repo/'main.py').write_text('value = 3\n');self.git(self.bot,'add','main.py')
        (self.repo/'main.py').write_text('value = 4\n')
        (self.repo/'.env').write_text('TOKEN=private\n')
        (self.repo/'notes.txt').write_text('keep me\n')
        preview=review(self.bot,self.git)
        self.assertTrue(preview['can_force'],preview['blockers'])
        self.assertIn('+value = 3',preview['sections'][0]['patch'])
        self.assertIn('+value = 4',preview['sections'][1]['patch'])
        saved=[]
        result=force_update(self.bot,self.git,preview['revision'],saved.append)
        self.assertEqual((self.repo/'main.py').read_text(),'value = 2\n')
        self.assertEqual((self.repo/'.env').read_text(),'TOKEN=private\n')
        self.assertEqual((self.repo/'notes.txt').read_text(),'keep me\n')
        recovery=saved[0]
        self.assertEqual(result['recovery'],recovery)
        self.assertEqual(self.git(self.bot,'show',recovery['ref']+':main.py'),'value = 4')
        self.assertEqual(self.git(self.bot,'show',recovery['ref']+'^2:main.py'),'value = 3')

    def test_preview_rejects_later_edits_and_remote_changes(self):
        preview=review(self.bot,self.git)
        (self.repo/'main.py').write_text('new local edit\n')
        with self.assertRaisesRegex(ValueError,'changed since'):
            force_update(self.bot,self.git,preview['revision'],lambda r:None)
        self.assertEqual((self.repo/'main.py').read_text(),'new local edit\n')
        preview=review(self.bot,self.git)
        (self.origin/'main.py').write_text('value = 5\n');self.origin_git('commit','-am','Another update')
        with self.assertRaisesRegex(ValueError,'changed since'):
            force_update(self.bot,self.git,preview['revision'],lambda r:None)

    def test_ignored_and_untracked_collisions_block_force(self):
        (self.origin/'notes.txt').write_text('remote notes\n');self.origin_git('add','notes.txt')
        (self.origin/'.venv').mkdir();(self.origin/'.venv'/'data').write_text('remote data')
        self.origin_git('add','-f','.venv/data');self.origin_git('commit','-m','Conflicting paths')
        (self.repo/'notes.txt').write_text('local notes')
        (self.repo/'.venv').mkdir();(self.repo/'.venv'/'data').write_text('local data')
        preview=review(self.bot,self.git)
        self.assertFalse(preview['can_force'])
        with self.assertRaisesRegex(ValueError,'blocked'):
            force_update(self.bot,self.git,preview['revision'],lambda r:None)
        self.assertEqual((self.repo/'.venv'/'data').read_text(),'local data')

    def test_failed_recovery_persistence_leaves_checkout_untouched(self):
        (self.repo/'main.py').write_text('local edit\n')
        preview=review(self.bot,self.git)
        def fail(recovery):raise OSError('disk full')
        with self.assertRaises(OSError):force_update(self.bot,self.git,preview['revision'],fail)
        self.assertEqual((self.repo/'main.py').read_text(),'local edit\n')
        self.assertEqual(self.git(self.bot,'rev-parse','HEAD'),preview['head'])

    def test_local_commits_have_a_recovery_ref(self):
        self.git(self.bot,'config','user.name','Test');self.git(self.bot,'config','user.email','test@example.invalid')
        (self.repo/'local.py').write_text('local = True\n');self.git(self.bot,'add','local.py');self.git(self.bot,'commit','-m','Local commit')
        preview=review(self.bot,self.git)
        self.assertEqual(preview['ahead'],1)
        saved=[];force_update(self.bot,self.git,preview['revision'],saved.append)
        self.assertEqual(self.git(self.bot,'rev-parse',saved[0]['ref']),preview['head'])
        self.assertFalse((self.repo/'local.py').exists())

    def test_tracked_env_and_binary_changes_are_blocked(self):
        (self.origin/'.env').write_text('TOKEN=secret');self.origin_git('add','-f','.env')
        (self.origin/'binary.dat').write_bytes(b'\x00\x01\x02');self.origin_git('add','binary.dat')
        self.origin_git('commit','-m','Protected changes')
        preview=review(self.bot,self.git)
        self.assertFalse(preview['can_force'])
        self.assertTrue(any('environment' in b for b in preview['blockers']))
        self.assertTrue(any('Binary' in b for b in preview['blockers']))

    def test_hidden_local_edits_block_force(self):
        self.git(self.bot,'update-index','--assume-unchanged','main.py')
        (self.repo/'main.py').write_text('hidden local edit\n')
        preview=review(self.bot,self.git)
        self.assertFalse(preview['can_force'])
        self.assertTrue(any('assume-unchanged' in b for b in preview['blockers']))


class GitReviewApiTests(unittest.TestCase):
    def test_demo_preview_confirmation_and_isolation(self):
        app=create_app(demo=True);client=app.test_client()
        self.assertEqual(client.post('/api/bots/notifier/git-preview',json={}).status_code,403)
        token=client.get('/api/session').json['csrf']
        self.assertEqual(client.post('/api/bots/notifier/git-preview',json={},headers={'X-CSRF-Token':token}).status_code,401)
        token=client.post('/api/login',json={},headers={'X-CSRF-Token':token}).json['csrf']
        headers={'X-CSRF-Token':token}
        with patch('features.review_git',side_effect=AssertionError('real Git')),patch('features.force_update',side_effect=AssertionError('real force')):
            preview=client.post('/api/bots/notifier/git-preview',json={},headers=headers).json
            self.assertTrue(preview['demo'])
            self.assertEqual(client.post('/api/bots/notifier/force-update',json={'revision':preview['revision']},headers=headers).status_code,400)
            response=client.post('/api/bots/notifier/force-update',json={'revision':preview['revision'],'confirm':'notifier'},headers=headers)
            self.assertEqual(response.status_code,202)
            app.extensions['deck_jobs'].queue.join()
            self.assertEqual(client.get('/api/jobs').json['jobs'][0]['state'],'succeeded')
