from __future__ import annotations

import tempfile
import threading
import unittest
from pathlib import Path

from copy_studio.commercial import CommercialStore


class CommercialStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.store = CommercialStore(self.root)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_generates_requested_tier_and_usage_limit(self):
        cdks = self.store.create_cdks(duration_limit_minutes=5, total_uses=5, quantity=2)
        self.assertEqual(len(cdks), 2)
        self.assertEqual(cdks[0]["duration_limit_minutes"], 5)
        self.assertEqual(cdks[0]["remaining_uses"], 5)
        self.assertTrue(cdks[0]["code"].startswith("TX-05M-"))

    def test_duration_over_limit_does_not_consume_quota(self):
        cdk = self.store.create_cdks(duration_limit_minutes=5, total_uses=2)[0]
        with self.assertRaisesRegex(ValueError, "未扣除次数"):
            self.store.consume(cdk["id"], duration_seconds=301)
        refreshed = self.store.list_cdks(query=cdk["code"])[0]
        self.assertEqual(refreshed["used_uses"], 0)
        self.assertEqual(refreshed["remaining_uses"], 2)

    def test_valid_duration_consumes_exactly_one_use(self):
        cdk = self.store.create_cdks(duration_limit_minutes=10, total_uses=3)[0]
        updated = self.store.consume(cdk["id"], duration_seconds=600)
        self.assertEqual(updated["used_uses"], 1)
        self.assertEqual(updated["remaining_uses"], 2)

    def test_exhausted_code_can_enter_but_cannot_consume_again(self):
        cdk = self.store.create_cdks(duration_limit_minutes=5, total_uses=1)[0]
        token, _ = self.store.create_user_session(cdk["code"])
        self.store.consume(cdk["id"], duration_seconds=120)
        self.assertEqual(self.store.get_user_session(token)["id"], cdk["id"])
        new_token, refreshed = self.store.create_user_session(cdk["code"])
        self.assertEqual(refreshed["remaining_uses"], 0)
        self.assertEqual(self.store.get_user_session(new_token)["id"], cdk["id"])
        with self.assertRaisesRegex(PermissionError, "使用次数已用完"):
            self.store.consume(cdk["id"], duration_seconds=60)

    def test_concurrent_consumption_cannot_exceed_total_uses(self):
        cdk = self.store.create_cdks(duration_limit_minutes=5, total_uses=1)[0]
        barrier = threading.Barrier(2)
        outcomes: list[str] = []

        def consume_once():
            barrier.wait()
            try:
                self.store.consume(cdk["id"], duration_seconds=60)
                outcomes.append("ok")
            except PermissionError:
                outcomes.append("denied")

        threads = [threading.Thread(target=consume_once) for _ in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        self.assertCountEqual(outcomes, ["ok", "denied"])
        refreshed = self.store.list_cdks(query=cdk["code"])[0]
        self.assertEqual(refreshed["used_uses"], 1)

    def test_disabling_code_invalidates_existing_session(self):
        cdk = self.store.create_cdks(duration_limit_minutes=20, total_uses=2)[0]
        token, _ = self.store.create_user_session(cdk["code"])
        self.store.set_cdk_enabled(cdk["id"], False)
        self.assertIsNone(self.store.get_user_session(token, allow_exhausted=True, allow_disabled=True))

    def test_asr_settings_are_persistent(self):
        saved = self.store.update_settings(asr_provider="dashscope")
        self.assertEqual(saved["asr_provider"], "dashscope")
        reopened = CommercialStore(self.root)
        self.assertEqual(reopened.get_settings(), saved)

    def test_cdk_filters_can_be_combined_and_exported(self):
        active_five = self.store.create_cdks(duration_limit_minutes=5, total_uses=3)[0]
        disabled_five = self.store.create_cdks(duration_limit_minutes=5, total_uses=5)[0]
        exhausted_ten = self.store.create_cdks(duration_limit_minutes=10, total_uses=1)[0]
        active_ten = self.store.create_cdks(duration_limit_minutes=10, total_uses=5)[0]
        self.store.set_cdk_enabled(disabled_five["id"], False)
        self.store.consume(exhausted_ten["id"], duration_seconds=120)

        disabled_match = self.store.list_cdks(
            status="disabled",
            duration_limit_minutes=5,
            total_uses=5,
        )
        self.assertEqual([item["code"] for item in disabled_match], [disabled_five["code"]])

        exhausted_match = self.store.list_cdks(status="exhausted")
        self.assertEqual([item["code"] for item in exhausted_match], [exhausted_ten["code"]])

        exported = self.store.export_cdk_codes(
            status="active",
            duration_limit_minutes=10,
            total_uses=5,
        )
        self.assertEqual(exported, [active_ten["code"]])
        self.assertNotIn(active_five["code"], exported)

    def test_dashboard_statistics_include_today_usage(self):
        cdk = self.store.create_cdks(duration_limit_minutes=5, total_uses=3)[0]
        self.store.consume(cdk["id"], duration_seconds=90)
        stats = self.store.statistics()
        self.assertEqual(stats["total_codes"], 1)
        self.assertEqual(stats["used_uses"], 1)
        self.assertEqual(stats["today_uses"], 1)
        self.assertEqual(stats["today_duration_seconds"], 90.0)

    def test_bulk_delete_uses_filters_and_expected_count_guard(self):
        matching = self.store.create_cdks(duration_limit_minutes=5, total_uses=3, quantity=2)
        untouched = self.store.create_cdks(duration_limit_minutes=10, total_uses=3)[0]
        count = self.store.count_cdks(duration_limit_minutes=5, total_uses=3)
        self.assertEqual(count, 2)

        deleted = self.store.delete_cdks(
            duration_limit_minutes=5,
            total_uses=3,
            expected_count=count,
        )
        self.assertCountEqual(deleted, [item["id"] for item in matching])
        self.assertEqual(self.store.list_cdks(), [untouched])
        self.assertEqual(self.store.count_cdks(query=matching[0]["code"]), 0)

        self.store.create_cdks(duration_limit_minutes=10, total_uses=3)
        with self.assertRaisesRegex(RuntimeError, "匹配数量已从"):
            self.store.delete_cdks(duration_limit_minutes=10, total_uses=3, expected_count=1)
        self.assertEqual(self.store.count_cdks(duration_limit_minutes=10, total_uses=3), 2)


if __name__ == "__main__":
    unittest.main()
