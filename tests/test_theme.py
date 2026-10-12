"""Integration contracts for the main dashboard theme and icon registry."""
import re
import unittest

from app import create_app
from onboarding import BOT_ICONS


class ThemeTests(unittest.TestCase):
    def test_main_template_has_all_registered_icons_and_local_assets(self):
        client = create_app(demo=True).test_client()
        html = client.get('/').get_data(as_text=True)
        self.assertEqual(set(re.findall(r'data-bot-icon="([^"]+)"', html)), set(BOT_ICONS))
        self.assertEqual(set(re.findall(r'name="icon" value="([^"]+)"', html)), set(BOT_ICONS))
        self.assertNotIn('/xp.css', html)
        self.assertNotIn('xp-preview-', html)
        for asset in re.findall(r'(?:src|href)="(/static/[^\"]+)"', html):
            with client.get(asset) as response:
                self.assertEqual(response.status_code, 200, asset)

    def test_classic_icons_save_through_main_settings_api(self):
        client = create_app(demo=True).test_client()
        csrf = client.get('/api/session').json['csrf']
        csrf = client.post('/api/login', json={'username': 'admin'}, headers={'X-CSRF-Token': csrf}).json['csrf']
        for icon in ('computer', 'documents', 'folder', 'network', 'recycle', 'browser',
                     'mail', 'drive', 'paint', 'terminal', 'printer', 'control'):
            with self.subTest(icon=icon):
                state = client.get('/api/bots/notifier/settings').json
                result = client.put('/api/bots/notifier/settings',
                    json={**state['bot'], 'revision': state['revision'], 'icon': icon},
                    headers={'X-CSRF-Token': csrf})
                self.assertEqual(result.status_code, 200, result.json)
                self.assertEqual(client.get('/api/bots/notifier/settings').json['bot']['icon'], icon)
