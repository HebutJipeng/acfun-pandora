import json
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "skills/acfun-publisher/scripts"
sys.path.insert(0, str(SCRIPTS))

import inbox


def write(path, data=b"x"):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


class RegisterTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.data = Path(self.directory.name)

    def test_register_writes_reusable_pending_record(self):
        image = write(self.data / "src/one.jpg")
        result = inbox.register(self.data, "my-asset-1", "https://example.com/post/1",
                                title_hint="原标题", media_files=[str(image)],
                                source_urls=["https://example.com/mirror/1"])
        record = json.loads((self.data / "pending/my-asset-1.json").read_text())
        self.assertEqual(result["id"], "my-asset-1")
        self.assertEqual(record["url"], "https://example.com/post/1")
        self.assertEqual(record["source_urls"], ["https://example.com/post/1",
                                                 "https://example.com/mirror/1"])
        self.assertEqual(record["media_files"], [str(image.resolve())])
        self.assertEqual(record["video_files"], [])
        self.assertFalse(record["has_video"])
        self.assertEqual(record["title_hint"], "原标题")
        self.assertIsNone(record["submission"])
        self.assertTrue(record["observed_at"])

    def test_video_assets_force_video_record(self):
        video = write(self.data / "src/a.mp4")
        record = inbox.register(self.data, "7", "https://example.com/v", video_files=[str(video)])
        self.assertTrue(record["has_video"])
        self.assertEqual(record["video_files"], [str(video.resolve())])

    def test_register_rejects_bad_identity_and_url(self):
        image = write(self.data / "src/one.jpg")
        cases = [
            ({"item_id": "", "url": "https://e.com/1"}, "ID"),
            ({"item_id": "has space", "url": "https://e.com/1"}, "ID"),
            ({"item_id": "a/b", "url": "https://e.com/1"}, "ID"),
            ({"item_id": "x" * 65, "url": "https://e.com/1"}, "ID"),
            ({"item_id": "1", "url": "http://e.com/1"}, "HTTPS"),
            ({"item_id": "1", "url": "https:///no-host"}, "HTTPS"),
            ({"item_id": "1", "url": "https://e.com/1", "media_files": ["/nope/missing.jpg"]}, "不存在"),
            ({"item_id": "1", "url": "https://e.com/1", "video_files": [str(image)]}, "MP4"),
            ({"item_id": "1", "url": "https://e.com/1", "source_urls": ["ftp://e.com/2"]}, "HTTPS"),
        ]
        for kwargs, message in cases:
            with self.subTest(kwargs=kwargs), self.assertRaisesRegex(ValueError, message):
                inbox.register(self.data, **kwargs)

    def test_register_refuses_to_overwrite_existing_state(self):
        image = write(self.data / "src/one.jpg")
        inbox.register(self.data, "dup", "https://e.com/1", media_files=[str(image)])
        with self.assertRaisesRegex(ValueError, "已存在"):
            inbox.register(self.data, "dup", "https://e.com/2", media_files=[str(image)])
        write(self.data / "published/p2.json", b"{}")
        with self.assertRaisesRegex(ValueError, "已存在"):
            inbox.register(self.data, "p2", "https://e.com/2", media_files=[str(image)])

    def test_registered_id_survives_the_full_state_machine(self):
        image = write(self.data / "src/one.jpg")
        inbox.register(self.data, "generic-1", "https://e.com/1", media_files=[str(image)])
        inbox.mark_submitted(self.data, "generic-1", "48999001", kind="article")
        record = json.loads((self.data / "pending/generic-1.json").read_text())
        self.assertEqual(record["submission"]["acid"], "48999001")
        inbox.mark_published(self.data, "generic-1", "https://www.acfun.cn/a/ac48999001")
        moved = json.loads((self.data / "published/generic-1.json").read_text())
        self.assertEqual(moved["acfun_url"], "https://www.acfun.cn/a/ac48999001")
        self.assertFalse((self.data / "pending/generic-1.json").exists())


class SourceMatchTests(unittest.TestCase):
    def test_primary_and_alternate_sources_are_accepted(self):
        item = {"url": "https://a.example/1", "source_urls": ["https://b.example/2"]}
        self.assertTrue(inbox.source_matches(item, "https://a.example/1"))
        self.assertTrue(inbox.source_matches(item, "https://b.example/2"))
        self.assertFalse(inbox.source_matches(item, "https://c.example/3"))
        self.assertFalse(inbox.source_matches(item, None))
        self.assertTrue(inbox.source_matches({"url": "https://a.example/1"}, "https://a.example/1"))

    def test_malformed_source_urls_never_match(self):
        for value in ("https://b.example/2", 3, ["ftp://b.example/2", None]):
            with self.subTest(value=value):
                self.assertFalse(inbox.source_matches({"url": "https://a.example/1",
                                                       "source_urls": value}, "https://b.example/2"))


if __name__ == "__main__":
    unittest.main()
