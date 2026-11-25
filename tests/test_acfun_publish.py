import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

SCRIPTS = Path(__file__).resolve().parents[1] / "skills/acfun-publisher/scripts"
sys.path.insert(0, str(SCRIPTS))

import acfun_article
import acfun_video
import inbox

PRIMARY = "https://source.example/post/1"
MIRROR = "https://mirror.example/post/1"


class FakeClient:
    """Records what would be submitted, without touching AcFun."""

    def __init__(self):
        self.cookie = "acPasstoken=fake; auth_key=123"
        self.uploads = []
        self.posts = []

    def find_contribution(self, kind, *, title=None, acid=None):
        return None

    def article_category(self, name):
        return "110", "28"

    def video_category(self, category):
        return "70"

    def upload_image(self, path, *, biz_flag="web-article-text"):
        self.uploads.append((Path(path).name, biz_flag))
        return f"https://imgs.aixifan.com/{Path(path).name}"

    def upload_video(self, path):
        return 9000 + len(self.uploads)

    def post(self, path, *, data=None, json_data=None, files=None, referer="/upload-video", expected=0):
        self.posts.append((path, data))
        if path == "/article/api/postArticle":
            return {"articleId": 48999001}
        if path == "/video/api/createDouga":
            return {"dougaId": 48999002}
        return {"result": 0}

    def public_url(self, kind, acid):
        return f"https://www.acfun.cn/a/ac{acid}"


class PublishGateTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.data = Path(self.directory.name)
        self.image = self.data / "src/one.jpg"
        self.image.parent.mkdir(parents=True)
        self.image.write_bytes(b"image")
        self.draft = self.data / "draft.txt"
        self.draft.write_text("标题\n正文\n[[IMAGE_1]]\n", encoding="utf-8")

    def register(self, **extra):
        return inbox.register(self.data, "asset-1", PRIMARY, media_files=[str(self.image)], **extra)

    def article_args(self, **extra):
        base = dict(draft=self.draft, image=[self.image], image_source=PRIMARY,
                    post_id="asset-1", description="摘要", tags=["科技"], category="新闻资讯",
                    creation_type=3)
        base.update(extra)
        return SimpleNamespace(**base)

    def publish_article(self, args):
        client = FakeClient()
        with patch.object(acfun_article, "INBOX", self.data / "pending"), \
                patch.object(acfun_article, "verify_saved_images", return_value={}):
            return acfun_article.publish(client, args), client

    def test_primary_source_publishes(self):
        self.register()
        result, client = self.publish_article(self.article_args())
        self.assertEqual(result["acid"], "48999001")
        self.assertIn("/article/api/postArticle", [path for path, _ in client.posts])

    def test_registered_mirror_is_accepted(self):
        self.register(source_urls=[MIRROR])
        result, _ = self.publish_article(self.article_args(image_source=MIRROR))
        self.assertEqual(result["acid"], "48999001")

    def test_unregistered_source_is_rejected(self):
        self.register(source_urls=[MIRROR])
        with self.assertRaisesRegex(ValueError, "来源"):
            self.publish_article(self.article_args(image_source="https://evil.example/x"))

    def test_image_outside_registered_assets_is_rejected(self):
        self.register()
        stray = self.data / "src/stray.jpg"
        stray.write_bytes(b"image")
        with self.assertRaisesRegex(ValueError, "采集到的图片"):
            self.publish_article(self.article_args(image=[stray]))

    def test_video_source_must_be_registered(self):
        video = self.data / "src/clip.mp4"
        video.write_bytes(b"mp4")
        inbox.register(self.data, "vid-1", PRIMARY, media_files=[str(self.image)],
                       video_files=[str(video)])
        with patch.object(acfun_video, "INBOX", self.data / "pending"):
            self.assertEqual(acfun_video.pending_item("vid-1", PRIMARY)[0],
                             self.data / "pending/vid-1.json")
            with self.assertRaisesRegex(ValueError, "来源"):
                acfun_video.pending_item("vid-1", "https://evil.example/x")

    def test_submitted_record_is_never_published_twice(self):
        item = self.register()
        item["submission"] = {"acid": "48998000"}
        inbox.write_json(self.data / "pending/asset-1.json", item)
        with self.assertRaisesRegex(ValueError, "重复投稿"):
            self.publish_article(self.article_args())

    def test_video_record_cannot_publish_as_article(self):
        video = self.data / "src/clip.mp4"
        video.write_bytes(b"mp4")
        inbox.register(self.data, "vid-1", PRIMARY, media_files=[str(self.image)],
                       video_files=[str(video)])
        with patch.object(acfun_article, "INBOX", self.data / "pending"):
            with self.assertRaisesRegex(ValueError, "视频稿"):
                acfun_article.pending_item("vid-1")


if __name__ == "__main__":
    unittest.main()
