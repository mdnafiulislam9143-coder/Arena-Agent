# -*- coding: utf-8 -*-
"""
url_guard টেস্ট — MeTube-এর app/tests/test_url_guard.py-র ধাঁচে (IP-লিটারেল ব্যবহার করা হয়েছে
যাতে DNS না থাকলেও টেস্ট deterministic থাকে)।
"""
from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "server"))

import url_guard  # noqa: E402


class AddressPolicyTest(unittest.TestCase):
    def test_global_addresses_allowed(self) -> None:
        for addr in ("8.8.8.8", "1.1.1.1", "2606:4700:4700::1111"):
            self.assertTrue(url_guard.address_is_global(addr), addr)

    def test_internal_addresses_blocked(self) -> None:
        for addr in ("127.0.0.1", "10.0.0.5", "192.168.1.1", "172.16.9.9", "169.254.169.254",
                     "::1", "fd00::1", "fe80::1"):
            self.assertFalse(url_guard.address_is_global(addr), addr)

    def test_tunnelled_ipv4_is_unwrapped(self) -> None:
        """NAT64/6to4/IPv4-mapped দিয়ে ক্লাউড মেটাডেটায় ঢোকার চেষ্টা ব্লক হতে হবে।"""
        for addr in ("::ffff:169.254.169.254", "64:ff9b::a9fe:a9fe", "2002:a9fe:a9fe::",
                     "::169.254.169.254"):
            self.assertFalse(url_guard.address_is_global(addr), addr)

    def test_invalid_literal(self) -> None:
        self.assertFalse(url_guard.address_is_global("not-an-ip"))


class ValidateUrlTest(unittest.TestCase):
    def test_blocks_loopback_and_private(self) -> None:
        for url in ("http://127.0.0.1:8080/api/health", "http://10.0.0.1/", "https://192.168.0.1/x",
                    "http://[::1]/", "http://localhost/x", "http://sub.localhost/x",
                    "http://metadata.google.internal/computeMetadata/v1/"):
            self.assertIsNotNone(url_guard.validate_url(url), url)

    def test_blocks_cloud_metadata_via_nat64(self) -> None:
        error = url_guard.validate_url("http://[64:ff9b::a9fe:a9fe]/latest/meta-data/")
        self.assertIsNotNone(error)

    def test_blocks_non_http_schemes(self) -> None:
        self.assertIsNotNone(url_guard.validate_url("file:///etc/passwd"))
        self.assertIsNotNone(url_guard.validate_url("gopher://8.8.8.8/"))

    def test_allows_bare_ids_and_extractor_prefixes(self) -> None:
        """`://` না থাকলে yt-dlp-র নিজস্ব ইনপুট (ভিডিও ID, ytsearch:) — হস্তক্ষেপ নয়।"""
        self.assertIsNone(url_guard.validate_url("dQw4w9WgXcQ"))
        self.assertIsNone(url_guard.validate_url("ytsearch:bangla gaan"))
        self.assertIsNone(url_guard.validate_url(""))

    def test_allows_global_ip_literal(self) -> None:
        self.assertIsNone(url_guard.validate_url("https://8.8.8.8/video.mp4"))

    def test_allow_private_bypasses_address_checks(self) -> None:
        """Fake-IP/প্রোক্সি সেটআপের জন্য ALLOW_PRIVATE_ADDRESSES=true → ছাড়।"""
        self.assertIsNone(url_guard.validate_url("http://192.168.1.10/x", allow_private=True))
        self.assertIsNotNone(url_guard.validate_url("file:///etc/passwd", allow_private=True))

    def test_host_allowlist_restricts(self) -> None:
        error = url_guard.validate_url("https://8.8.8.8/v", allowed_hosts=["youtube.com"])
        self.assertIsNotNone(error)
        self.assertIn("allowlist", error)
        self.assertIsNone(url_guard.validate_url("https://8.8.8.8/v", allowed_hosts=["8.8.8.8"]))

    def test_unresolvable_host_fails_closed(self) -> None:
        error = url_guard.validate_url("https://this-host-does-not-exist.invalid/x")
        self.assertIsNotNone(error)

    def test_guard_report(self) -> None:
        report = url_guard.guard_report("http://169.254.169.254/")
        self.assertFalse(report["allowed"])
        self.assertIn("169.254.169.254", report["error"])


class EndpointTest(unittest.TestCase):
    def test_endpoint_parsing(self) -> None:
        self.assertEqual(url_guard.endpoint_of("http://127.0.0.1:9050"), ("127.0.0.1", 9050))
        self.assertEqual(url_guard.endpoint_of("https://proxy.example.com"), ("proxy.example.com", 443))
        self.assertEqual(url_guard.endpoint_of("socks5://127.0.0.1:1080"), ("127.0.0.1", 1080))
        self.assertIsNone(url_guard.endpoint_of(""))


if __name__ == "__main__":
    unittest.main(verbosity=2)
