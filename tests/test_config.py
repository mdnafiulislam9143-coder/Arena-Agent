# -*- coding: utf-8 -*-
"""
Config টেস্ট — MeTube-এর app/tests/test_config.py-র ধাঁচে। যেগুলো আসলে প্রোডাকশনে ভাঙে,
সেগুলোই কভার করা হচ্ছে: %NAME ইনডিরেকশন (সাফিক্স সহ), কঠোর বুলিয়ান, HOST='*', trailing slash।
"""
from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "server"))

from config import Config  # noqa: E402


class ConfigTest(unittest.TestCase):
    def test_defaults_resolve_indirection_with_suffix(self) -> None:
        """এটাই ছিল আসল বাগ: `%DOWNLOAD_DIR/.state` চুপচাপ '' হয়ে যেত → স্টেট রিপো রুটে পড়ত।"""
        cfg = Config({})
        self.assertEqual(cfg.DOWNLOAD_DIR, "downloads")
        self.assertEqual(cfg.TEMP_DIR, os.path.join("downloads", "tmp"))
        self.assertEqual(cfg.STATE_DIR, os.path.join("downloads", ".state"))

    def test_pure_reference_form(self) -> None:
        cfg = Config({"DOWNLOAD_DIR": "/mnt/media", "STATE_DIR": "%%DOWNLOAD_DIR"})
        self.assertEqual(cfg.STATE_DIR, "/mnt/media")

    def test_output_template_is_not_treated_as_reference(self) -> None:
        """`%(id)s.%(ext)s` yt-dlp-র টেমপ্লেট — ইনডিরেকশন রেজেক্সে ধরা পড়া যাবে না।"""
        cfg = Config({})
        self.assertEqual(cfg.OUTPUT_TEMPLATE, "%(id)s.%(ext)s")

    def test_host_wildcard_becomes_empty(self) -> None:
        """getaddrinfo '*' বোঝে না — '' মানে দুই ফ্যামিলিতেই শোনা।"""
        self.assertEqual(Config({"HOST": "*"}).HOST, "")
        self.assertEqual(Config({}).HOST, "0.0.0.0")

    def test_port_is_int(self) -> None:
        self.assertEqual(Config({"PORT": "9090"}).PORT, 9090)
        with self.assertRaises(SystemExit):
            Config({"PORT": "abc"})

    def test_trailing_slash_is_stripped(self) -> None:
        cfg = Config({"DOWNLOAD_DIR": "/data/", "STATE_DIR": "/data/state/"})
        self.assertEqual(cfg.DOWNLOAD_DIR, "/data")
        self.assertEqual(cfg.STATE_DIR, "/data/state")

    def test_boolean_validation_is_strict(self) -> None:
        self.assertTrue(Config({"DEMO": "true"}).DEMO)
        self.assertFalse(Config({"DEMO": "off"}).DEMO)
        with self.assertRaises(SystemExit):
            Config({"DEMO": "yes"})

    def test_allowed_hosts_list(self) -> None:
        cfg = Config({"ALLOWED_HOSTS": "youtube.com, youtu.be , googlevideo.com"})
        self.assertEqual(cfg.ALLOWED_HOSTS_LIST, ["youtube.com", "youtu.be", "googlevideo.com"])

    def test_max_concurrent_floor(self) -> None:
        self.assertEqual(Config({"MAX_CONCURRENT_DOWNLOADS": "0"}).MAX_CONCURRENT_DOWNLOADS, 1)
        self.assertEqual(Config({"MAX_CONCURRENT_DOWNLOADS": "5"}).MAX_CONCURRENT_DOWNLOADS, 5)

    def test_url_prefix_normalised(self) -> None:
        self.assertEqual(Config({"URL_PREFIX": "/tube"}).URL_PREFIX, "/tube/")
        self.assertEqual(Config({}).URL_PREFIX, "")

    def test_as_dict_hides_secrets(self) -> None:
        cfg = Config({"COOKIES": "/secret/cookies.txt", "PO_TOKEN": "xyz"})
        data = cfg.as_dict()
        self.assertNotIn("COOKIES", data)
        self.assertNotIn("PO_TOKEN", data)
        self.assertIn("DOWNLOAD_DIR", data)

    def test_ensure_dirs_creates_tree(self) -> None:
        import shutil
        import tempfile
        root = tempfile.mkdtemp(prefix="kaitube-cfg-")
        try:
            cfg = Config({"DOWNLOAD_DIR": os.path.join(root, "dl"), "DEMO": "true"})
            cfg.ensure_dirs()
            for attr in ("DOWNLOAD_DIR", "TEMP_DIR", "STATE_DIR"):
                self.assertTrue(os.path.isdir(getattr(cfg, attr)), attr)
        finally:
            shutil.rmtree(root, ignore_errors=True)


if __name__ == "__main__":
    unittest.main(verbosity=2)
