# -*- coding: utf-8 -*-
"""KaiOS ফরম্যাট-সিলেকশন ও Range লজিকের ইউনিট টেস্ট (stdlib unittest, নেটওয়ার্ক লাগে না)।"""
from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "server"))

import formats  # noqa: E402
import innertube  # noqa: E402
import clients  # noqa: E402


class PickForKaiosTest(unittest.TestCase):
    def setUp(self) -> None:
        self.data = innertube.demo_player_response("dQw4w9WgXcQ", height=360)["streamingData"]

    def test_picks_progressive_itag18_first(self) -> None:
        pick = formats.pick_for_kaios(self.data, max_height=360)
        self.assertEqual(pick["mode"], "progressive")
        self.assertEqual(pick["format"]["itag"], 18)
        self.assertEqual(pick["mime"], "video/mp4")
        self.assertTrue(pick["reason"])

    def test_uses_720_when_only_muxed_option(self) -> None:
        """শুধু itag 22 (720p muxed) থাকলে সেটাই বাছা হয় — ৩৬০p জোর করে নামায় না।"""
        data = {"formats": [{"itag": 22, "mimeType": 'video/mp4; codecs="avc1.64001F, mp4a.40.2"',
                             "height": 720, "audioQuality": "AUDIO_QUALITY_MEDIUM", "bitrate": 1_300_000}],
                "adaptiveFormats": []}
        pick = formats.pick_for_kaios(data, max_height=720)
        self.assertEqual(pick["mode"], "progressive")
        self.assertEqual(pick["format"]["itag"], 22)
        self.assertEqual(pick["height"], 720)

    def test_adaptive_only_path(self) -> None:
        """MSE না থাকলে adaptive = সার্ভারে mux দরকার — এটা clear করে জানানো হয়।"""
        data = {"formats": [], "adaptiveFormats": [
            {"itag": 137, "mimeType": 'video/mp4; codecs="avc1.640028"', "height": 1080, "bitrate": 2_000_000},
            {"itag": 140, "mimeType": 'audio/mp4; codecs="mp4a.40.2"', "bitrate": 128_000},
        ]}
        pick = formats.pick_for_kaios(data, max_height=360)
        self.assertEqual(pick["mode"], "adaptive")
        self.assertIsNotNone(pick["audio"])
        self.assertIn("ffmpeg", pick["reason"])

    def test_vp9_rejected_when_h264_available(self) -> None:
        data = {"formats": [], "adaptiveFormats": [
            {"itag": 247, "mimeType": 'video/webm; codecs="vp9"', "height": 720, "bitrate": 900_000},
            {"itag": 136, "mimeType": 'video/mp4; codecs="avc1.4d401f"', "height": 720, "bitrate": 1_100_000},
            {"itag": 140, "mimeType": 'audio/mp4; codecs="mp4a.40.2"', "bitrate": 128_000},
        ]}
        pick = formats.pick_for_kaios(data, max_height=720)
        self.assertEqual(pick["mode"], "adaptive")
        self.assertIn("avc1", pick["format"]["mimeType"])
        self.assertNotIn("vp9", pick["format"]["mimeType"])

    def test_empty_streaming_data(self) -> None:
        pick = formats.pick_for_kaios({}, max_height=360)
        self.assertEqual(pick["mode"], "none")
        self.assertIn("yt-dlp", pick["reason"])

    def test_high_bitrate_penalised(self) -> None:
        data = {"formats": [
            {"itag": 22, "mimeType": 'video/mp4; codecs="avc1.64001F, mp4a.40.2"', "height": 720,
             "audioQuality": "AUDIO_QUALITY_MEDIUM", "bitrate": 3_000_000},
            {"itag": 18, "mimeType": 'video/mp4; codecs="avc1.42001E, mp4a.40.2"', "height": 360,
             "audioQuality": "AUDIO_QUALITY_LOW", "bitrate": 500_000},
        ], "adaptiveFormats": []}
        pick = formats.pick_for_kaios(data, max_height=720, max_kbps=1500)
        self.assertEqual(pick["format"]["itag"], 18)   # 3 Mbps ডিভাইসে বাফার করবে


class RangeTest(unittest.TestCase):
    def test_standard(self) -> None:
        self.assertEqual(formats.parse_range("bytes=0-1023", 4096), (0, 1023))

    def test_open_ended(self) -> None:
        self.assertEqual(formats.parse_range("bytes=2048-", 4096), (2048, None))
        self.assertEqual(formats.resolve_range("bytes=2048-", 4096), (206, 2048, 4095))
        self.assertEqual(formats.resolve_range("bytes=0-", 4096), (206, 0, 4095))

    def test_out_of_range_is_416(self) -> None:
        self.assertEqual(formats.resolve_range("bytes=8192-", 4096), (416, 8192, 4095))

    def test_clamped_end(self) -> None:
        self.assertEqual(formats.resolve_range("bytes=0-999999", 4096), (206, 0, 4095))

    def test_no_range_is_full(self) -> None:
        self.assertEqual(formats.resolve_range(None, 4096), (200, 0, 4095))

    def test_suffix(self) -> None:
        self.assertEqual(formats.parse_range("bytes=-500", 4096), (3596, None))

    def test_garbage(self) -> None:
        self.assertIsNone(formats.parse_range("items=1-2", 4096))
        self.assertIsNone(formats.parse_range(None, 4096))
        self.assertIsNone(formats.parse_range("bytes=abc-def", 4096))

    def test_proxy_headers_keep_range(self) -> None:
        out = formats.proxy_headers(206, {"Content-Range": "bytes 0-1023/4096",
                                          "Content-Type": "video/mp4", "Content-Length": "1024"})
        self.assertEqual(out["Accept-Ranges"], "bytes")
        self.assertEqual(out["Content-Range"], "bytes 0-1023/4096")
        self.assertEqual(out["Cache-Control"], "no-store")


class ClientsTest(unittest.TestCase):
    def test_chain_order_and_ids(self) -> None:
        chain = clients.load_chain()
        names = [c["name"] for c in chain]
        self.assertEqual(names[0], "ANDROID_VR")          # বিনা টোকেনে সেরা
        for c in chain:
            self.assertIn(c["clientName"], clients.CLIENT_IDS)

    def test_every_client_has_version(self) -> None:
        for c in clients.load_chain():
            self.assertTrue(c.get("clientVersion"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
