# -*- coding: utf-8 -*-
"""
ডাউনলোড কিউ টেস্ট — MeTube-এর app/tests/test_download_queue.py-র ধাঁচ (তবে DemoRunner
দিয়ে, তাই ইন্টারনেট লাগে না)।

যা যাচাই করা হয়: স্টেটাস ট্রানজিশন, কনকারেন্সি ক্যাপ, persistence/রিলোড, বাতিল,
completed কমপ্যাকশন, stale `downloading` রিকভারি, পাথ-ট্রাভার্সাল-নিরাপদ ফাইল খোঁজা।
"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile
import threading
import time
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "server"))

import dlqueue  # noqa: E402
from config import Config  # noqa: E402


def make_config(root: str, **overrides) -> Config:
    env = {
        "DOWNLOAD_DIR": os.path.join(root, "downloads"),
        "TEMP_DIR": os.path.join(root, "tmp"),
        "STATE_DIR": os.path.join(root, "state"),
        "MAX_CONCURRENT_DOWNLOADS": "2",
        "DEMO": "true",
    }
    env.update(overrides)
    return Config(env)


def wait_for(predicate, timeout: float = 8.0, interval: float = 0.05) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return False


class QueueTest(unittest.TestCase):
    def setUp(self) -> None:
        self.root = tempfile.mkdtemp(prefix="kaitube-queue-")
        self.cfg = make_config(self.root)
        self.cfg.ensure_dirs()
        self.runner = dlqueue.DemoRunner(self.cfg.DOWNLOAD_DIR, size=32768, delay=0.0)
        self.queue = dlqueue.DownloadQueue(self.cfg, self.runner)
        self.queue.initialize()

    def tearDown(self) -> None:
        self.queue.close()
        shutil.rmtree(self.root, ignore_errors=True)

    # ------------------------------------------------------------------ বেসিক
    def test_add_download_finishes_and_file_exists(self) -> None:
        result = self.queue.add("https://youtu.be/dQw4w9WgXcQ", height=360)
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["item"]["id"], "dQw4w9WgXcQ")

        self.assertTrue(wait_for(lambda: self.queue.get("dQw4w9WgXcQ")["status"] == "finished"),
                        "ডাউনলোড শেষ হয়নি")
        item = self.queue.get("dQw4w9WgXcQ")
        self.assertEqual(item["progress"], 100.0)
        self.assertTrue(item["playable"])
        self.assertEqual(item["file_url"], "/api/file/dQw4w9WgXcQ")
        self.assertTrue(os.path.exists(self.queue.find_file("dQw4w9WgXcQ")))
        self.assertNotIn("cancel_requested", item)          # অভ্যন্তরীণ ফিল্ড ফাঁস হবে না

    def test_duplicate_add_is_rejected(self) -> None:
        self.queue.add("https://youtu.be/dQw4w9WgXcQ")
        again = self.queue.add("https://youtu.be/dQw4w9WgXcQ")
        self.assertEqual(again["status"], "error")
        self.assertIn("ইতিমধ্যে", again["msg"])

    def test_invalid_input_rejected(self) -> None:
        with self.assertRaises(ValueError):
            self.queue.add("কিছু এলোমেলো টেক্সট")

    def test_probe_fills_title_and_duration(self) -> None:
        self.queue.add("https://youtu.be/abcdefghijk")
        self.assertTrue(wait_for(lambda: self.queue.get("abcdefghijk")["duration"] > 0))
        item = self.queue.get("abcdefghijk")
        self.assertIn("ডেমো", item["title"])
        self.assertEqual(item["author"], "KaiOS Demo")

    # -------------------------------------------------------------- কনকারেন্সি
    def test_concurrency_cap_is_respected(self) -> None:
        """MAX_CONCURRENT_DOWNLOADS=2 → একসাথে কখনো ৩টি চলবে না (MeTube-এর সেমাফোর আচরণ)।"""
        peak = {"value": 0, "current": 0}
        lock = threading.Lock()

        class ProbeRunner(dlqueue.DemoRunner):
            def download(self, item, on_progress, should_cancel):
                with lock:
                    peak["current"] += 1
                    peak["value"] = max(peak["value"], peak["current"])
                try:
                    time.sleep(0.25)
                    return super().download(item, on_progress, should_cancel)
                finally:
                    with lock:
                        peak["current"] -= 1

        queue = dlqueue.DownloadQueue(self.cfg, ProbeRunner(self.cfg.DOWNLOAD_DIR, size=4096, delay=0.0))
        queue.initialize()
        try:
            for vid in ("aaaaaaaaaaa", "bbbbbbbbbbb", "ccccccccccc", "ddddddddddd"):
                queue.add("https://youtu.be/%s" % vid)
            self.assertTrue(wait_for(lambda: len(queue.list_all()["completed"]) == 4, timeout=10))
            self.assertLessEqual(peak["value"], 2)
            self.assertEqual(peak["value"], 2)          # সত্যিই ক্যাপ পর্যন্ত ব্যবহার হচ্ছে
        finally:
            queue.close()

    # ----------------------------------------------------------------- ক্যানসেল
    def test_cancel_running_download(self) -> None:
        slow = dlqueue.DemoRunner(self.cfg.DOWNLOAD_DIR, size=1 << 20, delay=0.03)
        queue = dlqueue.DownloadQueue(make_config(self.root, MAX_CONCURRENT_DOWNLOADS="1"), slow)
        queue.initialize()
        try:
            queue.add("https://youtu.be/zzzzzzzzzzz")
            self.assertTrue(wait_for(lambda: queue.get("zzzzzzzzzzz")["status"] == "downloading"))
            result = queue.cancel(["zzzzzzzzzzz"])
            self.assertEqual(result["status"], "ok")
            self.assertTrue(wait_for(lambda: queue.get("zzzzzzzzzzz") is None))
            self.assertEqual(queue.list_all()["completed"], [])
        finally:
            queue.close()

    def test_cancel_queued_item_removes_it(self) -> None:
        self.queue.cancel(["dQw4w9WgXcQ"])
        self.queue.add("https://youtu.be/dQw4w9WgXcQ")
        self.assertEqual(self.queue.cancel(["dQw4w9WgXcQ"])["status"], "ok")
        self.assertIsNone(self.queue.get("dQw4w9WgXcQ"))

    def test_cancel_unknown_id_reports_error(self) -> None:
        self.assertEqual(self.queue.cancel(["nope"])["status"], "error")

    # ------------------------------------------------------------- persistence
    def test_state_survives_restart(self) -> None:
        self.queue.add("https://youtu.be/dQw4w9WgXcQ")
        self.assertTrue(wait_for(lambda: self.queue.get("dQw4w9WgXcQ")["status"] == "finished"))
        self.queue.close()

        revived = dlqueue.DownloadQueue(self.cfg, dlqueue.DemoRunner(self.cfg.DOWNLOAD_DIR, size=1024, delay=0.0))
        revived.initialize()
        try:
            completed = revived.list_all()["completed"]
            self.assertEqual([i["id"] for i in completed], ["dQw4w9WgXcQ"])
            self.assertTrue(completed[0]["playable"])
            self.assertTrue(revived.find_file("dQw4w9WgXcQ"))
        finally:
            revived.close()

    def test_stale_downloading_is_requeued(self) -> None:
        """ক্র্যাশের পর `downloading`-এ আটকে থাকা রেকর্ড আবার কিউতে ফেরে (MeTube-এর রিকভারি)।"""
        stale = dlqueue.new_item("https://youtu.be/stalevideo1", 360)
        stale["status"] = dlqueue.STATUS_DOWNLOADING
        stale["id"] = "stalevideo1"
        queue = dlqueue.DownloadQueue(self.cfg, dlqueue.DemoRunner(self.cfg.DOWNLOAD_DIR, size=1024, delay=0.0))
        queue.queue_store.save({"items": [dlqueue.compact(stale)]})
        queue.initialize()
        try:
            self.assertTrue(wait_for(lambda: queue.get("stalevideo1")["status"] == "finished"))
        finally:
            queue.close()

    def test_persisted_entries_are_compacted(self) -> None:
        self.queue.add("https://youtu.be/dQw4w9WgXcQ")
        self.assertTrue(wait_for(lambda: self.queue.get("dQw4w9WgXcQ")["status"] == "finished"))
        self.queue._persist(force=True)
        payload = self.queue.completed_store.load()
        self.assertEqual(payload["kind"], "completed")
        record = payload["items"][0]
        self.assertNotIn("cancel_requested", record)
        self.assertNotIn("speed", record)             # ভারী/ক্ষণস্থায়ী ফিল্ড বাদ
        self.assertIn("filepath", record)             # প্লেব্যাকের জন্য দরকারি ফিল্ড রাখা

    # ------------------------------------------------------------------ clear
    def test_clear_keeps_file_by_default(self) -> None:
        self.queue.add("https://youtu.be/dQw4w9WgXcQ")
        self.assertTrue(wait_for(lambda: self.queue.get("dQw4w9WgXcQ")["status"] == "finished"))
        path = self.queue.find_file("dQw4w9WgXcQ")
        self.assertEqual(self.queue.clear(["dQw4w9WgXcQ"])["status"], "ok")
        self.assertEqual(self.queue.list_all()["completed"], [])
        self.assertTrue(os.path.exists(path))

    def test_clear_deletes_file_when_configured(self) -> None:
        cfg = make_config(self.root, DELETE_FILE_ON_TRASHCAN="true")
        cfg.ensure_dirs()
        queue = dlqueue.DownloadQueue(cfg, dlqueue.DemoRunner(cfg.DOWNLOAD_DIR, size=4096, delay=0.0))
        queue.initialize()
        try:
            queue.add("https://youtu.be/dQw4w9WgXcQ")
            self.assertTrue(wait_for(lambda: queue.get("dQw4w9WgXcQ")["status"] == "finished"))
            path = queue.find_file("dQw4w9WgXcQ")
            queue.clear(["dQw4w9WgXcQ"])
            self.assertFalse(os.path.exists(path))
        finally:
            queue.close()

    def test_start_pending_retries_errored(self) -> None:
        item = dlqueue.new_item("https://youtu.be/errrrrrrrrr", 360)
        item["id"] = "errrrrrrrrr"
        item["status"] = dlqueue.STATUS_ERROR
        item["error"] = "নেটওয়ার্ক"
        self.queue._queue.append(item)
        self.assertEqual(self.queue.start_pending(["errrrrrrrrr"])["status"], "ok")
        self.assertTrue(wait_for(lambda: self.queue.get("errrrrrrrrr")["status"] == "finished"))


class HelperTest(unittest.TestCase):
    def test_youtube_id_extraction(self) -> None:
        cases = {
            "https://www.youtube.com/watch?v=dQw4w9WgXcQ": "dQw4w9WgXcQ",
            "https://youtu.be/dQw4w9WgXcQ?t=5": "dQw4w9WgXcQ",
            "https://www.youtube.com/shorts/dQw4w9WgXcQ": "dQw4w9WgXcQ",
            "dQw4w9WgXcQ": "dQw4w9WgXcQ",
            "https://example.com/other": "",
        }
        for url, expected in cases.items():
            self.assertEqual(dlqueue.youtube_id(url), expected, url)

    def test_backfill_adds_missing_fields(self) -> None:
        item = dlqueue.backfill({"id": "x"})
        self.assertEqual(item["status"], dlqueue.STATUS_QUEUED)
        self.assertFalse(item["cancel_requested"])
        self.assertEqual(item["percent"], 0.0)

    def test_format_selector_is_kaios_safe(self) -> None:
        runner = dlqueue.YtdlpRunner("/tmp/dl", "/tmp/tmp", height=360)
        selector = runner.format_selector()
        self.assertIn("vcodec^=avc1", selector)      # H.264 আগে
        self.assertIn("height<=360", selector)
        self.assertTrue(selector.startswith("bv*"))
        self.assertEqual(runner.format_selector(audio_only=True), "bestaudio[ext=m4a]/bestaudio")

    def test_extractor_args_dict_conversion(self) -> None:
        runner = dlqueue.YtdlpRunner("/tmp/dl", "/tmp/tmp",
                                     extractor_args="youtube:player_client=default,tv,-android_sdkless")
        self.assertEqual(runner._extractor_args_dict(),
                         {"youtube": {"player_client": ["default", "tv", "-android_sdkless"]}})


if __name__ == "__main__":
    unittest.main(verbosity=2)
