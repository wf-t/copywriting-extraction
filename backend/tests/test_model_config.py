from __future__ import annotations

import json
import os
import tempfile
import unittest
from unittest.mock import patch

from copy_studio.model_config import ModelConfigStore
from copy_studio.model_runtime import call_openai_compatible_chat, resolve_model


class CopyStudioModelTests(unittest.TestCase):
    def test_fun_asr_sdk_has_file_transcription_interface(self):
        from dashscope.audio.asr import Transcription

        self.assertTrue(callable(Transcription.async_call))
        self.assertTrue(callable(Transcription.wait))

    def test_environment_overrides_file_without_persisting_keys(self):
        with tempfile.TemporaryDirectory() as root:
            store = ModelConfigStore(root)
            store.save({'api_providers': {'deepseek': {'api_key': 'file-key'}}})
            with patch.dict(os.environ, {
                'DEEPSEEK_API_KEY': 'environment-key',
                'DEEPSEEK_BASE_URL': 'https://gateway.test/v1',
                'DEEPSEEK_MODEL': 'deepseek-reasoner',
                'DASHSCOPE_API_KEY': 'cloud-key',
            }):
                config = store.load()
                resolved = resolve_model(config)
            self.assertEqual(resolved.model, 'deepseek-reasoner')
            self.assertEqual(resolved.provider_config['api_key'], 'environment-key')
            self.assertEqual(resolved.provider_config['base_url'], 'https://gateway.test/v1')
            self.assertEqual(config['api_providers']['dashscope']['api_key'], 'cloud-key')
            self.assertNotIn('environment-key', store.path.read_text())

    def test_text_runtime_builds_configured_request_without_media_dependencies(self):
        config = {'api_providers': {'deepseek': {
            'api_key': 'test-key', 'base_url': 'https://example.test/v1',
        }}, 'models': {'llm': 'deepseek-chat'}}
        resolved = resolve_model(config)
        with patch('copy_studio.model_runtime.build_opener') as build:
            response = build.return_value.open.return_value.__enter__.return_value
            response.read.return_value = json.dumps({'choices': [{'message': {'content': '整理完成'}}]}).encode()
            result = call_openai_compatible_chat(
                resolved=resolved, system_prompt='系统说明', user_prompt='语音内容',
                input_data={'temperature': 0.2}, config=config,
            )
            request = build.return_value.open.call_args.args[0]
        self.assertEqual(result, '整理完成')
        self.assertEqual(request.full_url, 'https://example.test/v1/chat/completions')
        payload = json.loads(request.data)
        self.assertEqual(payload['model'], 'deepseek-chat')
        self.assertEqual(payload['messages'][1]['content'], '语音内容')
        self.assertEqual(payload['temperature'], 0.2)

    def test_whisper_uses_explicit_executable(self):
        from copy_studio.transcript_extractor import TranscriptTaskManager

        with tempfile.TemporaryDirectory() as root:
            manager = TranscriptTaskManager(root)
            try:
                with (
                    patch.dict(os.environ, {'WHISPER_COMMAND': '/custom/bin/whisper'}),
                    patch('copy_studio.transcript_extractor.service.shutil.which', return_value='/custom/bin/whisper') as which,
                ):
                    self.assertEqual(manager._whisper_command(), '/custom/bin/whisper')
                    which.assert_called_once_with('/custom/bin/whisper')
            finally:
                manager.shutdown()


if __name__ == '__main__':
    unittest.main()
