from __future__ import annotations

import json
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from copy_studio.transcript_extractor.platforms import (
    parse_bilibili_share,
    parse_douyin_share,
    parse_kuaishou_share,
    parse_xiaohongshu_share,
)
from copy_studio.transcript_extractor.service import (
    TaskState,
    TranscriptJob,
    TranscriptTaskManager,
    extract_first_url,
)


class FakeResponse:
    def __init__(self, body: str = "", url: str = ""):
        self.body = body.encode("utf-8")
        self.url = url

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def geturl(self):
        return self.url

    def read(self):
        return self.body


class TranscriptExtractorTests(unittest.TestCase):
    def test_extracts_url_from_full_share_copy(self):
        source = "这是一段分享文字 https://v.douyin.com/abc123/ 复制打开抖音"
        self.assertEqual(extract_first_url(source), "https://v.douyin.com/abc123/")

    def test_parse_douyin_router_data(self):
        router_data = {
            "loaderData": {
                "video_(id)/page": {
                    "videoInfoRes": {
                        "item_list": [{
                            "desc": "测试视频",
                            "author": {"nickname": "测试作者"},
                            "video": {
                                "duration": 125000,
                                "play_addr": {"url_list": ["https://media.test/playwm/video.mp4"]},
                            },
                        }]
                    }
                }
            }
        }
        html = f"<script>window._ROUTER_DATA = {json.dumps(router_data)}</script>"
        with (
            patch(
                "copy_studio.transcript_extractor.platforms.douyin._resolve_share_url",
                return_value="https://www.douyin.com/video/1234567890",
            ),
            patch(
                "copy_studio.transcript_extractor.platforms.douyin._request_share_html",
                return_value=html,
            ),
        ):
            result = parse_douyin_share("https://v.douyin.com/example/")
        self.assertEqual(result["video_id"], "1234567890")
        self.assertEqual(result["title"], "测试视频")
        self.assertEqual(result["author"], "测试作者")
        self.assertEqual(result["duration"], 125.0)
        self.assertEqual(result["media_url"], "https://media.test/play/video.mp4")

    def test_loads_ssr_share_page_after_resolving_short_link(self):
        router_data = {
            "loaderData": {
                "video_(id)/page": {
                    "videoInfoRes": {
                        "item_list": [{
                            "desc": "带参数的分享页",
                            "author": {"nickname": "作者"},
                            "video": {
                                "duration": 1000,
                                "play_addr": {"url_list": ["https://media.test/playwm/video.mp4"]},
                            },
                        }]
                    }
                }
            }
        }
        html = f"<script>window._ROUTER_DATA = {json.dumps(router_data)}</script>"
        with (
            patch(
                "copy_studio.transcript_extractor.platforms.douyin._resolve_share_url",
                return_value="https://www.iesdouyin.com/share/video/7672694373275979014/?share_sign=test",
            ),
            patch(
                "copy_studio.transcript_extractor.platforms.douyin._request_share_html",
                return_value=html,
            ) as request,
        ):
            result = parse_douyin_share("https://v.douyin.com/new-link/")
        request.assert_called_once_with(
            "https://www.iesdouyin.com/share/video/7672694373275979014/?from_ssr=1"
        )
        self.assertEqual(result["video_id"], "7672694373275979014")

    def test_parse_bilibili_uses_public_combined_mp4_for_selected_part(self):
        view_payload = {
            "code": 0,
            "data": {
                "bvid": "BV1TEST",
                "title": "测试合集",
                "owner": {"name": "UP主"},
                "pages": [
                    {"cid": 11, "duration": 30, "part": "第一集"},
                    {"cid": 22, "duration": 40, "part": "第二集"},
                ],
            },
        }
        play_payload = {
            "code": 0,
            "data": {
                "timelength": 40500,
                "durl": [{"url": "https://media.test/bilibili-low.mp4"}],
            },
        }
        with (
            patch(
                "copy_studio.transcript_extractor.platforms.bilibili.resolve_url",
                return_value="https://www.bilibili.com/video/BV1TEST?p=2",
            ),
            patch(
                "copy_studio.transcript_extractor.platforms.bilibili.request_json",
                side_effect=[view_payload, play_payload],
            ) as request,
        ):
            result = parse_bilibili_share("https://b23.tv/example")

        self.assertEqual(result["video_id"], "BV1TEST-P2")
        self.assertEqual(result["duration"], 40.5)
        self.assertEqual(result["media_url"], "https://media.test/bilibili-low.mp4")
        self.assertIn("P2 第二集", result["title"])
        self.assertEqual(request.call_args_list[1].kwargs["params"]["fnval"], 0)

    def test_parse_kuaishou_apollo_state(self):
        state = {
            "defaultClient": {
                "VisionVideoDetailPhoto:3xabc": {
                    "__typename": "VisionVideoDetailPhoto",
                    "caption": "快手测试视频",
                    "duration": 88000,
                    "photoUrl": "https://media.test/kuaishou.mp4",
                },
                "VisionVideoDetailAuthor:author": {
                    "__typename": "VisionVideoDetailAuthor",
                    "name": "快手作者",
                },
            }
        }
        html = f"<script>window.__APOLLO_STATE__={json.dumps(state)};</script>"
        with patch(
            "copy_studio.transcript_extractor.platforms.kuaishou._get_page",
            return_value=("https://www.kuaishou.com/short-video/3xabc", html),
        ):
            result = parse_kuaishou_share("https://v.kuaishou.com/example")

        self.assertEqual(result["video_id"], "3xabc")
        self.assertEqual(result["duration"], 88.0)
        self.assertEqual(result["author"], "快手作者")
        self.assertEqual(result["media_url"], "https://media.test/kuaishou.mp4")

    def test_parse_kuaishou_mobile_state(self):
        state = {
            "obfuscated-page-key": {
                "result": 1,
                "photo": {
                    "caption": "快手移动页测试视频",
                    "duration": 45123,
                    "photoType": "VIDEO",
                    "userName": "移动页作者",
                    "share_info": "photoId=3xmobile",
                    "mainMvUrls": [{"url": "https://media.test/kuaishou-mobile.mp4"}],
                },
            }
        }
        html = f"<script>window.INIT_STATE={json.dumps(state)};</script>"
        with patch(
            "copy_studio.transcript_extractor.platforms.kuaishou._get_page",
            return_value=("https://example.m.chenzhongtech.com/fw/photo/3xmobile", html),
        ):
            result = parse_kuaishou_share("https://v.kuaishou.com/example")

        self.assertEqual(result["video_id"], "3xmobile")
        self.assertEqual(result["duration"], 45.123)
        self.assertEqual(result["author"], "移动页作者")
        self.assertEqual(result["media_url"], "https://media.test/kuaishou-mobile.mp4")

    def test_parse_xiaohongshu_initial_state(self):
        state = {
            "note": {
                "noteDetailMap": {
                    "abc123": {
                        "note": {
                            "title": "小红书测试视频",
                            "desc": "描述",
                            "user": {"nickname": "小红书作者"},
                            "video": {
                                "media": {
                                    "stream": {
                                        "h264": [{
                                            "duration": 66000,
                                            "videoBitrate": 1000,
                                            "masterUrl": "https://media.test/xhs.mp4",
                                        }]
                                    }
                                }
                            },
                        }
                    }
                }
            }
        }
        html = f"<script>window.__INITIAL_STATE__={json.dumps(state)}</script>"
        with patch(
            "copy_studio.transcript_extractor.platforms.xiaohongshu._get_page",
            return_value=("https://www.xiaohongshu.com/explore/abc123?xsec_token=test", html),
        ):
            result = parse_xiaohongshu_share("xhslink.com/m/example")

        self.assertEqual(result["video_id"], "abc123")
        self.assertEqual(result["duration"], 66.0)
        self.assertEqual(result["author"], "小红书作者")
        self.assertEqual(result["media_url"], "https://media.test/xhs.mp4")

    def test_manager_has_no_database_or_persistent_job_path(self):
        with tempfile.TemporaryDirectory() as root:
            manager = TranscriptTaskManager(Path(root))
            self.assertFalse(hasattr(manager, "store"))
            self.assertFalse(hasattr(manager, "database"))
            self.assertEqual(manager._tasks, {})

    def test_deepseek_semantic_organization_uses_configured_runtime(self):
        with tempfile.TemporaryDirectory() as root:
            manager = TranscriptTaskManager(Path(root))
            fake_config = {
                "api_providers": {"deepseek": {"api_key": "test", "base_url": "https://example.test/v1"}},
                "models": {"llm": "deepseek-chat"},
            }
            with (
                patch.object(manager.model_config_store, "load", return_value=fake_config),
                patch("copy_studio.transcript_extractor.service.resolve_model", return_value=object()),
                patch(
                    "copy_studio.transcript_extractor.service.call_openai_compatible_chat",
                    return_value="## 整理后的完整文案\n已整理",
                ) as call,
            ):
                text = manager._organize_transcript("原始语音", title="测试标题", author="测试作者")
            self.assertIn("已整理", text)
            self.assertEqual(call.call_args.kwargs["input_data"]["temperature"], 0.2)
            self.assertIn("原始语音", call.call_args.kwargs["user_prompt"])

    def test_parses_dashscope_transcription_result(self):
        payload = {
            "transcripts": [{
                "text": "第一句。第二句。",
                "sentences": [
                    {"begin_time": 100, "end_time": 900, "text": "第一句。"},
                    {"begin_time": 900, "end_time": 1800, "text": "第二句。"},
                ],
            }]
        }
        result = TranscriptTaskManager._parse_dashscope_transcript(payload)
        self.assertEqual(result["text"], "第一句。第二句。")
        self.assertEqual(result["segments"][0]["start"], 0.1)
        self.assertEqual(result["model"], "fun-asr")

    def test_completed_semantic_result_is_written_to_cdk_history(self):
        captured: list[tuple[int, str, str]] = []

        def write_history(cdk_id: int, *, video_url: str, final_text: str):
            captured.append((cdk_id, video_url, final_text))

        with tempfile.TemporaryDirectory() as root:
            manager = TranscriptTaskManager(Path(root), history_writer=write_history)
            manager._tasks["task-history"] = TaskState(id="task-history", platform="douyin", owner_id=27)
            video_info = {
                "platform": "douyin",
                "media_url": "https://media.test/video.mp4",
                "media_headers": {"Referer": "https://www.douyin.com/"},
                "source_url": "https://v.douyin.com/history/",
                "title": "测试",
                "author": "作者",
                "duration": 10,
            }
            with (
                patch.object(manager, "_download"),
                patch.object(manager, "_extract_audio"),
                patch.object(manager, "_transcribe_local", return_value={"text": "原文", "segments": [], "language": "zh", "model": "test"}),
                patch.object(manager, "_organize_transcript", return_value="最终整理文案"),
            ):
                manager._run_prepared(TranscriptJob(
                    task_id="task-history",
                    owner_id=27,
                    platform="douyin",
                    asr_provider="local",
                    video_info=video_info,
                    request_base_url="http://127.0.0.1",
                ))
            manager.shutdown()

        self.assertEqual(captured, [(27, "https://v.douyin.com/history/", "最终整理文案")])
        self.assertEqual(manager._tasks["task-history"].status, "completed")

    def test_dashscope_pipeline_lets_cloud_fetch_media_without_local_download(self):
        with tempfile.TemporaryDirectory() as root:
            manager = TranscriptTaskManager(
                Path(root),
                settings_provider=lambda: {"asr_provider": "dashscope"},
            )
            manager._tasks["task-cloud"] = TaskState(
                id="task-cloud",
                platform="douyin",
                asr_provider="dashscope",
                owner_id=5,
            )
            video_info = {
                "platform": "douyin",
                "media_url": "https://aweme.snssdk.com/aweme/v1/play/?video_id=test",
                "media_headers": {},
                "source_url": "https://v.douyin.com/cloud/",
                "title": "云端识别",
                "author": "作者",
                "duration": 10,
            }
            with (
                patch.object(manager, "_download") as download,
                patch.object(manager, "_extract_audio") as extract_audio,
                patch.object(
                    manager,
                    "_transcribe_dashscope_url",
                    return_value={"text": "云端原文", "segments": [], "language": "zh", "model": "fun-asr"},
                ) as transcribe,
                patch.object(manager, "_organize_transcript", return_value="云端最终文案"),
            ):
                manager._run_prepared(TranscriptJob(
                    task_id="task-cloud",
                    owner_id=5,
                    platform="douyin",
                    asr_provider="dashscope",
                    video_info=video_info,
                    request_base_url="http://127.0.0.1",
                ))
            manager.shutdown()

        download.assert_not_called()
        extract_audio.assert_not_called()
        transcribe.assert_called_once_with(video_info["media_url"])
        self.assertEqual(manager._tasks["task-cloud"].status, "completed")
        self.assertEqual(manager._tasks["task-cloud"].result["model"], "fun-asr")
        self.assertNotIn("media_url", manager._tasks["task-cloud"].result["video"])
        self.assertNotIn("media_headers", manager._tasks["task-cloud"].result["video"])

    def test_provider_is_frozen_when_task_enters_its_own_queue(self):
        settings = {"asr_provider": "local"}
        video_info = {
            "platform": "douyin",
            "video_id": "fixed-provider",
            "media_url": "https://media.test/fixed.mp4",
            "media_headers": {},
            "source_url": "https://v.douyin.com/fixed/",
            "title": "固定识别方式",
            "author": "作者",
            "duration": 10,
        }
        with tempfile.TemporaryDirectory() as root:
            manager = TranscriptTaskManager(Path(root), settings_provider=lambda: dict(settings))
            with (
                patch.object(manager._executors["local"], "submit") as local_submit,
                patch.object(manager._executors["dashscope"], "submit") as cloud_submit,
            ):
                local_task = manager.create_prepared(video_info, owner_id=11)
                settings["asr_provider"] = "dashscope"
                cloud_task = manager.create_prepared(video_info, owner_id=22)

            local_job = local_submit.call_args.args[1]
            cloud_job = cloud_submit.call_args.args[1]
            manager.shutdown()

        self.assertEqual(local_task["asr_provider"], "local")
        self.assertEqual(cloud_task["asr_provider"], "dashscope")
        self.assertEqual(local_job.asr_provider, "local")
        self.assertEqual(local_job.owner_id, 11)
        self.assertEqual(cloud_job.asr_provider, "dashscope")
        self.assertEqual(cloud_job.owner_id, 22)

    def test_local_job_does_not_block_fun_asr_queue(self):
        settings = {"asr_provider": "local"}
        local_started = threading.Event()
        release_local = threading.Event()

        def video(video_id: str) -> dict[str, object]:
            return {
                "platform": "douyin",
                "video_id": video_id,
                "media_url": f"https://media.test/{video_id}.mp4",
                "media_headers": {},
                "source_url": f"https://v.douyin.com/{video_id}/",
                "title": video_id,
                "author": video_id,
                "duration": 10,
            }

        def block_local_download(*_args, **_kwargs):
            local_started.set()
            if not release_local.wait(timeout=3):
                raise TimeoutError("本地测试任务没有被释放")

        with tempfile.TemporaryDirectory() as root:
            manager = TranscriptTaskManager(Path(root), settings_provider=lambda: dict(settings))
            with (
                patch.object(manager, "_download", side_effect=block_local_download),
                patch.object(manager, "_extract_audio"),
                patch.object(
                    manager,
                    "_transcribe_local",
                    return_value={"text": "本地原文", "segments": [], "language": "zh", "model": "local"},
                ),
                patch.object(
                    manager,
                    "_transcribe_dashscope_url",
                    return_value={"text": "云端原文", "segments": [], "language": "zh", "model": "fun-asr"},
                ),
                patch.object(manager, "_organize_transcript", side_effect=lambda text, **_kwargs: f"整理：{text}"),
            ):
                local_task = manager.create_prepared(video("local-job"), owner_id=1)
                self.assertTrue(local_started.wait(timeout=1))
                settings["asr_provider"] = "dashscope"
                cloud_task = manager.create_prepared(video("cloud-job"), owner_id=2)

                deadline = time.time() + 2
                while time.time() < deadline and manager._tasks[cloud_task["id"]].status != "completed":
                    time.sleep(0.02)
                self.assertEqual(manager._tasks[cloud_task["id"]].status, "completed")
                self.assertEqual(manager._tasks[local_task["id"]].status, "processing")

                release_local.set()
                deadline = time.time() + 2
                while time.time() < deadline and manager._tasks[local_task["id"]].status != "completed":
                    time.sleep(0.02)
            manager.shutdown()

        self.assertEqual(manager._tasks[local_task["id"]].result["semantic_text"], "整理：本地原文")
        self.assertEqual(manager._tasks[cloud_task["id"]].result["semantic_text"], "整理：云端原文")

    def test_concurrent_cloud_tasks_keep_owner_and_result_isolated(self):
        barrier = threading.Barrier(2)
        histories: list[tuple[int, str]] = []

        def transcribe(media_url: str):
            barrier.wait(timeout=3)
            marker = "用户A原文" if "user-a" in media_url else "用户B原文"
            return {"text": marker, "segments": [], "language": "zh", "model": "fun-asr"}

        def organize(transcript: str, **_kwargs):
            return f"整理结果：{transcript}"

        def save_history(cdk_id: int, *, video_url: str, final_text: str):
            histories.append((cdk_id, f"{video_url}|{final_text}"))

        def video(owner: str) -> dict[str, object]:
            return {
                "platform": "douyin",
                "video_id": owner,
                "media_url": f"https://media.test/{owner}.mp4",
                "media_headers": {},
                "source_url": f"https://v.douyin.com/{owner}/",
                "title": owner,
                "author": owner,
                "duration": 10,
            }

        with patch.dict("os.environ", {"TRANSCRIPT_FUN_ASR_WORKERS": "2"}):
            with tempfile.TemporaryDirectory() as root:
                manager = TranscriptTaskManager(
                    Path(root),
                    settings_provider=lambda: {"asr_provider": "dashscope"},
                    history_writer=save_history,
                )
                with (
                    patch.object(manager, "_transcribe_dashscope_url", side_effect=transcribe),
                    patch.object(manager, "_organize_transcript", side_effect=organize),
                ):
                    task_a = manager.create_prepared(video("user-a"), owner_id=101)
                    task_b = manager.create_prepared(video("user-b"), owner_id=202)
                    deadline = time.time() + 5
                    while time.time() < deadline:
                        if all(
                            manager._tasks[task_id].status == "completed"
                            for task_id in (task_a["id"], task_b["id"])
                        ):
                            break
                        time.sleep(0.02)

                result_a = manager.get(task_a["id"], owner_id=101)
                result_b = manager.get(task_b["id"], owner_id=202)
                with self.assertRaises(KeyError):
                    manager.get(task_a["id"], owner_id=202)
                self.assertFalse(manager.forget(task_a["id"], owner_id=202))
                self.assertEqual(manager.get(task_a["id"], owner_id=101)["id"], task_a["id"])
                manager.shutdown()

        self.assertEqual(result_a["result"]["video"]["video_id"], "user-a")
        self.assertEqual(result_a["result"]["semantic_text"], "整理结果：用户A原文")
        self.assertEqual(result_b["result"]["video"]["video_id"], "user-b")
        self.assertEqual(result_b["result"]["semantic_text"], "整理结果：用户B原文")
        self.assertEqual({item[0] for item in histories}, {101, 202})


if __name__ == "__main__":
    unittest.main()
