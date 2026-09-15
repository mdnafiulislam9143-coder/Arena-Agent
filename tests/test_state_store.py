# -*- coding: utf-8 -*-
"""AtomicJsonStore-এর টেস্ট — MeTube-এর app/tests/test_state_store.py-র ধাঁচে।"""
from __future__ import annotations

import datetime
import json
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "server"))

from state_store import AtomicJsonStore, from_json_compatible, to_json_compatible  # noqa: E402


class AtomicJsonStoreTest(unittest.TestCase):
    def setUp(self) -> None:
        self.dir = tempfile.mkdtemp(prefix="kaitube-state-")

    def tearDown(self) -> None:
        shutil.rmtree(self.dir, ignore_errors=True)

    def path(self, name: str = "queue.json") -> str:
        return os.path.join(self.dir, name)

    def test_roundtrip_unicode(self) -> None:
        store = AtomicJsonStore(self.path(), kind="queue")
        store.save({"items": [{"title": "বাংলা ভিডিও — ৩৬০p", "percent": 42.5}]})
        loaded = store.load()
        self.assertEqual(loaded["kind"], "queue")
        self.assertEqual(loaded["items"][0]["title"], "বাংলা ভিডিও — ৩৬০p")
        self.assertEqual(loaded["schema_version"], 1)

    def test_kind_mismatch_quarantines(self) -> None:
        """ভুল kind মানে ফাইলে ভিন্ন কিছুর ডেটা — মুছে না ফেলে পাশে সরানো হয়।"""
        store = AtomicJsonStore(self.path(), kind="queue")
        with open(self.path(), "w", encoding="utf-8") as fh:
            json.dump({"kind": "completed", "items": []}, fh)
        self.assertIsNone(store.load())
        quarantined = [f for f in os.listdir(self.dir) if ".invalid." in f]
        self.assertEqual(len(quarantined), 1)
        self.assertFalse(os.path.exists(self.path()))

    def test_corrupt_json_is_quarantined(self) -> None:
        with open(self.path(), "w", encoding="utf-8") as fh:
            fh.write("{not json at all")
        store = AtomicJsonStore(self.path(), kind="queue")
        self.assertIsNone(store.load())
        self.assertTrue(any(".invalid." in f for f in os.listdir(self.dir)))

    def test_permissions_are_owner_only(self) -> None:
        """স্টেটে URL/অপশন থাকে → শেয়ার করা মাউন্টে world-readable হওয়া যাবে না।"""
        store = AtomicJsonStore(self.path(), kind="queue")
        store.save({"items": [{"url": "https://example.com/watch?v=x"}]})
        mode = os.stat(self.path()).st_mode & 0o777
        self.assertEqual(mode & 0o077, 0, oct(mode))

    def test_failed_serialization_keeps_old_file(self) -> None:
        store = AtomicJsonStore(self.path(), kind="queue")
        store.save({"items": [{"ok": True}]})
        before = open(self.path(), encoding="utf-8").read()
        with self.assertRaises(TypeError):
            store.save({"items": [{"bad": object()}]})
        self.assertEqual(open(self.path(), encoding="utf-8").read(), before)

    def test_no_temp_files_left_behind(self) -> None:
        store = AtomicJsonStore(self.path(), kind="queue")
        for i in range(5):
            store.save({"items": [{"n": i}]})
        leftovers = [f for f in os.listdir(self.dir) if f.endswith(".tmp")]
        self.assertEqual(leftovers, [])

    def test_bytes_and_datetime_markers(self) -> None:
        value = {"blob": b"\x00\x01\x02", "when": datetime.datetime(2026, 9, 15, 12, 30)}
        restored = from_json_compatible(to_json_compatible(value))
        self.assertEqual(restored["blob"], b"\x00\x01\x02")
        self.assertEqual(restored["when"].year, 2026)

    def test_load_missing_file_returns_none(self) -> None:
        self.assertIsNone(AtomicJsonStore(self.path("nope.json"), kind="queue").load())


if __name__ == "__main__":
    unittest.main(verbosity=2)
