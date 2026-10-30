"""Check the independent API boundary with no platform or paid-service calls."""
from __future__ import annotations

import importlib
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from copy_studio.transcript_extractor.service import TaskState


class StandaloneAPITests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.environment = patch.dict(os.environ, {
            'COPY_STUDIO_ROOT': str(self.root),
            'CDK_ADMIN_USERNAME': 'test-admin',
            'CDK_ADMIN_PASSWORD': 'test-admin-password',
            'COOKIE_SECURE': 'false',
        })
        self.environment.start()
        sys.modules.pop('copy_studio.api', None)
        self.api = importlib.import_module('copy_studio.api')
        self.client = TestClient(self.api.app)

    def tearDown(self):
        self.client.close()
        self.api.transcript_tasks.shutdown(wait=False)
        self.api.history_store.stop_cleanup_scheduler()
        sys.modules.pop('copy_studio.api', None)
        self.environment.stop()
        self.temporary.cleanup()

    def login_admin(self):
        response = self.client.post('/api/commercial/admin/login', json={
            'username': 'test-admin', 'password': 'test-admin-password',
        })
        self.assertEqual(response.status_code, 200)

    def activate_code(self, client=None):
        cdk = self.api.commercial_store.create_cdks(
            duration_limit_minutes=5, total_uses=1, quantity=1,
        )[0]
        response = (client or self.client).post('/api/commercial/access', json={'code': cdk['code']})
        self.assertEqual(response.status_code, 200)
        return cdk

    def test_independent_health_schema_and_storage(self):
        self.assertEqual(self.client.get('/api/health').json(), {
            'status': 'ok', 'service': 'copy-studio',
        })
        schema = self.client.get('/openapi.json').json()
        self.assertEqual(schema['info']['title'], 'Copy Studio')
        for path in schema['paths']:
            self.assertTrue(path == '/api/health' or path.startswith((
                '/api/commercial/', '/api/transcript-extractor/',
            )), path)
        self.assertEqual(self.client.get('/api/model-config').status_code, 404)
        self.assertEqual(self.client.get('/api/projects').status_code, 404)
        self.assertEqual(self.client.post('/api/lab/generate', json={}).status_code, 404)
        self.assertEqual(self.client.post('/api/remake/generate', json={}).status_code, 404)
        self.assertFalse((self.root / 'data' / 'studio.db').exists())
        self.assertEqual(list((self.root / 'data').glob('*.db')), [self.root / 'data/commercial.db'])

    def test_user_and_admin_routes_require_separate_authentication(self):
        self.assertEqual(self.client.get('/api/commercial/admin/cdks').status_code, 401)
        self.assertEqual(self.client.get('/api/transcript-extractor/health').status_code, 401)
        self.activate_code()
        self.assertEqual(self.client.get('/api/transcript-extractor/health').status_code, 200)
        self.assertEqual(self.client.get('/api/commercial/admin/cdks').status_code, 401)
        self.login_admin()
        self.assertEqual(self.client.get('/api/commercial/admin/cdks').status_code, 200)

    def test_admin_engine_switch_and_history(self):
        self.login_admin()
        response = self.client.put('/api/commercial/admin/settings', json={'asr_provider': 'dashscope'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['settings']['asr_provider'], 'dashscope')
        cdk = self.activate_code()
        self.api.history_store.save(cdk['id'], video_url='https://example.test/video', final_text='整理后的文案')
        records = self.client.get(f"/api/commercial/admin/cdks/{cdk['id']}/history")
        self.assertEqual(records.status_code, 200)
        self.assertEqual(records.json()['records'][0]['final_text'], '整理后的文案')

    def test_duration_rejection_preserves_quota(self):
        cdk = self.activate_code()
        with patch.object(self.api.transcript_tasks, 'prepare_video', return_value={'duration': 301}):
            response = self.client.post('/api/transcript-extractor/tasks', json={
                'share_text': 'https://www.douyin.com/video/123',
            })
        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.api.commercial_store.get_cdk(cdk['id'])['remaining_uses'], 1)

    def test_last_quota_task_can_be_polled_only_by_its_owner(self):
        cdk = self.activate_code()

        def create_task(_video_info, **kwargs):
            task = TaskState(id='tx_fixture', platform='douyin', owner_id=kwargs['owner_id'])
            self.api.transcript_tasks._tasks[task.id] = task
            return task.public()

        with (
            patch.object(self.api.transcript_tasks, 'prepare_video', return_value={'duration': 60}),
            patch.object(self.api.transcript_tasks, 'create_prepared', side_effect=create_task),
        ):
            response = self.client.post('/api/transcript-extractor/tasks', json={
                'share_text': 'https://www.douyin.com/video/123',
            })
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()['quota']['remaining_uses'], 0)
            self.assertEqual(self.client.get('/api/transcript-extractor/tasks/tx_fixture').status_code, 200)
            retry = self.client.post('/api/transcript-extractor/tasks', json={
                'share_text': 'https://www.douyin.com/video/123',
            })
            self.assertEqual(retry.status_code, 403)
        self.assertTrue(self.client.get('/api/commercial/access').json()['authorized'])
        with TestClient(self.api.app) as other:
            self.activate_code(other)
            self.assertEqual(other.get('/api/transcript-extractor/tasks/tx_fixture').status_code, 404)
            self.assertFalse(other.delete('/api/transcript-extractor/tasks/tx_fixture').json()['deleted'])
        self.assertEqual(self.api.commercial_store.get_cdk(cdk['id'])['remaining_uses'], 0)

    def test_built_pages_and_assets_are_served_and_missing_build_is_clear(self):
        self.assertEqual(self.client.get('/copy-studio').status_code, 503)
        dist = self.root / 'frontend/dist'
        (dist / 'assets').mkdir(parents=True)
        (dist / 'assets/test.css').write_text('body { color: black; }')
        for filename in ('transcript.html', 'transcript-admin.html', 'transcript-records.html'):
            (dist / filename).write_text('<!doctype html><title>Copy Studio</title>')
        for path in ('/', '/copy-studio', '/copy-studio/', '/copy-studio/records', '/copy-studio/ft'):
            self.assertEqual(self.client.get(path).status_code, 200, path)
        self.assertEqual(self.client.get('/assets/test.css').status_code, 200)


if __name__ == '__main__':
    unittest.main()
