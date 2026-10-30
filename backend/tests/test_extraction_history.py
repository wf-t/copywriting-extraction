from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path

from copy_studio.extraction_history import ExtractionHistoryStore


class ExtractionHistoryStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.store = ExtractionHistoryStore(self.root, retention_seconds=60)

    def tearDown(self):
        self.store.stop_cleanup_scheduler()
        self.temp_dir.cleanup()

    def test_json_body_contains_only_link_and_final_copy(self):
        self.store.save(7, video_url="https://v.douyin.com/example/", final_text="整理后的最终文案")

        files = list((self.root / "data" / "cdk-history" / "7").glob("*.json"))
        self.assertEqual(len(files), 1)
        payload = json.loads(files[0].read_text(encoding="utf-8"))
        self.assertEqual(
            payload,
            {"video_url": "https://v.douyin.com/example/", "final_text": "整理后的最终文案"},
        )
        self.assertEqual(self.store.list_for_cdk(7), [payload])

    def test_cleanup_removes_expired_files_and_empty_directory(self):
        self.store.save(8, video_url="https://www.douyin.com/video/8", final_text="即将过期")
        record = next((self.root / "data" / "cdk-history" / "8").glob("*.json"))
        os.utime(record, (100, 100))

        self.assertEqual(self.store.cleanup_expired(now=161), 1)
        self.assertFalse(record.exists())
        self.assertFalse(record.parent.exists())

    def test_clear_cdk_does_not_touch_other_code_history(self):
        self.store.save(9, video_url="https://www.douyin.com/video/9", final_text="九")
        self.store.save(10, video_url="https://www.douyin.com/video/10", final_text="十")

        self.store.clear_cdk(9)

        self.assertEqual(self.store.list_for_cdk(9), [])
        self.assertEqual(len(self.store.list_for_cdk(10)), 1)


if __name__ == "__main__":
    unittest.main()
